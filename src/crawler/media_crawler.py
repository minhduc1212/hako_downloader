"""
Media Crawler: Asynchronous Downloader for Covers and Chapter Illustrations
"""

import asyncio
import hashlib
from pathlib import Path
from typing import Optional
import httpx
from ..config import MediaConfig, ProxyConfig
from ..core.proxy_manager import ProxyManager, get_proxy_manager
from ..database.repository import NovelRepository
from ..utils.helpers import calculate_hash, sanitize_filename
from ..utils.logger import get_logger

log = get_logger("media")


class MediaCrawler:
    """Handles downloading and local storage of novel covers and chapter inline illustrations."""

    def __init__(
        self,
        config: MediaConfig,
        repository: NovelRepository,
        proxy_manager: Optional[ProxyManager] = None,
    ):
        self.config = config
        self.repository = repository
        self.proxy_manager = proxy_manager or get_proxy_manager()
        self._semaphore = asyncio.Semaphore(self.config.max_image_workers)

    async def _get_http_client(self) -> httpx.AsyncClient:
        proxy_url = self.proxy_manager.get_current_proxy()
        return httpx.AsyncClient(
            proxy=proxy_url if proxy_url else None,
            timeout=self.config.image_timeout_sec,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Referer": "https://docln.sbs/",
            },
            follow_redirects=True,
        )

    async def download_cover(self, novel_id: int, novel_slug: str, cover_url: str) -> Optional[str]:
        """Download novel cover and store locally."""
        if not self.config.download_images or not self.config.download_covers or not cover_url:
            return None

        async with self._semaphore:
            try:
                ext = ".jpg"
                if ".png" in cover_url.lower():
                    ext = ".png"
                elif ".webp" in cover_url.lower():
                    ext = ".webp"

                filename = f"{sanitize_filename(novel_slug)}{ext}"
                dest_path = self.config.cover_dir / filename
                dest_path.parent.mkdir(parents=True, exist_ok=True)

                if dest_path.exists() and dest_path.stat().st_size > 0:
                    local_str = str(dest_path)
                    await self.repository.update_novel_cover_local(novel_id, local_str)
                    return local_str

                async with await self._get_http_client() as client:
                    resp = await client.get(cover_url)
                    if resp.status_code == 200 and len(resp.content) > 0:
                        dest_path.write_bytes(resp.content)
                        local_str = str(dest_path)
                        await self.repository.update_novel_cover_local(novel_id, local_str)
                        log.debug(f"[Media] Saved cover for {novel_slug} ({len(resp.content)} bytes)")
                        return local_str
                    else:
                        log.warning(f"[Media] Failed downloading cover {cover_url} (HTTP {resp.status_code})")
            except Exception as e:
                log.warning(f"[Media] Error downloading cover {cover_url}: {e}")
        return None

    async def download_chapter_image(
        self,
        novel_id: int,
        novel_slug: str,
        chapter_id: int,
        img_url: str,
    ) -> Optional[str]:
        """Download inline chapter illustration."""
        if not self.config.download_images or not self.config.download_chapter_illustrations or not img_url:
            return None

        async with self._semaphore:
            try:
                # Record image in DB first
                img_db_id = await self.repository.record_image(
                    novel_id=novel_id,
                    chapter_id=chapter_id,
                    original_url=img_url,
                    image_type="chapter_illustration",
                )

                # Generate clean hash-based filename
                url_hash = hashlib.md5(img_url.encode("utf-8")).hexdigest()[:12]
                ext = ".jpg"
                if ".png" in img_url.lower():
                    ext = ".png"
                elif ".webp" in img_url.lower():
                    ext = ".webp"

                novel_folder = self.config.chapter_img_dir / sanitize_filename(novel_slug)
                novel_folder.mkdir(parents=True, exist_ok=True)
                dest_path = novel_folder / f"{url_hash}{ext}"

                if dest_path.exists() and dest_path.stat().st_size > 0:
                    local_str = str(dest_path)
                    await self.repository.mark_image_downloaded(img_db_id, local_str, dest_path.stat().st_size)
                    return local_str

                async with await self._get_http_client() as client:
                    resp = await client.get(img_url)
                    if resp.status_code == 200 and len(resp.content) > 0:
                        dest_path.write_bytes(resp.content)
                        local_str = str(dest_path)
                        await self.repository.mark_image_downloaded(img_db_id, local_str, len(resp.content))
                        log.debug(f"[Media] Saved illustration for {novel_slug}: {dest_path.name}")
                        return local_str
                    else:
                        await self.repository.mark_image_failed(img_db_id, f"HTTP {resp.status_code}")
            except Exception as e:
                log.warning(f"[Media] Error downloading image {img_url}: {e}")
                if "img_db_id" in locals() and img_db_id:
                    await self.repository.mark_image_failed(img_db_id, str(e))
        return None

    async def download_all_chapter_images(
        self,
        novel_id: int,
        novel_slug: str,
        chapter_id: int,
        image_urls: list,
    ):
        """Concurrently download all illustration images for a chapter."""
        if not image_urls:
            return
        tasks = [
            self.download_chapter_image(novel_id, novel_slug, chapter_id, url)
            for url in image_urls
        ]
        await asyncio.gather(*tasks, return_exceptions=True)
