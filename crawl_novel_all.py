"""
Crawler tối ưu cho docln.sbs — async multi-worker, rate limiter, resume.
Cài đặt: pip install playwright && playwright install chromium
"""

import asyncio
import re
import os
import json
import random
import time
import logging
from pathlib import Path
from dataclasses import dataclass, field
from asyncio import Queue, Lock, Event
from playwright.async_api import async_playwright, BrowserContext, Page


# ─────────────────────────────────────────────
#  CẤU HÌNH — chỉnh ở đây
# ─────────────────────────────────────────────
@dataclass
class Config:
    output_dir:          Path  = Path("output") 
    url_list_file:       str   = "url_list.txt"
    progress_file:       str   = "progress.json"
    log_file:            str   = "crawler.log"
    user_data_dir:       str   = "./hako"

    num_workers:         int   = 4      # Số worker đồng thời (4 là an toàn)
    max_rps:             float = 3.0    # Giới hạn request/giây TOÀN CỤC

    # Delay ngẫu nhiên sau mỗi chương (giây)
    chapter_delay_min:   float = 0.5
    chapter_delay_max:   float = 1.5

    # Khi bị 429: toàn bộ worker dừng lại
    backoff_429_min:     float = 60.0
    backoff_429_max:     float = 120.0

    max_retries:         int   = 3
    checkpoint_every:    int   = 10     # Ghi progress sau mỗi N truyện hoàn thành

CFG = Config()


# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(CFG.log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  ADAPTIVE RATE LIMITER
# ─────────────────────────────────────────────
class AdaptiveRateLimiter:
    """
    Token bucket tự điều chỉnh tốc độ dựa trên tần suất 429.

    Logic:
    • Bị 429 → giảm rate xuống 50%, ghi nhận thời điểm
    • Chạy ổn định ≥ RECOVER_AFTER giây → tăng rate lên 10% (tối đa max_rps)
    • Nếu liên tục bị 429 (< MIN_RPS) → log cảnh báo nhưng vẫn tự chạy, không cần can thiệp thủ công
    """

    RECOVER_AFTER = 120.0   # giây ổn định trước khi tăng tốc lại
    MIN_RPS       = 0.3     # sàn tốc độ (1 req / ~3s)
    STEP_UP       = 1.10    # nhân 1.10 mỗi lần phục hồi
    STEP_DOWN     = 0.50    # nhân 0.50 mỗi lần bị 429

    def __init__(self, initial_rate: float):
        self._max_rate    = initial_rate
        self._rate        = initial_rate
        self._tokens      = initial_rate
        self._updated     = time.monotonic()
        self._last_429    = 0.0          # monotonic timestamp của lần 429 gần nhất
        self._consecutive_429 = 0
        self._lock        = Lock()

    # ── gọi từ bên ngoài khi nhận HTTP 429 ──
    async def on_429(self):
        async with self._lock:
            self._consecutive_429 += 1
            self._last_429 = time.monotonic()
            new_rate = max(self.MIN_RPS, self._rate * self.STEP_DOWN)
            log.warning(
                f"[RateLimiter] 429 #{self._consecutive_429} — "
                f"giảm tốc {self._rate:.2f} → {new_rate:.2f} req/s"
            )
            self._rate   = new_rate
            self._tokens = min(self._tokens, new_rate)   # flush tokens thừa

    # ── gọi sau mỗi request thành công ──
    async def on_success(self):
        async with self._lock:
            if self._rate >= self._max_rate:
                return
            if time.monotonic() - self._last_429 >= self.RECOVER_AFTER:
                new_rate = min(self._max_rate, self._rate * self.STEP_UP)
                if new_rate != self._rate:
                    log.info(
                        f"[RateLimiter] Phục hồi — tăng tốc {self._rate:.2f} → {new_rate:.2f} req/s"
                    )
                    self._rate = new_rate
                    self._consecutive_429 = 0

    async def acquire(self):
        async with self._lock:
            now     = time.monotonic()
            elapsed = now - self._updated
            self._tokens  = min(self._rate, self._tokens + elapsed * self._rate)
            self._updated = now

            if self._tokens >= 1.0:
                self._tokens -= 1.0
            else:
                wait = (1.0 - self._tokens) / self._rate
                # Giải phóng lock trong khi chờ để worker khác không bị block
                self._lock.release()
                await asyncio.sleep(wait)
                await self._lock.acquire()
                self._tokens = 0.0


