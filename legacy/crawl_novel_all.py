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

    num_workers:         int   = 4      # Số worker đồng thời
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
    • Bị 429 → giảm rate xuống 50%
    • Chạy ổn định ≥ RECOVER_AFTER giây → tăng rate lên 10% (tối đa max_rps)
    """

    RECOVER_AFTER = 120.0   # giây ổn định trước khi tăng tốc lại
    MIN_RPS       = 0.3     # sàn tốc độ (1 req / ~3s)
    STEP_UP       = 1.10    # nhân 1.10 mỗi lần phục hồi
    STEP_DOWN     = 0.50    # nhân 0.50 mỗi lần bị 429

    def __init__(self, initial_rate: float):
        self._max_rate        = initial_rate
        self._rate            = initial_rate
        self._tokens          = initial_rate
        self._updated         = time.monotonic()
        self._last_429        = 0.0
        self._consecutive_429 = 0
        self._lock            = Lock()

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
            self._tokens = min(self._tokens, new_rate)

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
                    self._rate            = new_rate
                    self._consecutive_429 = 0

    async def acquire(self):
        """
        FIX Bug 1: Bỏ `async with` + release thủ công bên trong.
        Dùng acquire/release tường minh + sleep NGOÀI lock để không chặn
        các worker khác trong thời gian chờ.
        """
        await self._lock.acquire()
        try:
            now           = time.monotonic()
            elapsed       = now - self._updated
            self._tokens  = min(self._rate, self._tokens + elapsed * self._rate)
            self._updated = now

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return          # fast path — trả lock ngay

            # Tính thời gian cần chờ rồi trả lock TRƯỚC KHI sleep
            wait         = (1.0 - self._tokens) / self._rate
            self._tokens = 0.0
        finally:
            self._lock.release()

        # Sleep hoàn toàn ngoài lock — worker khác vẫn acquire được
        await asyncio.sleep(wait)


# ─────────────────────────────────────────────
#  TRẠNG THÁI CHIA SẺ GIỮA CÁC WORKER
# ─────────────────────────────────────────────
@dataclass
class SharedState:
    done_urls:       set  = field(default_factory=set)
    done_lock:       Lock = field(default_factory=Lock)
    rate_limiter:    AdaptiveRateLimiter = field(
                         default_factory=lambda: AdaptiveRateLimiter(CFG.max_rps)
                     )

    # FIX Bug 2: thay thế pattern clear/sleep/set phân tán bằng
    # timestamp tập trung + watcher task độc lập.
    # proceed.set()   = bình thường (proceed)
    # proceed.clear() = tạm dừng (wait)
    proceed:         Event = field(default_factory=Event)
    _backoff_lock:   Lock  = field(default_factory=Lock)
    _backoff_until:  float = 0.0      # monotonic timestamp hết hạn backoff

    completed_count: int   = 0

    def __post_init__(self):
        self.proceed.set()            # Mặc định cho phép chạy

    # ── Gọi từ bất kỳ worker nào khi nhận 429 ──
    async def trigger_backoff(self, duration: float):
        """
        Chỉ kéo dài backoff, không bao giờ rút ngắn.
        Worker đầu tiên đặt deadline, các worker sau extend nếu cần.
        """
        async with self._backoff_lock:
            target = time.monotonic() + duration
            if target > self._backoff_until:
                self._backoff_until = target
                self.proceed.clear()
                log.warning(
                    f"[Backoff] Dừng toàn bộ worker {duration:.0f}s "
                    f"(hết lúc +{duration:.0f}s)"
                )

    # ── Chạy như asyncio.Task suốt vòng đời crawler ──
    async def backoff_watcher(self):
        """
        Polling nhẹ (1s) — set proceed khi đã qua _backoff_until.
        Tách biệt hoàn toàn với logic worker, không có race condition.
        """
        while True:
            await asyncio.sleep(1.0)
            if not self.proceed.is_set():
                async with self._backoff_lock:
                    if time.monotonic() >= self._backoff_until:
                        self.proceed.set()
                        log.info("[Backoff] Hết thời gian chờ — tiếp tục crawl")


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


# FIX Opt 3: save_progress không block event loop
async def save_progress_async(done: set):
    data = json.dumps(list(done), ensure_ascii=False, indent=2)
    await asyncio.to_thread(
        Path(CFG.progress_file).write_text, data, "utf-8"
    )


# FIX Opt 4: chặn resource bằng regex — không cần Python callback mỗi request
_BLOCK_RE = re.compile(
    r"\.(png|jpe?g|gif|webp|svg|ico|woff2?|ttf|otf|eot|css|mp4|webm|avi)(\?.*)?$",
    re.IGNORECASE,
)

async def setup_page(page: Page):
    """Áp dụng resource blocker cho một page."""
    await page.route(
        _BLOCK_RE,
        lambda route, _req: route.abort()
    )


# ─────────────────────────────────────────────
#  CRAWL MỘT CHƯƠNG
# ─────────────────────────────────────────────
async def fetch_chapter(
    page: Page, ch: dict, state: SharedState
) -> tuple[str, str] | None:
    """Trả về (title, content) hoặc None nếu thất bại hết retry."""

    for attempt in range(1, CFG.max_retries + 1):
        # Chờ nếu đang trong thời gian backoff toàn cục
        await state.proceed.wait()
        await state.rate_limiter.acquire()

        try:
            resp = await page.goto(
                ch["url"], timeout=60_000, wait_until="domcontentloaded"
            )

            # ── 429: báo rate limiter + trigger backoff tập trung ──
            if resp and resp.status == 429:
                await state.rate_limiter.on_429()
                wait_s = random.uniform(CFG.backoff_429_min, CFG.backoff_429_max)
                await state.trigger_backoff(wait_s)   # FIX Bug 2
                # Không sleep ở đây — watcher sẽ set proceed khi đến lúc
                await state.proceed.wait()
                continue

            await page.locator("#chapter-content").first.wait_for(timeout=30_000)
            title   = await page.locator("h4.title-item").inner_text()
            content = await page.locator("#chapter-content").inner_text()
            await state.rate_limiter.on_success()
            return title.strip(), content.strip()

        except Exception as e:
            log.warning(
                f"[Attempt {attempt}/{CFG.max_retries}] "
                f"Lỗi chương {ch['title']}: {e}"
            )
            if attempt < CFG.max_retries:
                await asyncio.sleep(2 ** attempt + random.random())

    log.error(
        f"[SKIP] Bỏ qua chương sau {CFG.max_retries} lần thất bại: {ch['title']}"
    )
    return None


# ─────────────────────────────────────────────
#  CRAWL MỘT TRUYỆN
# ─────────────────────────────────────────────
async def crawl_novel(
    nav_page:   Page,   # page dùng để load trang novel / lấy danh sách chương
    fetch_page: Page,   # page dùng để tải từng chương (FIX Opt 6: tách page)
    novel_url:  str,
    state:      SharedState,
):
    CFG.output_dir.mkdir(exist_ok=True)

    try:
        await state.proceed.wait()
        await state.rate_limiter.acquire()

        await nav_page.goto(novel_url, timeout=60_000, wait_until="domcontentloaded")

        novel_title = await nav_page.locator("span.series-name a").inner_text()
        novel_title = novel_title.strip()
        file_path   = CFG.output_dir / f"{sanitize(novel_title)}.txt"

        if file_path.exists():
            log.info(f"[SKIP] Đã có: {novel_title}")
            async with state.done_lock:
                state.done_urls.add(novel_url)
            # FIX Bug 4: lưu progress ngay để không mất track khi restart
            await save_progress_async(state.done_urls)
            return

        # FIX Opt 5: chờ danh sách chương render xong trước khi lấy
        await nav_page.locator(
            "ul.list-chapters.at-series"
        ).wait_for(timeout=15_000)

        # FIX Opt 4: eval JS một lần thay vì N await riêng lẻ
        # → nhanh hơn 5-10x với truyện nhiều chương (500+)
        chapters: list[dict] = await nav_page.eval_on_selector_all(
            "ul.list-chapters.at-series .chapter-name a",
            """els => els.map(el => ({
                title: el.innerText.trim(),
                url:    el.getAttribute('href')
            }))"""
        )

        log.info(f"[START] {novel_title} — {len(chapters)} chương")

        tmp_path = file_path.with_suffix(".tmp")
        tmp_path.unlink(missing_ok=True)

        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(f"{novel_title.upper()}\n\n{'='*40}\n\n")

                for idx, ch in enumerate(chapters, 1):
                    result = await fetch_chapter(fetch_page, ch, state)
                    if result:
                        ch_title, content = result
                        f.write(
                            f"{ch_title.upper()}\n\n{content}\n\n{'='*40}\n\n"
                        )
                        log.info(
                            f"  [{idx:>4}/{len(chapters)}] "
                            f"{novel_title[:30]} › {ch_title[:40]}"
                        )

                    await asyncio.sleep(
                        random.uniform(CFG.chapter_delay_min, CFG.chapter_delay_max)
                    )

            tmp_path.rename(file_path)
            log.info(f"[DONE] {novel_title}")

        except (Exception, asyncio.CancelledError):
            tmp_path.unlink(missing_ok=True)
            raise

    except asyncio.CancelledError:
        raise   # không swallow CancelledError
    except Exception as e:
        log.error(f"[ERROR] Truyện {novel_url}: {e}")
        return

    # Lưu progress
    async with state.done_lock:
        state.done_urls.add(novel_url)
        state.completed_count += 1
        if state.completed_count % CFG.checkpoint_every == 0:
            await save_progress_async(state.done_urls)   # FIX Opt 3: async IO
            log.info(
                f"[CHECKPOINT] Đã lưu progress ({state.completed_count} truyện xong)"
            )


# ─────────────────────────────────────────────
#  WORKER — lấy việc từ queue
# ─────────────────────────────────────────────
async def worker(
    worker_id:  int,
    context:    BrowserContext,
    queue:      Queue,
    state:      SharedState,
):
    log.info(f"Worker {worker_id} khởi động")

    # FIX Opt 6: mỗi worker có 2 page riêng biệt
    #   nav_page   → navigate trang novel, lấy danh sách chương
    #   fetch_page → load từng chapter (không cần navigate lại trang novel)
    nav_page   = await context.new_page()
    fetch_page = await context.new_page()

    await setup_page(nav_page)    # FIX Opt 4: regex blocker, không dùng callback
    await setup_page(fetch_page)

    try:
        while True:
            try:
                novel_url = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            await crawl_novel(nav_page, fetch_page, novel_url, state)
            queue.task_done()
    finally:
        await nav_page.close()
        await fetch_page.close()

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

    queue: Queue = asyncio.Queue()
    for url in remaining:
        queue.put_nowait(url)

    async with async_playwright() as pw:
        context: BrowserContext = await pw.chromium.launch_persistent_context(
            user_data_dir=CFG.user_data_dir,
            headless=True,
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

        # FIX Bug 2: khởi động backoff_watcher như một task độc lập
        watcher_task = asyncio.create_task(state.backoff_watcher())

        try:
            await asyncio.gather(
                *[
                    worker(i + 1, context, queue, state)
                    for i in range(CFG.num_workers)
                ]
            )
        finally:
            watcher_task.cancel()       # dừng watcher khi xong
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass

            # Luôn lưu progress khi kết thúc (kể cả Ctrl+C)
            await save_progress_async(state.done_urls)
            log.info(
                f"Progress đã lưu. "
                f"Tổng hoàn thành: {len(state.done_urls)}/{len(all_urls)}"
            )
            await context.close()


if __name__ == "__main__":
    asyncio.run(main())