"""
Retry Manager: In-Run Retry Decorator and Post-Retry (Dead Letter Queue) Processor
"""

import asyncio
import random
from typing import Callable, Any, Optional
from ..database.repository import NovelRepository
from ..database.models import RetryItem
from ..utils.logger import get_logger

log = get_logger("retry_manager")


async def retry_async(
    func: Callable[..., Any],
    *args,
    max_retries: int = 3,
    backoff_base: float = 2.0,
    jitter: bool = True,
    operation_name: str = "operation",
    **kwargs,
) -> Any:
    """
    Executes an async function with exponential backoff and jitter.
    """
    last_error: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                delay = (backoff_base ** attempt) + (random.uniform(0.5, 1.5) if jitter else 0.0)
                log.warning(
                    f"[{operation_name}] Attempt {attempt}/{max_retries} failed: {e}. "
                    f"Retrying in {delay:.1f}s..."
                )
                await asyncio.sleep(delay)
            else:
                log.error(f"[{operation_name}] All {max_retries} attempts failed. Last error: {e}")

    if last_error:
        raise last_error


class PostRetryWorker:
    """
    Worker that processes failed items in the retry_queue table after normal crawling runs.
    """

    def __init__(self, repository: NovelRepository):
        self.repository = repository

    async def process_pending_retries(self, crawler_engine, batch_size: int = 30) -> int:
        """
        Pulls pending retry items from SQLite and re-attempts crawling them.
        Returns the number of resolved items.
        """
        pending_items = await self.repository.get_pending_retries(limit=batch_size)
        if not pending_items:
            log.info("[PostRetry] No pending items in retry queue.")
            return 0

        log.info(f"[PostRetry] Starting Post-Retry phase for {len(pending_items)} failed items...")
        resolved_count = 0

        for item in pending_items:
            try:
                log.info(f"[PostRetry] Retrying {item.item_type} #{item.id} -> {item.target_url}")
                if item.item_type == "chapter":
                    # Re-attempt fetching this chapter
                    novel_id = item.extra_data.get("novel_id")
                    volume_id = item.extra_data.get("volume_id")
                    chapter_index = item.extra_data.get("chapter_index", 0)
                    chapter_title = item.extra_data.get("title", "Chương")

                    success = await crawler_engine.crawl_single_chapter(
                        chapter_url=item.target_url,
                        novel_id=novel_id,
                        volume_id=volume_id,
                        chapter_index=chapter_index,
                        chapter_title=chapter_title,
                    )
                    if success:
                        await self.repository.resolve_retry(item.id)
                        resolved_count += 1
                        log.info(f"[PostRetry] Successfully RESOLVED chapter: {item.target_url}")
                    else:
                        await self.repository.fail_retry(item.id, "Post-retry chapter fetch failed")

                elif item.item_type == "image":
                    # Re-attempt downloading image
                    success = await crawler_engine.download_single_image(
                        image_id=item.target_id,
                        image_url=item.target_url,
                        image_type=item.extra_data.get("image_type", "chapter_illustration"),
                    )
                    if success:
                        await self.repository.resolve_retry(item.id)
                        resolved_count += 1
                        log.info(f"[PostRetry] Successfully RESOLVED image: {item.target_url}")
                    else:
                        await self.repository.fail_retry(item.id, "Post-retry image download failed")

                elif item.item_type == "novel":
                    # Re-attempt novel crawl
                    success = await crawler_engine.crawl_single_novel(
                        novel_url=item.target_url,
                        force_recrawl=False,
                    )
                    if success:
                        await self.repository.resolve_retry(item.id)
                        resolved_count += 1
                        log.info(f"[PostRetry] Successfully RESOLVED novel: {item.target_url}")
                    else:
                        await self.repository.fail_retry(item.id, "Post-retry novel crawl failed")

            except Exception as e:
                log.error(f"[PostRetry] Error processing item {item.target_url}: {e}")
                await self.repository.fail_retry(item.id, str(e))

            await asyncio.sleep(crawler_engine.state.get_random_chapter_delay())

        log.info(f"[PostRetry] Completed Post-Retry phase: {resolved_count}/{len(pending_items)} resolved.")
        return resolved_count
