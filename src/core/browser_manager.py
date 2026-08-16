"""
Playwright Browser Context Manager with Anti-Detection and Stealth Features
"""

import re
import random
from typing import Optional, List
from playwright.async_api import async_playwright, BrowserContext, Page, Playwright
from ..config import CrawlerEngineConfig, ProxyConfig
from .proxy_manager import ProxyManager, get_proxy_manager
from ..utils.logger import get_logger

log = get_logger("browser")

# User-Agent rotation pool
USER_AGENTS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
]

# Common resource extensions to abort for fast crawling (media, fonts, tracking)
# Note: we do NOT block scripts (.js) because chapter text decryption relies on docln scripts
_BLOCK_EXT_RE = re.compile(
    r"\.(woff2?|ttf|otf|eot|mp4|webm|avi|mp3|ogg|wav)(\?.*)?$",
    re.IGNORECASE,
)

# Common analytics / ads domains to block
_BLOCK_DOMAINS = [
    "google-analytics.com",
    "googletagmanager.com",
    "facebook.net",
    "doubleclick.net",
    "adnxs.com",
]


class BrowserManager:
    """Manages Playwright lifecycle, stealth context initialization, and page setup."""

    def __init__(
        self,
        crawler_config: CrawlerEngineConfig,
        proxy_manager: Optional[ProxyManager] = None,
    ):
        self.crawler_config = crawler_config
        self.proxy_manager = proxy_manager or get_proxy_manager()
        self._pw: Optional[Playwright] = None
        self.context: Optional[BrowserContext] = None

    async def start(self) -> BrowserContext:
        """Launch the persistent or standard browser context with stealth parameters."""
        self._pw = await async_playwright().start()

        # Stealth browser arguments
        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-infobars",
            "--window-size=1920,1080",
        ]

        user_agent = random.choice(USER_AGENTS)
        proxy_dict = self.proxy_manager.get_playwright_proxy()

        if proxy_dict:
            log.info(f"[Browser] Launching with proxy: {proxy_dict.get('server')}")

        # Launch persistent context to preserve cookies and session state
        self.context = await self._pw.chromium.launch_persistent_context(
            user_data_dir=self.crawler_config.user_data_dir,
            headless=self.crawler_config.headless,
            args=args,
            user_agent=user_agent,
            proxy=proxy_dict,
            viewport={"width": 1920, "height": 1080},
            ignore_https_errors=True,
        )

        # Inject stealth scripts to mask webdriver and automation fingerprints
        await self.context.add_init_script("""
            // Mask navigator.webdriver
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
            });
            // Overwrite chrome runtime
            window.chrome = { runtime: {} };
            // Fake plugins & languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['vi-VN', 'vi', 'en-US', 'en'],
            });
        """)

        log.info(f"[Browser] BrowserContext initialized (Headless={self.crawler_config.headless})")
        return self.context

    async def setup_page(self, page: Page):
        """Configure page-level routes and blocking rules for max speed & min memory."""
        if self.crawler_config.block_resources:
            async def route_interceptor(route):
                req = route.request
                res_type = req.resource_type
                url = req.url

                # Block images, fonts, media, audio, video in browser (images downloaded via async MediaCrawler)
                if res_type in ("image", "font", "media", "stylesheet", "other"):
                    await route.abort()
                    return

                # Block ad / tracker domains
                if any(domain in url for domain in _BLOCK_DOMAINS):
                    await route.abort()
                    return

                # Block heavy font/video extensions
                if _BLOCK_EXT_RE.search(url):
                    await route.abort()
                    return

                # Allow HTML document and essential scripts for JS text decryption
                await route.continue_()

            await page.route("**/*", route_interceptor)

    async def close(self):
        """Cleanly close context and Playwright instance."""
        if self.context:
            try:
                await self.context.close()
            except Exception as e:
                log.debug(f"Error closing browser context: {e}")
            self.context = None

        if self._pw:
            try:
                await self._pw.stop()
            except Exception as e:
                log.debug(f"Error stopping playwright: {e}")
            self._pw = None

        log.info("[Browser] Browser shut down cleanly.")
