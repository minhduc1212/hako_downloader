"""
Adaptive Rate Limiter and Shared Synchronization State (Dynamic Auto-Pacing & Anti-Burst Leaky Bucket)
"""

import asyncio
import random
import time
from dataclasses import dataclass, field
from asyncio import Lock, Event
from typing import Set, Optional, Dict
from ..config import CrawlerEngineConfig
from ..utils.logger import get_logger

log = get_logger("rate_limiter")


class AdaptiveRateLimiter:
    """
    High-precision dynamic rate limiter that synchronizes multiple concurrent workers.
    Actively tracks Cloudflare/Nginx 'x-ratelimit-remaining' and 'retry-after' headers
    to maximize download speed while keeping 429 rate-limit errors at virtually 0%.
    """

    RECOVER_AFTER_SEC = 60.0   # Seconds of stability before rate increases
    STEP_UP = 1.05             # Rate multiplier on sustained success
    STEP_DOWN = 0.65           # Rate reduction on 429

    def __init__(self, max_rps: float = 0.95, min_rps: float = 0.4, jitter: bool = True):
        self.max_rate = max_rps
        self.min_rate = min_rps
        self.current_rate = max_rps
        self._next_allowed_time = 0.0
        self.last_429 = 0.0
        self.consecutive_429 = 0
        self.jitter = jitter
        self.server_remaining_quota: Optional[int] = None
        self.server_reset_epoch: Optional[float] = None
        self._lock = Lock()

    async def acquire(self):
        """
        Acquires an optimized execution slot before making an HTTP request.
        Coordinates multiple workers concurrently to prevent burst collisions on target servers.
        """
        async with self._lock:
            now = time.monotonic()

            # Dynamic pacing based on server's real-time remaining quota
            if self.server_remaining_quota is not None:
                if self.server_remaining_quota <= 1:
                    # Near quota exhaustion -> brief pause for 60s sliding window to roll over
                    target_interval = 4.0
                elif self.server_remaining_quota <= 5:
                    target_interval = 2.0
                elif self.server_remaining_quota <= 15:
                    target_interval = 1.3
                else:
                    target_interval = 1.0 / max(0.1, self.current_rate)
            else:
                target_interval = 1.0 / max(0.1, self.current_rate)

            # Schedule next request time
            target_time = max(now, self._next_allowed_time)
            wait_time = target_time - now
            self._next_allowed_time = target_time + target_interval

            if self.jitter and wait_time < 0.5:
                wait_time += random.uniform(0.05, 0.15)

        if wait_time > 0:
            await asyncio.sleep(wait_time)

    async def update_from_headers(self, headers: Dict[str, str]):
        """Proactively monitor server x-ratelimit headers and adjust pacing."""
        rem_str = headers.get("x-ratelimit-remaining") or headers.get("X-RateLimit-Remaining")
        reset_str = headers.get("x-ratelimit-reset") or headers.get("X-RateLimit-Reset")

        async with self._lock:
            if rem_str is not None:
                try:
                    self.server_remaining_quota = int(rem_str)
                except (ValueError, TypeError):
                    pass

            if reset_str is not None:
                try:
                    self.server_reset_epoch = float(reset_str)
                except (ValueError, TypeError):
                    pass

    async def on_429(self, retry_after_sec: Optional[float] = None):
        """Called when a 429 Too Many Requests response is encountered."""
        async with self._lock:
            self.consecutive_429 += 1
            self.last_429 = time.monotonic()
            new_rate = max(self.min_rate, self.current_rate * self.STEP_DOWN)
            log.warning(
                f"[RateLimiter] 429 detected (#{self.consecutive_429}) -> "
                f"adjusting rate {self.current_rate:.2f} -> {new_rate:.2f} req/s"
            )
            self.current_rate = new_rate
            self.server_remaining_quota = 0
            pause = retry_after_sec if retry_after_sec and retry_after_sec > 0 else 15.0
            self._next_allowed_time = max(self._next_allowed_time, time.monotonic() + pause)

    async def on_success(self):
        """Called on successful requests to gradually recover maximum speed."""
        async with self._lock:
            if self.current_rate >= self.max_rate:
                return
            if time.monotonic() - self.last_429 >= self.RECOVER_AFTER_SEC:
                new_rate = min(self.max_rate, self.current_rate * self.STEP_UP)
                if abs(new_rate - self.current_rate) > 0.02:
                    log.info(
                        f"[RateLimiter] Recovering speed -> "
                        f"{self.current_rate:.2f} -> {new_rate:.2f} req/s"
                    )
                    self.current_rate = new_rate
                    self.consecutive_429 = 0

    def notify_429(self, retry_after_sec: Optional[float] = None):
        """Synchronous wrapper for on_429."""
        asyncio.create_task(self.on_429(retry_after_sec))

    def notify_success(self, headers: Optional[Dict[str, str]] = None):
        """Synchronous wrapper for on_success with header sync."""
        if headers:
            asyncio.create_task(self.update_from_headers(headers))
        asyncio.create_task(self.on_success())


