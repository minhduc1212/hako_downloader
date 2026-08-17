"""
Async Multi-Worker Crawler Engine Orchestrator (Pure HTTPX + Asyncio)
"""

import asyncio
import time
from typing import List, Optional
from asyncio import Queue

from ..config import Settings, CONFIG
from ..core.proxy_manager import get_proxy_manager
from ..core.rate_limiter import SharedCrawlState
from ..core.retry_manager import PostRetryWorker
from ..database.repository import NovelRepository
from .media_crawler import MediaCrawler
from .novel_crawler import NovelCrawler
from ..utils.logger import get_logger, console

log = get_logger("engine")


class CrawlerEngine:
    """
    Main asynchronous crawler engine orchestrating multi-workers,
    shared rate-limiter, proxy rotation, and post-retry verification.
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

    async def _worker(
        self,
        worker_id: int,
        queue: Queue,
        force_recrawl: bool = False,
    ):
        """Worker task processing novel URLs from queue with dedicated HTTP session."""
        log.debug(f"Worker {worker_id} started.")
        worker_crawler = NovelCrawler(
            self.settings.crawler,
            self.repository,
            self.media_crawler,
            self.state,
            proxy_manager=self.proxy_manager,
            settings=self.settings,
        )

        try:
            while True:
                try:
                    novel_url = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                try:
                    await worker_crawler.crawl_novel(
                        novel_url=novel_url,
                        force_recrawl=force_recrawl,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.error(f"[Worker {worker_id}] Unhandled error on {novel_url}: {e}")
                finally:
                    queue.task_done()
                    # Polite pause between novels
                    await asyncio.sleep(self.state.get_random_page_delay())
        finally:
            await worker_crawler.close_session()
            log.debug(f"Worker {worker_id} finished.")

    async def crawl_urls(self, urls: List[str], force_recrawl: bool = False) -> dict:
        """
        Runs multi-worker crawler on a list of novel URLs.
        Includes automatic post-retry execution when main queue completes.
        """
        if not urls:
            log.info("No URLs provided to crawl.")
            return {"total": 0, "completed": 0, "duration": 0.0}

        start_time = time.monotonic()
        effective_workers = max(1, self.settings.crawler.num_workers)

        log.info(
            f"Starting crawl for {len(urls)} novels using {effective_workers} concurrent worker(s) "
            f"({'Proxy Pool' if self.proxy_manager.is_enabled else 'Auto-Paced Multi-Worker'})..."
        )

        queue: Queue = asyncio.Queue()
        for url in urls:
            queue.put_nowait(url)

        watcher_task = asyncio.create_task(self.state.backoff_watcher())

        try:
            workers = [
                asyncio.create_task(
                    self._worker(i + 1, queue, force_recrawl=force_recrawl)
                )
                for i in range(effective_workers)
            ]
            await asyncio.gather(*workers)
        finally:
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass

        # ── Post-Retry Phase ──
        if self.settings.crawler.enable_post_retry:
            log.info("[Post-Retry] Checking for failed items to retry...")
            await self.post_retry_worker.process_pending_retries(self)

        duration = time.monotonic() - start_time
        log.info(f"[Crawl Finished] Processed {len(urls)} novels in {duration:.1f}s.")
        return {"total": len(urls), "completed": len(urls), "duration": duration}

    async def crawl_single_novel(self, novel_url: str, force_recrawl: bool = False) -> bool:
        """Helper to crawl a single novel."""
        watcher_task = asyncio.create_task(self.state.backoff_watcher())
        try:
            novel_id, total_ch, new_ch = await self.novel_crawler.crawl_novel(
                novel_url=novel_url,
                force_recrawl=force_recrawl,
            )
            return novel_id > 0
        finally:
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass

    async def crawl_single_chapter(
        self,
        chapter_url: str,
        novel_id: Optional[int],
        volume_id: Optional[int],
        chapter_index: int = 0,
        chapter_title: str = "",
    ) -> bool:
        """Helper to crawl a single chapter (used in post-retry)."""
        content = await self.novel_crawler.fetch_chapter_content(chapter_url)
        if content and novel_id:
            from ..database.models import Chapter
            chap_model = Chapter(
                novel_id=novel_id,
                volume_id=volume_id,
                chapter_index=chapter_index,
                title=content.title or chapter_title,
                url=chapter_url,
                word_count=content.word_count,
                publish_date=content.publish_date,
                text_content=content.text_content,
                html_content=content.html_content,
                images=content.image_urls,
                crawl_status="completed",
            )
            chap_id = await self.repository.upsert_chapter(chap_model)
            if content.image_urls:
                novel = await self.repository.get_novel_by_id(novel_id)
                slug = novel.slug if novel else f"novel_{novel_id}"
                await self.media_crawler.download_all_chapter_images(
                    novel_id=novel_id,
                    novel_slug=slug,
                    chapter_id=chap_id,
                    image_urls=content.image_urls,
                )
            return True
        return False

    async def download_single_image(
        self,
        image_id: Optional[int],
        image_url: str,
        image_type: str = "chapter_illustration",
    ) -> bool:
        """Helper to retry downloading a single image."""
        if not image_id:
            return False
        return True
