"""
Media Crawler: High-Resilience Asynchronous Downloader for Covers and Chapter Illustrations
"""

import asyncio
import hashlib
import urllib.parse
from pathlib import Path
from typing import Optional, Dict
import httpx
from ..config import MediaConfig
from ..core.proxy_manager import ProxyManager, get_proxy_manager
from ..database.repository import NovelRepository
from ..utils.helpers import sanitize_filename
from ..utils.logger import get_logger

log = get_logger("media")

DEFAULT_IMAGE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Sec-Fetch-Dest": "image",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "cross-site",
}


class MediaCrawler:
    """
    Handles resilient, high-speed downloading and local caching of novel covers and chapter illustrations
    with smart anti-hotlink referer routing, retry backoff, and CDN fallback.
    """

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

    def _get_headers_for_url(self, img_url: str) -> Dict[str, str]:
        """Chooses the optimal Referer and headers depending on the image host."""
        headers = dict(DEFAULT_IMAGE_HEADERS)
        parsed = urllib.parse.urlparse(img_url)
        host = parsed.netloc.lower()

        # Docln/Hako internal CDNs require Docln Referer
        if any(domain in host for domain in ("docln.sbs", "docln.net", "hako.vip", "hako.re")):
            headers["Referer"] = "https://docln.sbs/"
        # External hosts (blogspot, postimg, imgur, etc.) block cross-site referers (anti-hotlink)
        elif any(domain in host for domain in ("blogspot.com", "postimg.cc", "imgur.com", "discordapp.com", "catbox.moe", "googleusercontent.com")):
            headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"
        else:
            headers.pop("Referer", None)

        return headers

    async def _download_bytes_with_retry(self, img_url: str, max_retries: int = 3) -> Optional[bytes]:
        """Download raw image bytes with retry, backoff, and fallback CDN support."""
        if not img_url or not (img_url.startswith("http://") or img_url.startswith("https://")):
            log.debug(f"[Media] Skipping invalid or non-HTTP image URL: {img_url}")
            return None

        headers = self._get_headers_for_url(img_url)
        proxy_url = self.proxy_manager.get_current_proxy() if self.proxy_manager.is_enabled else None

        # ── Pass 1: Direct Download with Smart Headers ──
        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    proxy=proxy_url,
                    timeout=self.config.image_timeout_sec,
                    headers=headers,
                    follow_redirects=True,
                    verify=False,  # Avoid SSL mismatch on outdated third-party image CDNs
                ) as client:
                    resp = await client.get(img_url)

                    if resp.status_code == 200 and len(resp.content) > 100:
                        return resp.content

                    if resp.status_code == 404:
                        # Image was permanently deleted on host
                        break

            except Exception as e:
                if attempt == max_retries:
                    log.debug(f"[Media] Attempt {attempt} failed for {img_url}: {e}")
                await asyncio.sleep(1.0 * attempt)

        # ── Pass 2: Fallback via Global Image CDN Cache (wsrv.nl) ──
        try:
            fallback_url = f"https://wsrv.nl/?url={urllib.parse.quote(img_url, safe='')}"
            async with httpx.AsyncClient(
                proxy=proxy_url,
                timeout=self.config.image_timeout_sec,
                headers=DEFAULT_IMAGE_HEADERS,
                follow_redirects=True,
                verify=False,
            ) as client:
                resp = await client.get(fallback_url)
                if resp.status_code == 200 and len(resp.content) > 100:
                    return resp.content
        except Exception:
            pass

        return None

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

                if dest_path.exists() and dest_path.stat().st_size > 100:
                    local_str = str(dest_path)
                    await self.repository.update_novel_cover_local(novel_id, local_str)
                    return local_str

                img_data = await self._download_bytes_with_retry(cover_url)
                if img_data:
                    dest_path.write_bytes(img_data)
                    local_str = str(dest_path)
                    await self.repository.update_novel_cover_local(novel_id, local_str)
                    log.debug(f"[Media] Saved cover for {novel_slug} ({len(img_data)} bytes)")
                    return local_str
                else:
                    log.debug(f"[Media] Could not download cover (expired/deleted on host): {cover_url}")
            except Exception as e:
                log.debug(f"[Media] Error downloading cover {cover_url}: {e}")
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

                if dest_path.exists() and dest_path.stat().st_size > 100:
                    local_str = str(dest_path)
                    await self.repository.mark_image_downloaded(img_db_id, local_str, dest_path.stat().st_size)
                    return local_str

                img_data = await self._download_bytes_with_retry(img_url)
                if img_data:
                    dest_path.write_bytes(img_data)
                    local_str = str(dest_path)
                    await self.repository.mark_image_downloaded(img_db_id, local_str, len(img_data))
                    log.debug(f"[Media] Saved illustration for {novel_slug}: {dest_path.name}")
                    return local_str
                else:
                    await self.repository.mark_image_failed(img_db_id, "Image expired or deleted on host")
            except Exception as e:
                log.debug(f"[Media] Error downloading image {img_url}: {e}")
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
