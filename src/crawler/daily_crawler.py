"""
Daily Incremental Sync Engine: Keeps the SQLite Database Always Up-To-Date (Pure HTTPX)
"""

import asyncio
import random
import time
from typing import List, Set, Dict, Any, Optional
import httpx
from rich.table import Table
from rich.panel import Panel

from ..config import Settings, CONFIG
from ..core.proxy_manager import get_proxy_manager
from ..core.rate_limiter import SharedCrawlState
from ..core.retry_manager import PostRetryWorker
from ..database.models import CrawlLog
from ..database.repository import NovelRepository
from ..parsers.feed_parser import FeedParser, UpdatedFeedItem
from .media_crawler import MediaCrawler
from .novel_crawler import NovelCrawler
from ..utils.exporter import NovelExporter
from ..utils.logger import get_logger, console

log = get_logger("daily_crawler")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]


class DailySyncEngine:
    """
    Automated daily sync engine that polls the 'Mới cập nhật' feed,
    detects new novels and new chapters, and incrementally synchronizes the database.
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or CONFIG
        self.repository = NovelRepository()
        self.proxy_manager = get_proxy_manager()
        self.media_crawler = MediaCrawler(self.settings.media, self.repository, self.proxy_manager)
        self.state = SharedCrawlState(self.settings.crawler)
        self.novel_crawler = NovelCrawler(
            self.settings.crawler,
            self.repository,
            self.media_crawler,
            self.state,
            proxy_manager=self.proxy_manager,
            settings=self.settings,
        )
        self.post_retry_worker = PostRetryWorker(self.repository)
        self.exporter = NovelExporter(self.repository, self.settings.app.output_dir / "novels")

    def _get_headers(self, referer: Optional[str] = None) -> Dict[str, str]:
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": referer or self.settings.crawler.base_url,
        }

    async def fetch_feed_page(self, page_num: int) -> List[UpdatedFeedItem]:
        """Fetches a single page of the configured daily update feed using HTTPX."""
        template = getattr(self.settings.daily, "feed_url_template", "")
        if template and "{page}" in template:
            feed_url = template.format(page=page_num)
        elif template and "{i}" in template:
            feed_url = template.format(i=page_num)
        elif template:
            feed_url = f"{template}&page={page_num}"
        else:
            feed_url = f"{self.settings.crawler.base_url}/the-loai/slice-of-life?truyendich=1&sangtac=1&convert=1&dangtienhanh=1&tamngung=1&hoanthanh=1&sapxep=capnhat&page={page_num}"

        log.info(f"[DailyFeed] Scanning page {page_num}: {feed_url}")

        for attempt in range(1, 4):
            await self.state.proceed.wait()
            await self.state.rate_limiter.acquire()

            proxy_url = self.proxy_manager.get_current_proxy()

            try:
                async with httpx.AsyncClient(
                    proxy=proxy_url if proxy_url else None,
                    timeout=20.0,
                    headers=self._get_headers(),
                    follow_redirects=True,
                ) as client:
                    resp = await client.get(feed_url)

                    if resp.status_code == 200:
                        self.state.rate_limiter.notify_success()
                        items = FeedParser.parse_latest_updates(resp.text, self.settings.crawler.base_url)
                        log.info(f"[DailyFeed] Page {page_num} found {len(items)} updated items.")
                        return items

                    if resp.status_code in (429, 502, 503, 504):
                        await self.state.rate_limiter.on_429()
                        if self.proxy_manager.is_enabled:
                            self.proxy_manager.rotate_proxy()
                        else:
                            await self.state.trigger_backoff(30.0)
                    else:
                        await asyncio.sleep(2.0)

            except Exception as e:
                log.warning(f"[DailyFeed] Failed to scan page {page_num}: {e}")
                if self.proxy_manager.is_enabled:
                    self.proxy_manager.rotate_proxy()
                await asyncio.sleep(2.0)

        return []

    async def run_sync(self, max_pages: Optional[int] = None, force_all: bool = False) -> Dict[str, Any]:
        """Executes a complete daily sync cycle."""
        pages_to_check = max_pages or self.settings.daily.latest_updates_max_pages
        start_time = time.monotonic()

        log_id = await self.repository.create_crawl_log(crawl_type="daily")
        log.info(f"[DailySync] ★ Starting Daily Sync cycle (Scan up to {pages_to_check} pages) ★")

        watcher_task = asyncio.create_task(self.state.backoff_watcher())

        all_feed_items: List[UpdatedFeedItem] = []
        seen_novel_urls: Set[str] = set()

        items_checked = 0
        items_updated = 0
        total_new_chapters = 0
        errors_count = 0
        updated_novel_ids = []

        try:
            # 1. Discover all updated novels across feed pages
            for p in range(1, pages_to_check + 1):
                feed_items = await self.fetch_feed_page(p)
                if not feed_items:
                    break
                for item in feed_items:
                    if item.novel_url not in seen_novel_urls:
                        seen_novel_urls.add(item.novel_url)
                        all_feed_items.append(item)

                await asyncio.sleep(self.state.get_random_page_delay())

            items_checked = len(all_feed_items)
            log.info(f"[DailySync] Total unique updated novels discovered: [bold cyan]{items_checked}[/bold cyan]")

            # 2. Check each novel against SQLite database
            db_chapter_urls = await self.repository.get_all_chapter_urls_in_db()

            novels_to_crawl = []
            for item in all_feed_items:
                existing_novel = await self.repository.get_novel_by_url(item.novel_url)
                if not existing_novel:
                    # Brand new novel -> full crawl needed
                    log.info(f"[DailySync] [NEW NOVEL] '{item.novel_title}' not in DB -> Queueing full crawl.")
                    novels_to_crawl.append(item.novel_url)
                elif force_all or (item.latest_chapter_url and item.latest_chapter_url not in db_chapter_urls):
                    # Existing novel has new chapter
                    log.info(f"[DailySync] [NEW CHAPTER] '{item.novel_title}' has update: {item.latest_chapter_title}")
                    novels_to_crawl.append(item.novel_url)
                else:
                    log.debug(f"[DailySync] '{item.novel_title}' already up to date.")

            log.info(f"[DailySync] Novels requiring update: [bold yellow]{len(novels_to_crawl)}/{items_checked}[/bold yellow]")

            # 3. Crawl updated novels
            for idx, novel_url in enumerate(novels_to_crawl, 1):
                try:
                    log.info(f"[DailySync] ({idx}/{len(novels_to_crawl)}) Syncing: {novel_url}")
                    novel_id, total_ch, new_ch = await self.novel_crawler.crawl_novel(
                        novel_url=novel_url,
                        force_recrawl=False,
                    )
                    if novel_id > 0:
                        items_updated += 1
                        total_new_chapters += new_ch
                        updated_novel_ids.append(novel_id)
                    else:
                        errors_count += 1
                except Exception as e:
                    errors_count += 1
                    log.error(f"[DailySync] Error syncing novel {novel_url}: {e}")

                await asyncio.sleep(self.state.get_random_page_delay())

        finally:
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass

        # 4. Post-Retry Phase for any failed chapters or images
        if self.settings.daily.auto_retry_failed:
            log.info("[DailySync] Running Post-Retry phase on pending retry queue...")
            from .engine import CrawlerEngine
            engine_helper = CrawlerEngine(self.settings)
            resolved = await self.post_retry_worker.process_pending_retries(engine_helper)
            log.info(f"[DailySync] Post-retry resolved {resolved} items.")

        duration = time.monotonic() - start_time
        status_str = "success" if errors_count == 0 else "partial"

        # Update crawl log
        log_model = CrawlLog(
            id=log_id,
            crawl_type="daily",
            status=status_str,
            items_checked=items_checked,
            items_updated=items_updated,
            new_chapters=total_new_chapters,
            errors_count=errors_count,
            duration_seconds=duration,
            details={"pages_scanned": pages_to_check, "updated_novels_count": len(novels_to_crawl)},
        )
        await self.repository.update_crawl_log(log_model)

        # 5. Summary Table
        table = Table(title="Daily Sync Report", border_style="cyan", show_header=True)
        table.add_column("Metric", style="bold cyan")
        table.add_column("Value", style="bold green")

        table.add_row("Feed Pages Scanned", str(pages_to_check))
        table.add_row("Novels Checked", str(items_checked))
        table.add_row("Novels Synchronized", str(items_updated))
        table.add_row("New Chapters Downloaded", str(total_new_chapters))
        table.add_row("Errors Encountered", f"[red]{errors_count}[/red]" if errors_count > 0 else "0")
        table.add_row("Execution Duration", f"{duration:.1f}s")
        table.add_row("Overall Status", f"[bold green]{status_str.upper()}[/bold green]")

        console.print(table)
        return {
            "items_checked": items_checked,
            "items_updated": items_updated,
            "new_chapters": total_new_chapters,
            "errors": errors_count,
            "duration": duration,
        }
