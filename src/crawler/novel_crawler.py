"""
Novel Crawler: Extracts Novel Details, Volumes, Chapters, and Inline Images
"""

import asyncio
import random
import time
from typing import Optional, Set, Tuple
from playwright.async_api import Page, BrowserContext
from ..config import CrawlerEngineConfig, Settings, CONFIG
from ..core.rate_limiter import SharedCrawlState
from ..database.models import Novel, Volume, Chapter
from ..database.repository import NovelRepository
from ..parsers.novel_parser import NovelParser, ParsedNovelInfo
from ..parsers.chapter_parser import ChapterParser, ParsedChapterContent
from .media_crawler import MediaCrawler
from ..utils.logger import get_logger, console

log = get_logger("novel_crawler")


class NovelCrawler:
    """Coordinates crawling of a single novel with full chapter, image, and DB persistence."""

    def __init__(
        self,
        config: CrawlerEngineConfig,
        repository: NovelRepository,
        media_crawler: MediaCrawler,
        state: SharedCrawlState,
        settings: Optional[Settings] = None,
    ):
        self.config = config
        self.repository = repository
        self.media_crawler = media_crawler
        self.state = state
        self.settings = settings or CONFIG

    async def fetch_novel_info(self, page: Page, novel_url: str) -> Optional[ParsedNovelInfo]:
        """Loads novel page and extracts all structured metadata, volumes, and chapters."""
        for attempt in range(1, self.config.max_retries + 1):
            await self.state.proceed.wait()
            await self.state.rate_limiter.acquire()

            try:
                resp = await page.goto(
                    novel_url,
                    timeout=self.config.goto_timeout_ms,
                    wait_until="domcontentloaded",
                )

                if resp and resp.status in (429, 502, 503, 504):
                    await self.state.rate_limiter.on_429()
                    backoff = random.uniform(self.config.backoff_429_min, self.config.backoff_429_max)
                    log.warning(f"[Server Status {resp.status}] Cooldown triggered for {novel_url}. Backoff {backoff:.1f}s...")
                    await self.state.trigger_backoff(backoff)
                    await self.state.proceed.wait()
                    continue

                # Wait for title to appear
                await page.locator("span.series-name a, span.series-name").first.wait_for(
                    timeout=self.config.selector_timeout_ms
                )

                html = await page.content()
                info = NovelParser.parse_novel_html(html, novel_url, self.config.base_url)
                await self.state.rate_limiter.on_success()
                return info

            except Exception as e:
                log.warning(f"[Attempt {attempt}/{self.config.max_retries}] Error loading novel {novel_url}: {e}")
                if attempt < self.config.max_retries:
                    await asyncio.sleep(self.config.retry_backoff_base ** attempt + random.random())

        log.error(f"[Failed] Could not load novel details: {novel_url}")
        return None

    async def fetch_chapter_content(
        self, page: Page, chapter_url: str
    ) -> Optional[ParsedChapterContent]:
        """Loads chapter page and extracts text, html, and images."""
        for attempt in range(1, self.config.max_retries + 1):
            await self.state.proceed.wait()
            await self.state.rate_limiter.acquire()

            try:
                resp = await page.goto(
                    chapter_url,
                    timeout=self.config.goto_timeout_ms,
                    wait_until="domcontentloaded",
                )

                if resp and resp.status in (429, 502, 503, 504):
                    await self.state.rate_limiter.on_429()
                    backoff = random.uniform(self.config.backoff_429_min, self.config.backoff_429_max)
                    log.warning(f"[Server Status {resp.status}] Cooldown triggered for {chapter_url}. Backoff {backoff:.1f}s...")
                    await self.state.trigger_backoff(backoff)
                    await self.state.proceed.wait()
                    continue

                # Wait for chapter content container
                await page.locator("#chapter-content").first.wait_for(
                    timeout=self.config.selector_timeout_ms
                )

                html = await page.content()
                content = ChapterParser.parse_chapter_html(html, chapter_url, self.config.base_url)
                await self.state.rate_limiter.on_success()
                return content

            except Exception as e:
                log.warning(f"[Attempt {attempt}/{self.config.max_retries}] Error loading chapter {chapter_url}: {e}")
                if attempt < self.config.max_retries:
                    await asyncio.sleep(self.config.retry_backoff_base ** attempt + random.random())

        return None

    async def crawl_novel(
        self,
        nav_page: Page,
        fetch_page: Page,
        novel_url: str,
        force_recrawl: bool = False,
    ) -> Tuple[int, int, int]:
        """
        Main novel crawling routine.
        Returns: (novel_id, total_chapters, new_chapters_count)
        """
        log.info(f"[Novel] Fetching metadata for: [bold blue]{novel_url}[/bold blue]")

        # 1. Fetch novel page details
        info = await self.fetch_novel_info(nav_page, novel_url)
        if not info:
            await self.repository.add_to_retry_queue(
                item_type="novel",
                target_url=novel_url,
                last_error="Failed to fetch novel metadata",
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

        for idx, (vol_id, ch_ref) in enumerate(chapters_to_process, 1):
            log.info(
                f"  [{idx:>3}/{len(chapters_to_process)}] "
                f"[dim]{info.title[:25]}[/dim] › [cyan]{ch_ref.title[:45]}[/cyan]"
            )

            chap_content = await self.fetch_chapter_content(fetch_page, ch_ref.url)

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
                log.error(f"  [Chapter Failed] {ch_ref.title} ({ch_ref.url}) -> Queueing for Post-Retry")
                # Record failed chapter in DB
                chap_model = Chapter(
                    novel_id=novel_id,
                    volume_id=vol_id,
                    chapter_index=ch_ref.chapter_index,
                    title=ch_ref.title,
                    url=ch_ref.url,
                    crawl_status="failed",
                    error_message="Chapter content fetch timeout or blocked",
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
                    last_error="Fetch content timeout/failed",
                )

            # Adaptive delay between chapters
            delay = self.state.get_random_chapter_delay()
            await asyncio.sleep(delay)

        # Mark novel completed
        await self.repository.update_novel_status(novel_id, "completed")
        log.info(f"[Done] Novel completed: [bold green]{info.title}[/bold green] (+{new_chapters_count} new chapters)")
        return (novel_id, total_chapters, new_chapters_count)
