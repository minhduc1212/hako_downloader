"""
Novel Crawler: Extracts Novel Details, Volumes, Chapters, and Inline Images via High-Speed Async HTTPX Session
"""

import asyncio
import random
import time
from typing import Optional, Set, Tuple, Dict, Any
import httpx

from ..config import CrawlerEngineConfig, Settings, CONFIG
from ..core.rate_limiter import SharedCrawlState
from ..core.proxy_manager import get_proxy_manager, ProxyManager
from ..database.models import Novel, Volume, Chapter
from ..database.repository import NovelRepository
from ..parsers.novel_parser import NovelParser, ParsedNovelInfo
from ..parsers.chapter_parser import ChapterParser, ParsedChapterContent
from .media_crawler import MediaCrawler
from ..utils.logger import get_logger, console

log = get_logger("novel_crawler")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]


class NovelCrawler:
    """
    Coordinates high-speed crawling of novels, chapters, and images using persistent HTTPX session
    with automatic cookie management, browser headers, and direct XOR decryption.
    """

    def __init__(
        self,
        config: CrawlerEngineConfig,
        repository: NovelRepository,
        media_crawler: MediaCrawler,
        state: SharedCrawlState,
        proxy_manager: Optional[ProxyManager] = None,
        settings: Optional[Settings] = None,
    ):
        self.config = config
        self.repository = repository
        self.media_crawler = media_crawler
        self.state = state
        self.proxy_manager = proxy_manager or get_proxy_manager()
        self.settings = settings or CONFIG
        self._client: Optional[httpx.AsyncClient] = None
        self._ua = random.choice(USER_AGENTS)
        self._last_error: str = ""

    def _get_headers(self, referer: Optional[str] = None) -> Dict[str, str]:
        return {
            "User-Agent": self._ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Upgrade-Insecure-Requests": "1",
            "Referer": referer or self.config.base_url,
        }

    async def get_session_client(self) -> httpx.AsyncClient:
        """Returns or initializes the persistent async session client."""
        if self._client is None or self._client.is_closed:
            proxy_url = self.proxy_manager.get_current_proxy() if self.proxy_manager.is_enabled else None
            self._client = httpx.AsyncClient(
                proxy=proxy_url,
                timeout=25.0,
                headers=self._get_headers(),
                follow_redirects=True,
            )
        return self._client

    async def close_session(self):
        """Closes the current session client cleanly."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _fetch_html_with_retry(self, url: str, referer: Optional[str] = None) -> Optional[str]:
        """Fetch raw HTML for a novel or chapter page with adaptive backoff, cookies, and rate-limiting."""
        if not url or not (url.startswith("http://") or url.startswith("https://")):
            self._last_error = f"Invalid or non-HTTP URL: {url}"
            log.error(f"[Crawler] Skipping invalid or non-HTTP URL: {url}")
            return None

        self._last_error = ""
        client = await self.get_session_client()

        for attempt in range(1, self.config.max_retries + 1):
            await self.state.wait_for_proceed()
            await self.state.rate_limiter.acquire()

            try:
                headers = self._get_headers(referer=referer)
                resp = await client.get(url, headers=headers)

                if resp.status_code == 200:
                    self.state.rate_limiter.notify_success(dict(resp.headers))
                    return resp.text

                if resp.status_code in (403, 404):
                    self._last_error = f"HTTP {resp.status_code} Locked by admin or not found"
                    log.info(f"[HTTP {resp.status_code}] Chapter locked by admin or not found: {url}")
                    return None

                if resp.status_code in (429, 502, 503, 504):
                    self._last_error = f"HTTP {resp.status_code} RateLimit / Server busy"
                    retry_after_val = None
                    retry_after = resp.headers.get("retry-after")
                    if retry_after:
                        try:
                            retry_after_val = float(retry_after) + 1.0
                        except ValueError:
                            pass
                    await self.state.rate_limiter.on_429(retry_after_sec=retry_after_val)

                    if self.proxy_manager.is_enabled:
                        new_proxy = self.proxy_manager.rotate_proxy()
                        log.warning(
                            f"[HTTP {resp.status_code}] Cooldown on {url} -> Rotated proxy to: {new_proxy}"
                        )
                        await self.close_session()
                        client = await self.get_session_client()
                    else:
                        retry_after = resp.headers.get("retry-after")
                        if retry_after:
                            try:
                                raw_backoff = float(retry_after) + random.uniform(0.5, 1.5)
                                backoff = max(self.config.backoff_429_min, min(raw_backoff, self.config.backoff_429_max))
                            except ValueError:
                                backoff = random.uniform(self.config.backoff_429_min, self.config.backoff_429_max)
                        else:
                            backoff = random.uniform(self.config.backoff_429_min, self.config.backoff_429_max)

                        log.warning(
                            f"[HTTP {resp.status_code}] Cooldown on {url} -> "
                            f"Backing off {backoff:.1f}s (Attempt {attempt}/{self.config.max_retries})..."
                        )
                        await self.state.trigger_backoff(backoff)
                        await self.state.wait_for_proceed()
                else:
                    self._last_error = f"HTTP {resp.status_code} Error"
                    log.warning(f"[HTTP {resp.status_code}] Error on {url} (Attempt {attempt}/{self.config.max_retries})")
                    await asyncio.sleep(self.config.retry_backoff_base ** attempt + random.random())

            except (httpx.RequestError, httpx.TimeoutException) as e:
                self._last_error = f"Network Exception: {e}"
                log.warning(f"[Network] Error on {url}: {e} (Attempt {attempt}/{self.config.max_retries})")
                if self.proxy_manager.is_enabled:
                    self.proxy_manager.rotate_proxy()
                    await self.close_session()
                    client = await self.get_session_client()
                await asyncio.sleep(self.config.retry_backoff_base ** attempt + random.random())

        if not self._last_error:
            self._last_error = f"Failed to fetch page after {self.config.max_retries} attempts"
        log.error(f"[Failed] Could not fetch page after {self.config.max_retries} attempts: {url}")
        return None

    async def fetch_novel_info(self, novel_url: str) -> Optional[ParsedNovelInfo]:
        """Loads novel page via HTTPX, establishes cookies, and extracts all structured metadata and chapters."""
        html = await self._fetch_html_with_retry(novel_url, referer=self.config.base_url)
        if not html:
            return None
        return NovelParser.parse_novel_html(html, novel_url, self.config.base_url)

    async def fetch_chapter_content(self, chapter_url: str, novel_url: Optional[str] = None) -> Optional[ParsedChapterContent]:
        """Loads chapter page via HTTPX session and extracts/decrypts text, html, and images."""
        html = await self._fetch_html_with_retry(chapter_url, referer=novel_url or self.config.base_url)
        if not html:
            return None
        return ChapterParser.parse_chapter_html(html, chapter_url, self.config.base_url)

    async def crawl_novel(
        self,
        novel_url: str,
        force_recrawl: bool = False,
    ) -> Tuple[int, int, int]:
        """
        Main novel crawling routine with persistent cookie session and direct XOR decryption.
        Returns: (novel_id, total_chapters, new_chapters_count)
        """
        log.info(f"[Novel] Fetching metadata for: [bold blue]{novel_url}[/bold blue]")

        # 1. Fetch novel page details (this also populates session cookies)
        info = await self.fetch_novel_info(novel_url)
        if not info:
            err = self._last_error or "Failed to fetch novel metadata"
            existing = await self.repository.get_novel_by_url(novel_url)
            if existing and existing.id:
                await self.repository.update_novel_status(existing.id, "error", error_message=err)
            else:
                from ..utils.helpers import extract_novel_slug
                slug = extract_novel_slug(novel_url)
                stub_novel = Novel(
                    url=novel_url,
                    slug=slug,
                    title=slug,
                    crawl_status="error",
                    error_message=err,
                )
                await self.repository.upsert_novel(stub_novel)

            await self.repository.add_to_retry_queue(
                item_type="novel",
                target_url=novel_url,
                last_error=err,
            )
            return (0, 0, 0)

        # 2. Save / Upsert novel in SQLite
        novel_obj = Novel(
            url=info.url,
            slug=info.slug,
            title=info.title,
            alternative_titles=info.alternative_titles,
            author=info.author,
            artist=info.artist,
            status=info.status,
            novel_type=info.novel_type,
            cover_url=info.cover_url,
            summary=info.summary,
            genres=info.genres,
            total_words=info.total_words,
            views=info.views,
            likes=info.likes,
            bookmarks=info.bookmarks,
            rating=info.rating,
            rating_count=info.rating_count,
            site_last_updated=info.site_last_updated,
            crawl_status="crawling",
            error_message=None,
        )
        novel_id = await self.repository.upsert_novel(novel_obj)
        novel_obj.id = novel_id

        # 3. Trigger cover image download in background
        if info.cover_url:
            asyncio.create_task(
                self.media_crawler.download_cover(novel_id, info.slug, info.cover_url)
            )

        # 4. Upsert volumes and collect all chapter refs
        vol_id_map = {}
        all_chapters_to_crawl = []

        for vol in info.volumes:
            vol_model = Volume(
                novel_id=novel_id,
                vol_index=vol.vol_index,
                title=vol.title,
                url=vol.url,
            )
            vol_id = await self.repository.upsert_volume(vol_model)
            vol_id_map[vol.title] = vol_id

            for ch in vol.chapters:
                all_chapters_to_crawl.append((vol_id, ch))

        total_chapters = len(all_chapters_to_crawl)
        log.info(
            f"[Novel] [bold cyan]{info.title}[/bold cyan] -> {len(info.volumes)} volumes, "
            f"{total_chapters} chapters detected."
        )

        # 5. Check already completed chapters to skip redundant downloads
        existing_urls: Set[str] = set()
        if not force_recrawl:
            existing_urls = await self.repository.get_existing_chapter_urls(novel_id)

        chapters_to_process = [
            (vid, ch) for vid, ch in all_chapters_to_crawl if ch.url not in existing_urls
        ]

        if not chapters_to_process:
            log.info(f"[Novel] All {total_chapters} chapters already up-to-date in DB.")
            await self.repository.update_novel_status(novel_id, "completed")
            return (novel_id, total_chapters, 0)

        log.info(f"[Novel] Crawling {len(chapters_to_process)} missing/new chapters for {info.title}...")

        new_chapters_count = 0
        novel_total_words = 0
        failed_chapters_count = 0

        for idx, (vol_id, ch_ref) in enumerate(chapters_to_process, 1):
            log.info(
                f"  [{idx:>3}/{len(chapters_to_process)}] "
                f"[dim]{info.title[:25]}[/dim] › [cyan]{ch_ref.title[:45]}[/cyan]"
            )

            # Pass novel_url as Referer and keep cookies
            chap_content = await self.fetch_chapter_content(ch_ref.url, novel_url=info.url)

            if chap_content:
                # Upsert chapter to DB
                chap_model = Chapter(
                    novel_id=novel_id,
                    volume_id=vol_id,
                    chapter_index=ch_ref.chapter_index,
                    title=chap_content.title or ch_ref.title,
                    url=ch_ref.url,
                    word_count=chap_content.word_count,
                    publish_date=chap_content.publish_date or ch_ref.publish_date,
                    text_content=chap_content.text_content,
                    html_content=chap_content.html_content,
                    images=chap_content.image_urls,
                    crawl_status="completed",
                    error_message=None,
                )
                chap_id = await self.repository.upsert_chapter(chap_model)
                new_chapters_count += 1
                novel_total_words += chap_content.word_count

                # Download inline chapter illustrations if any
                if chap_content.image_urls:
                    asyncio.create_task(
                        self.media_crawler.download_all_chapter_images(
                            novel_id=novel_id,
                            novel_slug=info.slug,
                            chapter_id=chap_id,
                            image_urls=chap_content.image_urls,
                        )
                    )
            else:
                err = self._last_error or "Chapter content fetch timeout or failed"
                failed_chapters_count += 1
                log.error(f"  [Chapter Failed] {ch_ref.title} ({ch_ref.url}) -> {err}")
                # Record failed chapter in DB
                chap_model = Chapter(
                    novel_id=novel_id,
                    volume_id=vol_id,
                    chapter_index=ch_ref.chapter_index,
                    title=ch_ref.title,
                    url=ch_ref.url,
                    crawl_status="failed",
                    error_message=err,
                )
                await self.repository.upsert_chapter(chap_model)

                # Add to retry queue
                await self.repository.add_to_retry_queue(
                    item_type="chapter",
                    target_url=ch_ref.url,
                    extra_data={
                        "novel_id": novel_id,
                        "volume_id": vol_id,
                        "chapter_index": ch_ref.chapter_index,
                        "title": ch_ref.title,
                    },
                    last_error=err,
                )

            # Adaptive polite delay between chapters
            delay = self.state.get_random_chapter_delay()
            await asyncio.sleep(delay)

        # Update novel status
        if failed_chapters_count > 0:
            await self.repository.update_novel_status(
                novel_id,
                "partial",
                error_message=f"{failed_chapters_count} chapter(s) failed during crawl",
            )
            log.warning(
                f"[Partial] Novel crawled with {failed_chapters_count} failed chapters: {info.title}"
            )
        else:
            await self.repository.update_novel_status(novel_id, "completed")
            log.info(f"[Done] Novel completed: [bold green]{info.title}[/bold green] (+{new_chapters_count} new chapters)")

        return (novel_id, total_chapters, new_chapters_count)
