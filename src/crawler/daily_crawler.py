"""
Daily Incremental Sync Engine: Keeps the SQLite Database Always Up-To-Date
"""

import asyncio
import time
from typing import List, Set, Dict, Any, Optional
from playwright.async_api import BrowserContext, Page
from ..config import Settings, CONFIG
from ..core.browser_manager import BrowserManager
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
from rich.table import Table
from rich.panel import Panel

log = get_logger("daily_crawler")


class DailySyncEngine:
    """
    Automated daily sync engine that polls the 'Mới cập nhật' feed,
    detects new novels and new chapters, and incrementally synchronizes the database.
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or CONFIG
        self.repository = NovelRepository()
        self.proxy_manager = get_proxy_manager()
        self.browser_manager = BrowserManager(self.settings.crawler, self.proxy_manager)
        self.media_crawler = MediaCrawler(self.settings.media, self.repository, self.proxy_manager)
        self.state = SharedCrawlState(self.settings.crawler)
        self.novel_crawler = NovelCrawler(
            self.settings.crawler, self.repository, self.media_crawler, self.state, settings=self.settings
        )
        self.post_retry_worker = PostRetryWorker(self.repository)
        self.exporter = NovelExporter(self.repository, self.settings.app.output_dir / "novels")

    async def fetch_feed_page(self, page: Page, page_num: int) -> List[UpdatedFeedItem]:
        """Fetches a single page of the 'Mới cập nhật' catalog."""
        feed_url = f"{self.settings.crawler.base_url}/danh-sach?sapxep=capnhat&page={page_num}"
        log.info(f"[DailyFeed] Scanning page {page_num}: {feed_url}")

        await self.state.proceed.wait()
        await self.state.rate_limiter.acquire()

        try:
            await page.goto(feed_url, timeout=self.settings.crawler.goto_timeout_ms, wait_until="domcontentloaded")
            await page.locator("div.thumb_attr.series-title a, .series-title a").first.wait_for(
                timeout=self.settings.crawler.selector_timeout_ms
            )
            html = await page.content()
            items = FeedParser.parse_latest_updates(html, self.settings.crawler.base_url)
            log.info(f"[DailyFeed] Page {page_num} found {len(items)} updated items.")
            return items
        except Exception as e:
            log.warning(f"[DailyFeed] Failed to scan page {page_num}: {e}")
            return []

    async def run_sync(self, max_pages: Optional[int] = None, force_all: bool = False) -> Dict[str, Any]:
        """
        Executes a complete daily sync cycle.
        """
        pages_to_check = max_pages or self.settings.daily.latest_updates_max_pages
        start_time = time.monotonic()

        log_id = await self.repository.create_crawl_log(crawl_type="daily")
        log.info(f"[DailySync] ★ Starting Daily Sync cycle (Scan up to {pages_to_check} pages) ★")

        context = await self.browser_manager.start()
        feed_page = await context.new_page()
        nav_page = await context.new_page()
        fetch_page = await context.new_page()

        await self.browser_manager.setup_page(feed_page)
        await self.browser_manager.setup_page(nav_page)
        await self.browser_manager.setup_page(fetch_page)

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
                feed_items = await self.fetch_feed_page(feed_page, p)
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
                        nav_page=nav_page,
                        fetch_page=fetch_page,
                        novel_url=novel_url,
                        force_recrawl=False,
                    )
                    if novel_id > 0:
                        items_updated += 1
                        total_new_chapters += new_ch
                        updated_novel_ids.append(novel_id)

                        # Auto-export if configured
                        if self.settings.daily.export_txt_on_complete and new_ch > 0:
                            await self.exporter.export_novel_txt(novel_id)
                    else:
                        errors_count += 1
                except Exception as e:
                    errors_count += 1
                    log.error(f"[DailySync] Error syncing novel {novel_url}: {e}")

                await asyncio.sleep(self.state.get_random_page_delay())

        finally:
            watcher_task.cancel()
            await feed_page.close()
            await nav_page.close()
            await fetch_page.close()
            await self.browser_manager.close()

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

        # 5. Beautiful Summary Table
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