# ─────────────────────────────────────────────
#  TRẠNG THÁI CHIA SẺ GIỮA CÁC WORKER
# ─────────────────────────────────────────────
@dataclass
class SharedState:
    done_urls:     set         = field(default_factory=set)
    done_lock:     Lock        = field(default_factory=Lock)
    rate_limiter:  AdaptiveRateLimiter = field(default_factory=lambda: AdaptiveRateLimiter(CFG.max_rps))

    # Event dùng để "tạm dừng tất cả worker" khi bị 429
    # set()   = bình thường (proceed)
    # clear() = tạm dừng (wait)
    proceed:       Event       = field(default_factory=lambda: Event())

    completed_count: int = 0

    def __post_init__(self):
        self.proceed.set()   # Mặc định là cho phép chạy


# ─────────────────────────────────────────────
#  HELPER
# ─────────────────────────────────────────────
def sanitize(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()


def load_progress() -> set:
    p = Path(CFG.progress_file)
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_progress(done: set):
    with open(CFG.progress_file, "w", encoding="utf-8") as f:
        json.dump(list(done), f, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────
#  CRAWL MỘT CHƯƠNG
# ─────────────────────────────────────────────
async def fetch_chapter(page: Page, ch: dict, state: SharedState) -> tuple[str, str] | None:
    """Trả về (title, content) hoặc None nếu thất bại."""
    for attempt in range(1, CFG.max_retries + 1):
        # Chờ nếu đang bị rate-limit toàn cục
        await state.proceed.wait()
        await state.rate_limiter.acquire()

        try:
            resp = await page.goto(ch["url"], timeout=60000, wait_until="domcontentloaded")

            # ── 429: báo rate limiter tự điều chỉnh, dừng worker, chờ ──
            if resp and resp.status == 429:
                await state.rate_limiter.on_429()
                wait_s = random.uniform(CFG.backoff_429_min, CFG.backoff_429_max)
                log.warning(f"[429] Dừng toàn bộ worker {wait_s:.0f}s …")
                state.proceed.clear()
                await asyncio.sleep(wait_s)
                state.proceed.set()
                continue   # thử lại

            await page.locator("#chapter-content").first.wait_for(timeout=30_000)
            title   = await page.locator("h4.title-item").inner_text()
            content = await page.locator("#chapter-content").inner_text()
            await state.rate_limiter.on_success()   # báo thành công → dần phục hồi tốc độ
            return title.strip(), content.strip()

        except Exception as e:
            log.warning(f"[Attempt {attempt}/{CFG.max_retries}] Lỗi chương {ch['title']}: {e}")
            if attempt < CFG.max_retries:
                await asyncio.sleep(2 ** attempt + random.random())   # exponential backoff

    log.error(f"[SKIP] Bỏ qua chương sau {CFG.max_retries} lần thất bại: {ch['title']}")
    return None


# ─────────────────────────────────────────────
#  CRAWL MỘT TRUYỆN
# ─────────────────────────────────────────────
async def crawl_novel(page: Page, novel_url: str, state: SharedState):
    CFG.output_dir.mkdir(exist_ok=True)

    try:
        await state.proceed.wait()
        await state.rate_limiter.acquire()

        await page.goto(novel_url, timeout=60000, wait_until="domcontentloaded")

        novel_title = await page.locator("span.series-name a").inner_text()
        novel_title = novel_title.strip()
        file_path   = CFG.output_dir / f"{sanitize(novel_title)}.txt"

        if file_path.exists():
            log.info(f"[SKIP] Đã có: {novel_title}")
            async with state.done_lock:
                state.done_urls.add(novel_url)
            return

        # Lấy danh sách chương
        elements = await page.locator(
            "ul.list-chapters.at-series .chapter-name a"
        ).all()
        chapters = []
        for el in elements:
            href  = await el.get_attribute("href")
            title = await el.inner_text()
            if href:
                chapters.append({
                    "title": title.strip(),
                    "url":   f"https://docln.sbs{href}",
                })

        log.info(f"[START] {novel_title} — {len(chapters)} chương")

        tmp_path = file_path.with_suffix(".tmp")
        tmp_path.unlink(missing_ok=True)   # xóa .tmp cũ nếu có

        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(f"{novel_title.upper()}\n\n{'='*40}\n\n")

                for idx, ch in enumerate(chapters, 1):
                    result = await fetch_chapter(page, ch, state)
                    if result:
                        ch_title, content = result
                        f.write(f"{ch_title.upper()}\n\n{content}\n\n{'='*40}\n\n")
                        log.info(f"  [{idx:>4}/{len(chapters)}] {novel_title[:30]} › {ch_title[:40]}")

                    await asyncio.sleep(
                        random.uniform(CFG.chapter_delay_min, CFG.chapter_delay_max)
                    )

            # Chỉ rename khi ghi xong toàn bộ
            tmp_path.rename(file_path)
            log.info(f"[DONE] {novel_title}")

        except (Exception, asyncio.CancelledError):
            # Ctrl+C hoặc lỗi → xóa .tmp, KHÔNG để lại file dở
            tmp_path.unlink(missing_ok=True)
            raise   # ném tiếp để worker xử lý

    except Exception as e:
        log.error(f"[ERROR] Truyện {novel_url}: {e}")
        return

    # Lưu progress
    async with state.done_lock:
        state.done_urls.add(novel_url)
        state.completed_count += 1
        if state.completed_count % CFG.checkpoint_every == 0:
            save_progress(state.done_urls)
            log.info(f"[CHECKPOINT] Đã lưu progress ({state.completed_count} truyện xong)")


# ─────────────────────────────────────────────
#  WORKER — lấy việc từ queue
# ─────────────────────────────────────────────
async def worker(worker_id: int, page: Page, queue: Queue, state: SharedState):
    log.info(f"Worker {worker_id} khởi động")
    while True:
        try:
            novel_url = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        await crawl_novel(page, novel_url, state)
        queue.task_done()
    log.info(f"Worker {worker_id} xong việc")


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
async def main():
    # Đọc URL list
    try:
        with open(CFG.url_list_file, encoding="utf-8") as f:
            all_urls: list[str] = json.load(f)
    except Exception as e:
        log.error(f"Không đọc được {CFG.url_list_file}: {e}")
        return

    done_urls = load_progress()

    # Lọc các URL chưa làm; shuffle để không bị pattern cố định
    remaining = [u for u in all_urls if u not in done_urls]
    random.shuffle(remaining)

    log.info(
        f"Tổng URL: {len(all_urls)} | Đã xong: {len(done_urls)} | "
        f"Còn lại: {len(remaining)} | Workers: {CFG.num_workers}"
    )

    if not remaining:
        log.info("Không còn URL nào cần crawl.")
        return

    state = SharedState(done_urls=done_urls)

    # Đưa tất cả URL vào queue
    queue: Queue = asyncio.Queue()
    for url in remaining:
        queue.put_nowait(url)

    async with async_playwright() as pw:
        context: BrowserContext = await pw.chromium.launch_persistent_context(
            user_data_dir=CFG.user_data_dir,
            headless=True,   # headless nhanh hơn ~20%
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-gpu",
            ],
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )

        # Mỗi worker có 1 page riêng
        pages = [await context.new_page() for _ in range(CFG.num_workers)]

        # Chặn tài nguyên không cần thiết (ảnh, font, media) → nhanh hơn
        async def block_resources(route):
            if route.request.resource_type in ("image", "media", "font", "stylesheet"):
                await route.abort()
            else:
                await route.continue_()

        for p in pages:
            await p.route("**/*", block_resources)

        try:
            await asyncio.gather(
                *[worker(i + 1, pages[i], queue, state) for i in range(CFG.num_workers)]
            )
        finally:
            # Luôn lưu progress khi kết thúc (kể cả Ctrl+C)
            save_progress(state.done_urls)
            log.info(
                f"Progress đã lưu. Tổng hoàn thành: {len(state.done_urls)}/{len(all_urls)}"
            )
            await context.close()


if __name__ == "__main__":
    asyncio.run(main())