@dataclass
class SharedCrawlState:
    """
    Coordinates state, rate limiting, and global pauses across concurrent workers.
    """

    config: CrawlerEngineConfig
    rate_limiter: AdaptiveRateLimiter = field(init=False)
    proceed: Event = field(default_factory=Event)
    _backoff_lock: Lock = field(default_factory=Lock)
    _backoff_until: float = 0.0
    _backoff_task: Optional[asyncio.Task] = None
    completed_novel_urls: Set[str] = field(default_factory=set)
    completed_chapter_urls: Set[str] = field(default_factory=set)
    lock: Lock = field(default_factory=Lock)

    def __post_init__(self):
        self.rate_limiter = AdaptiveRateLimiter(
            max_rps=self.config.max_rps,
            min_rps=self.config.min_rps,
            jitter=self.config.random_jitter,
        )
        self.proceed.set()

    async def trigger_backoff(self, duration_sec: float):
        """Pause all crawler workers globally for duration_sec with automatic self-resume."""
        async with self._backoff_lock:
            target = time.monotonic() + duration_sec
            if target > self._backoff_until:
                self._backoff_until = target
                self.proceed.clear()
                log.warning(
                    f"[Backoff] Pausing all workers for {duration_sec:.1f}s "
                    f"(resumes at +{duration_sec:.1f}s)"
                )
                if self._backoff_task and not self._backoff_task.done():
                    self._backoff_task.cancel()
                self._backoff_task = asyncio.create_task(self._auto_resume_timer())

    async def _auto_resume_timer(self):
        """Internal self-timer that automatically wakes workers up when backoff expires."""
        try:
            while True:
                remaining = self._backoff_until - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(max(0.1, remaining), 0.5))
            async with self._backoff_lock:
                self._backoff_until = 0.0
                self.proceed.set()
                log.info("[Backoff] Global backoff period ended. Resuming crawler workers.")
        except asyncio.CancelledError:
            pass

    async def wait_for_proceed(self):
        """
        Safe wait for backoff with timeout safeguard.
        Guarantees that workers will NEVER deadlock or hang indefinitely.
        """
        while not self.proceed.is_set():
            remaining = self._backoff_until - time.monotonic()
            if remaining <= 0:
                async with self._backoff_lock:
                    self._backoff_until = 0.0
                    self.proceed.set()
                log.info("[Backoff] Backoff duration reached. Resuming.")
                break
            try:
                await asyncio.wait_for(self.proceed.wait(), timeout=min(max(0.2, remaining), 1.0))
            except asyncio.TimeoutError:
                pass

    async def backoff_watcher(self):
        """Background compatibility task (internal _auto_resume_timer also handles this)."""
        try:
            while True:
                await asyncio.sleep(0.5)
                if not self.proceed.is_set():
                    async with self._backoff_lock:
                        if time.monotonic() >= self._backoff_until:
                            self._backoff_until = 0.0
                            self.proceed.set()
                            log.info("[Backoff] Global backoff period ended. Resuming crawler workers.")
        except asyncio.CancelledError:
            pass

    def get_random_chapter_delay(self) -> float:
        """Calculate small jitter delay between chapters."""
        base = random.uniform(
            self.config.chapter_delay_min,
            self.config.chapter_delay_max,
        )
        return max(0.05, base)

    def get_random_page_delay(self) -> float:
        """Calculate small delay between novels."""
        base = random.uniform(
            self.config.page_delay_min,
            self.config.page_delay_max,
        )
        return max(0.2, base)


_rate_limiter_instance: Optional[AdaptiveRateLimiter] = None


def get_rate_limiter(config: Optional[CrawlerEngineConfig] = None) -> AdaptiveRateLimiter:
    """Singleton getter for AdaptiveRateLimiter."""
    global _rate_limiter_instance
    if _rate_limiter_instance is None:
        if config:
            _rate_limiter_instance = AdaptiveRateLimiter(
                max_rps=config.max_rps,
                min_rps=config.min_rps,
                jitter=config.random_jitter,
            )
        else:
            from ..config import CONFIG
            _rate_limiter_instance = AdaptiveRateLimiter(
                max_rps=CONFIG.crawler.max_rps,
                min_rps=CONFIG.crawler.min_rps,
                jitter=CONFIG.crawler.random_jitter,
            )
    return _rate_limiter_instance
