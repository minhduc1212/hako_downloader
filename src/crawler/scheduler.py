"""
Continuous Daily Scheduler and Daemon Runner
"""

import asyncio
import time
from datetime import datetime, timedelta
from typing import Optional
from ..config import Settings, CONFIG
from .daily_crawler import DailySyncEngine
from ..utils.logger import get_logger, console
from rich.panel import Panel

log = get_logger("scheduler")


class DailyScheduler:
    """
    Runs a continuous daemon loop that triggers DailySyncEngine
    at specified hour intervals (e.g. every 6 hours) or daily.
    """

    def __init__(self, interval_hours: Optional[int] = None, settings: Optional[Settings] = None):
        self.settings = settings or CONFIG
        self.interval_hours = interval_hours or self.settings.daily.check_interval_hours
        self.daily_engine = DailySyncEngine(self.settings)
        self._running = False

    async def start(self, run_immediately: bool = True):
        """Start the background scheduling loop."""
        self._running = True
        log.info(
            f"[Scheduler] Starting daemon scheduler: [bold green]Every {self.interval_hours} hours[/bold green]."
        )

        interval_seconds = self.interval_hours * 3600

        if run_immediately:
            log.info("[Scheduler] Running initial synchronization pass...")
            try:
                await self.daily_engine.run_sync()
            except Exception as e:
                log.error(f"[Scheduler] Initial sync failed: {e}")

        while self._running:
            next_run_time = datetime.now() + timedelta(seconds=interval_seconds)
            log.info(
                f"[Scheduler] Next synchronization scheduled at: "
                f"[bold yellow]{next_run_time.strftime('%Y-%m-%d %H:%M:%S')}[/bold yellow]"
            )

            # Sleep in chunks to allow responsive shutdown
            elapsed = 0
            while elapsed < interval_seconds and self._running:
                chunk = min(60, interval_seconds - elapsed)
                await asyncio.sleep(chunk)
                elapsed += chunk

            if not self._running:
                break

            log.info("[Scheduler] Triggering scheduled sync run...")
            try:
                await self.daily_engine.run_sync()
            except Exception as e:
                log.error(f"[Scheduler] Scheduled sync error: {e}")

        log.info("[Scheduler] Scheduler loop stopped.")

    def stop(self):
        """Signal the scheduler loop to stop."""
        self._running = False
        log.info("[Scheduler] Stop signal received.")
