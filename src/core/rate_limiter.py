"""
Adaptive Rate Limiter and Shared Synchronization State
"""

import asyncio
import random
import time
from dataclasses import dataclass, field
from asyncio import Lock, Event
from typing import Set, Optional
from ..config import CrawlerEngineConfig
from ..utils.logger import get_logger

log = get_logger("rate_limiter")


class AdaptiveRateLimiter:
    """
    Token bucket rate limiter that self-adjusts when 429 / rate limit occurs.
    """

    RECOVER_AFTER_SEC = 120.0  # Stable seconds before increasing rate
    STEP_UP = 1.10             # Multiply by 1.10 on recovery
    STEP_DOWN = 0.50           # Halve rate on 429

    def __init__(self, max_rps: float = 2.5, min_rps: float = 0.3, jitter: bool = True):
        self.max_rate = max_rps
        self.min_rate = min_rps
        self.current_rate = max_rps
        self.tokens = max_rps
        self.last_update = time.monotonic()
        self.last_429 = 0.0
        self.consecutive_429 = 0
        self.jitter = jitter
        self._lock = Lock()

    async def acquire(self):
        """Acquire a token before making an HTTP / browser request."""
        await self._lock.acquire()
        wait_time = 0.0
        try:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.tokens = min(self.current_rate, self.tokens + elapsed * self.current_rate)
            self.last_update = now

            if self.tokens >= 1.0:
                self.tokens -= 1.0
            else:
                wait_time = (1.0 - self.tokens) / self.current_rate
                self.tokens = 0.0
        finally:
            self._lock.release()

        if wait_time > 0:
            if self.jitter:
                wait_time += random.uniform(0.05, 0.25)
            await asyncio.sleep(wait_time)

    async def on_429(self):
        """Called when a 429 Too Many Requests response is detected."""
        async with self._lock:
            self.consecutive_429 += 1
            self.last_429 = time.monotonic()
            new_rate = max(self.min_rate, self.current_rate * self.STEP_DOWN)
            log.warning(
                f"[RateLimiter] 429 detected (#{self.consecutive_429}) -> "
                f"reducing rate {self.current_rate:.2f} -> {new_rate:.2f} req/s"
            )
            self.current_rate = new_rate
            self.tokens = min(self.tokens, new_rate)

    async def on_success(self):
        """Called on successful requests to gradually recover speed."""
        async with self._lock:
            if self.current_rate >= self.max_rate:
                return
            if time.monotonic() - self.last_429 >= self.RECOVER_AFTER_SEC:
                new_rate = min(self.max_rate, self.current_rate * self.STEP_UP)
                if new_rate != self.current_rate:
                    log.info(
                        f"[RateLimiter] Recovering speed -> "
                        f"{self.current_rate:.2f} -> {new_rate:.2f} req/s"
                    )
                    self.current_rate = new_rate
                    self.consecutive_429 = 0

    def notify_429(self):
        """Synchronous wrapper for on_429."""
        asyncio.create_task(self.on_429())

    def notify_success(self):
        """Synchronous wrapper for on_success."""
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
        """Pause all crawler workers globally for duration_sec."""
        async with self._backoff_lock:
            target = time.monotonic() + duration_sec
            if target > self._backoff_until:
                self._backoff_until = target
                self.proceed.clear()
                log.warning(
                    f"[Backoff] Pausing all workers for {duration_sec:.1f}s "
                    f"(resumes at +{duration_sec:.1f}s)"
                )

    async def backoff_watcher(self):
        """Background task that wakes workers up once the backoff period expires."""
        while True:
            await asyncio.sleep(1.0)
            if not self.proceed.is_set():
                async with self._backoff_lock:
                    if time.monotonic() >= self._backoff_until:
                        self.proceed.set()
                        log.info("[Backoff] Global backoff period ended. Resuming crawler workers.")

    def get_random_chapter_delay(self) -> float:
        """Calculate randomized chapter delay with jitter."""
        base = random.uniform(
            self.config.chapter_delay_min,
            self.config.chapter_delay_max,
        )
        if self.config.random_jitter:
            base += random.gauss(0, 0.15)
        return max(0.1, base)

    def get_random_page_delay(self) -> float:
        """Calculate randomized page delay with jitter."""
        base = random.uniform(
            self.config.page_delay_min,
            self.config.page_delay_max,
        )
        if self.config.random_jitter:
            base += random.gauss(0, 0.25)
        return max(0.5, base)


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
