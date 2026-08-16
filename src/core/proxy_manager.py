"""
Proxy and Tor Network Manager for Anti-IP-Ban and Fingerprint Obfuscation
"""

import random
from typing import Optional, List, Dict, Any
from pathlib import Path
import httpx
from ..config import ProxyConfig
from ..utils.logger import get_logger

log = get_logger("proxy")


class ProxyManager:
    """Manages SOCKS5/Tor and HTTP proxies, rotation, and health verification."""

    def __init__(self, config: ProxyConfig):
        self.config = config
        self.proxies: List[str] = list(config.proxy_list)
        self._current_index = 0
        self._request_counter = 0

        # If proxy_url is set and not empty, add it to list
        if config.enabled and config.proxy_url and config.proxy_url not in self.proxies:
            self.proxies.append(config.proxy_url)

        self._load_from_file()

    def _load_from_file(self):
        """Load additional proxies from proxy_list_file if exists."""
        if self.config.proxy_list_file and Path(self.config.proxy_list_file).exists():
            try:
                with open(self.config.proxy_list_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and line not in self.proxies:
                            self.proxies.append(line)
                if self.proxies:
                    log.info(f"Loaded {len(self.proxies)} proxies into pool.")
            except Exception as e:
                log.warning(f"Could not load proxies from {self.config.proxy_list_file}: {e}")

    @property
    def is_enabled(self) -> bool:
        return self.config.enabled and len(self.proxies) > 0

    def get_current_proxy(self) -> Optional[str]:
        """Get the current active proxy URL."""
        if not self.is_enabled:
            return None
        return self.proxies[self._current_index % len(self.proxies)]

    def rotate_proxy(self) -> Optional[str]:
        """Switch to the next proxy in the list."""
        if not self.is_enabled or len(self.proxies) <= 1:
            return self.get_current_proxy()
        self._current_index = (self._current_index + 1) % len(self.proxies)
        proxy = self.proxies[self._current_index]
        log.info(f"[Proxy] Rotated to next proxy: {proxy}")
        return proxy

    def on_request(self) -> Optional[str]:
        """Call on each request. Auto-rotates if threshold reached."""
        if not self.is_enabled:
            return None
        self._request_counter += 1
        if self.config.rotate_interval_requests > 0 and self._request_counter % self.config.rotate_interval_requests == 0:
            return self.rotate_proxy()
        return self.get_current_proxy()

    def get_playwright_proxy(self) -> Optional[Dict[str, str]]:
        """Return proxy configuration dictionary formatted for Playwright."""
        proxy_url = self.get_current_proxy()
        if not proxy_url:
            return None
        return {"server": proxy_url}

    async def test_connection(self) -> Dict[str, Any]:
        """Test the current proxy connection by checking public IP."""
        proxy_url = self.get_current_proxy()
        log.info(f"[Proxy] Testing connection with proxy: {proxy_url or 'Direct Connection'}...")
        try:
            async with httpx.AsyncClient(
                proxy=proxy_url if proxy_url else None,
                timeout=15.0,
                headers={"User-Agent": "Mozilla/5.0"},
            ) as client:
                resp = await client.get("https://api.ipify.org?format=json")
                if resp.status_code == 200:
                    ip = resp.json().get("ip")
                    log.info(f"[Proxy] Connection SUCCESS! Public IP: [bold green]{ip}[/bold green]")
                    return {"status": "success", "ip": ip, "proxy": proxy_url}
                else:
                    log.warning(f"[Proxy] Status code: {resp.status_code}")
                    return {"status": "failed", "status_code": resp.status_code, "proxy": proxy_url}
        except Exception as e:
            log.error(f"[Proxy] Connection FAILED for {proxy_url}: {e}")
            return {"status": "error", "error": str(e), "proxy": proxy_url}


_proxy_manager_instance: Optional[ProxyManager] = None


def get_proxy_manager() -> ProxyManager:
    """Singleton getter for ProxyManager."""
    global _proxy_manager_instance
    if _proxy_manager_instance is None:
        from ..config import CONFIG
        _proxy_manager_instance = ProxyManager(CONFIG.proxy)
    return _proxy_manager_instance
