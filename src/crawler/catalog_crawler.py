"""
Full Catalog Discovery and Batch Crawler Engine (crawl-all / bootstrap)
With Adaptive Rate Limiting, Proxy Rotation, 502/429 Auto-Backoff, and Automated Failed-Page Recovery
"""

import asyncio
import random
import time
from typing import List, Optional, Set, Dict, Any, Union
import httpx
from bs4 import BeautifulSoup
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn

from ..config import Settings, CONFIG
from ..database.models import Novel, CrawlLog
from ..database.repository import NovelRepository
from ..parsers.feed_parser import FeedParser
from ..core.proxy_manager import get_proxy_manager
from ..core.rate_limiter import get_rate_limiter
from ..utils.helpers import extract_novel_slug
from ..utils.logger import get_logger, console
from .engine import CrawlerEngine

log = get_logger("catalog_crawler")


class CatalogCrawler:
    """
    Sweeps the entire Docln catalog (all ~114 pages / ~4,700 novels),
    populates the SQLite database, and executes multi-worker full crawling with resume capability.
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or CONFIG
        self.repository = NovelRepository()
        self.proxy_manager = get_proxy_manager()
        self.rate_limiter = get_rate_limiter(self.settings.crawler)
        self.engine = CrawlerEngine(self.settings)

    def _get_headers(self) -> Dict[str, str]:
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
        ]
        return {
            "User-Agent": random.choice(user_agents),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://docln.sbs/danh-sach",
        }

    async def get_total_catalog_pages(self, sort_by: str = "tentruyen") -> int:
        """Fetch page 1 to detect total page count."""
        url = f"{self.settings.crawler.base_url}/danh-sach?sapxep={sort_by}&page=1"
        for _ in range(3):
            try:
                proxy_url = self.proxy_manager.get_current_proxy()
                async with httpx.AsyncClient(
                    proxy=proxy_url if proxy_url else None,
                    timeout=20.0,
                    headers=self._get_headers(),
                    follow_redirects=True,
                ) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        max_p = FeedParser.extract_max_page(resp.text)
                        return max(1, max_p)
            except Exception as e:
                log.debug(f"Error detecting max catalog pages: {e}")
                self.proxy_manager.rotate_proxy()
                await asyncio.sleep(1.5)
        return 114  # Default fallback

    async def _fetch_catalog_page_with_retry(
        self,
        url: str,
        page_num: int,
        max_retries: int = 5,
    ) -> Optional[str]:
        """
        Fetch a single catalog page HTML with automatic 502/503/429 backoff and proxy rotation.
        """
        for attempt in range(1, max_retries + 1):
            await self.rate_limiter.acquire()
            proxy_url = self.proxy_manager.get_current_proxy()

            try:
                async with httpx.AsyncClient(
                    proxy=proxy_url if proxy_url else None,
                    timeout=25.0,
                    headers=self._get_headers(),
                    follow_redirects=True,
                ) as client:
                    resp = await client.get(url)

                    if resp.status_code == 200:
                        self.rate_limiter.notify_success()
                        return resp.text

                    # Handle 429, 502, 503, 504 (Server busy, rate-limit, bad gateway)
                    if resp.status_code in (429, 502, 503, 504):
                        self.rate_limiter.notify_429()
                        if self.proxy_manager.is_enabled:
                            new_proxy = self.proxy_manager.rotate_proxy()
                            log.warning(
                                f"[Catalog] Page {page_num} returned HTTP {resp.status_code} "
                                f"-> Rotated proxy to: {new_proxy}"
                            )
                            await asyncio.sleep(2.0)
                        else:
                            backoff = min(12.0 * attempt, 45.0)
                            log.warning(
                                f"[Catalog] Page {page_num} returned HTTP {resp.status_code} (Server Cooldown) "
                                f"-> Backing off for {backoff:.1f}s (Attempt {attempt}/{max_retries})..."
                            )
                            await asyncio.sleep(backoff)
                    else:
                        log.warning(f"[Catalog] Page {page_num} returned HTTP {resp.status_code} (Attempt {attempt}/{max_retries})")
                        await asyncio.sleep(3.0)

            except (httpx.RequestError, httpx.TimeoutException) as e:
                log.warning(f"[Catalog] Connection error on page {page_num}: {e} (Attempt {attempt}/{max_retries})")
                if self.proxy_manager.is_enabled:
                    self.proxy_manager.rotate_proxy()
                await asyncio.sleep(3.0)

        log.error(f"[Catalog] Failed to fetch page {page_num} after {max_retries} attempts.")
        return None

    async def discover_catalog_urls(
        self,
        start_page: int = 1,
        end_page: int = 0,
        sort_by: str = "tentruyen",
        specific_pages: Optional[List[int]] = None,
    ) -> List[str]:
        """
        Scans catalog pages and extracts all novel URLs.
        Registers new novels into SQLite DB with status='pending'.
        Includes an automated second-pass recovery for any pages that failed in pass 1.
        """
        if specific_pages:
            pages_to_scan = sorted(list(set(specific_pages)))
            log.info(f"[Catalog] Scanning {len(pages_to_scan)} specific pages: {pages_to_scan[:10]}...")
        else:
            max_detected = await self.get_total_catalog_pages(sort_by=sort_by)
            actual_end = max_detected if (end_page <= 0 or end_page > max_detected) else end_page
            pages_to_scan = list(range(start_page, actual_end + 1))
            log.info(
                f"[Catalog] Discovering novels from page {start_page} to {actual_end} "
                f"(Total catalog pages: {max_detected})..."
            )

        all_novel_urls: List[str] = []
        seen: Set[str] = set()
        failed_pages: List[int] = []

        # ── Pass 1: Main Sweep ──
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]Scanning catalog pages...", total=len(pages_to_scan))

            for page_num in pages_to_scan:
                url = f"{self.settings.crawler.base_url}/danh-sach?sapxep={sort_by}&page={page_num}"
                html_text = await self._fetch_catalog_page_with_retry(url, page_num=page_num)

                if html_text:
                    page_urls = FeedParser.parse_catalog_novel_urls(html_text, self.settings.crawler.base_url)
                    for u in page_urls:
                        if u not in seen:
                            seen.add(u)
                            all_novel_urls.append(u)

                            # Register in DB if not existing
                            existing = await self.repository.get_novel_by_url(u)
                            if not existing:
                                slug = extract_novel_slug(u)
                                new_novel = Novel(
                                    url=u,
                                    slug=slug,
                                    title=slug,
                                    crawl_status="pending",
                                )
                                await self.repository.upsert_novel(new_novel)
                else:
                    failed_pages.append(page_num)

                progress.advance(task)
                # Adaptive polite delay between catalog pages
                await asyncio.sleep(random.uniform(1.0, 1.8))

        # ── Pass 2: Automated Second-Pass Recovery for Failed Pages ──
        if failed_pages:
            console.print(
                f"[bold yellow]⚠️ {len(failed_pages)} page(s) failed in Pass 1 ({failed_pages}). "
                f"Starting Automated Second-Pass Recovery in 10s...[/bold yellow]"
            )
            await asyncio.sleep(10.0)

            unresolved_pages = []
            for page_num in failed_pages:
                url = f"{self.settings.crawler.base_url}/danh-sach?sapxep={sort_by}&page={page_num}"
                log.info(f"[Catalog Recovery] Retrying failed page {page_num}...")
                html_text = await self._fetch_catalog_page_with_retry(url, page_num=page_num, max_retries=7)

                if html_text:
                    page_urls = FeedParser.parse_catalog_novel_urls(html_text, self.settings.crawler.base_url)
                    for u in page_urls:
                        if u not in seen:
                            seen.add(u)
                            all_novel_urls.append(u)
                            existing = await self.repository.get_novel_by_url(u)
                            if not existing:
                                slug = extract_novel_slug(u)
                                new_novel = Novel(
                                    url=u,
                                    slug=slug,
                                    title=slug,
                                    crawl_status="pending",
                                )
                                await self.repository.upsert_novel(new_novel)
                    log.info(f"[Catalog Recovery] ✓ Successfully recovered page {page_num}!")
                else:
                    unresolved_pages.append(page_num)
                await asyncio.sleep(2.0)

            if unresolved_pages:
                log.error(f"[Catalog] Unresolved pages after recovery pass: {unresolved_pages}")
            else:
                console.print("[bold green]✓ All previously failed pages were successfully recovered![/bold green]")

        log.info(f"[Catalog] Total unique novels discovered and indexed: [bold green]{len(all_novel_urls)}[/bold green]")
        return all_novel_urls

    async def crawl_all_catalog(
        self,
        start_page: int = 1,
        end_page: int = 0,
        workers: Optional[int] = None,
        force_recrawl: bool = False,
        rescan: bool = False,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Crawls novels from SQLite DB with instant resume capability.
        Only scans catalog web pages if DB is empty or --rescan is requested.
        """
        start_time = time.monotonic()
        log_id = await self.repository.create_crawl_log(crawl_type="catalog_full")

        total_in_db = await self.repository.get_novels_count()
        pending_in_db = await self.repository.get_pending_novels_count()

        # 1. Determine URLs to crawl
        if total_in_db > 0 and not rescan and not force_recrawl:
            log.info(
                f"[Resume] Found [bold green]{total_in_db:,} novels[/bold green] in SQLite DB "
                f"([green]{total_in_db - pending_in_db:,} completed[/green], "
                f"[yellow]{pending_in_db:,} pending[/yellow]). "
                f"Resuming directly from DB..."
            )
            urls_to_crawl = await self.repository.get_pending_novel_urls(limit=limit)
            total_items = total_in_db
        else:
            # Discover from web catalog
            novel_urls = await self.discover_catalog_urls(start_page=start_page, end_page=end_page)
            if limit and limit > 0:
                novel_urls = novel_urls[:limit]

            if not force_recrawl:
                pending_urls = []
                for u in novel_urls:
                    n = await self.repository.get_novel_by_url(u)
                    if not n or n.crawl_status != "completed":
                        pending_urls.append(u)
                urls_to_crawl = pending_urls
            else:
                urls_to_crawl = novel_urls
            total_items = len(novel_urls)

        if not urls_to_crawl:
            console.print("[bold green]✓ All novels in SQLite database are already 100% completed![/bold green]")
            return {"total": total_items, "crawled": 0, "duration": 0.0}

        log.info(f"Starting crawl for [bold yellow]{len(urls_to_crawl)} pending novels[/bold yellow]...")

        # 2. Override worker count if specified
        if workers and workers > 0:
            self.settings.crawler.num_workers = workers

        # 3. Run crawler engine on pending queue
        result = await self.engine.crawl_urls(urls_to_crawl, force_recrawl=force_recrawl)

        duration = time.monotonic() - start_time
        log_model = CrawlLog(
            id=log_id,
            crawl_type="catalog_full",
            status="success",
            items_checked=total_items,
            items_updated=len(urls_to_crawl),
            duration_seconds=duration,
        )
        await self.repository.update_crawl_log(log_model)

        return result
