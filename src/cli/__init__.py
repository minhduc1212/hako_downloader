"""
CLI Commands Module
"""

from .commands import (
    handle_crawl_all,
    handle_discover,
    handle_daily,
    handle_schedule,
    handle_crawl,
    handle_crawl_list,
    handle_recrawl,
    handle_recrawl_all,
    handle_retry_failed,
    handle_export,
    handle_export_all,
    handle_stats,
    handle_test_proxy,
)

__all__ = [
    "handle_crawl_all",
    "handle_discover",
    "handle_daily",
    "handle_schedule",
    "handle_crawl",
    "handle_crawl_list",
    "handle_recrawl",
    "handle_recrawl_all",
    "handle_retry_failed",
    "handle_export",
    "handle_export_all",
    "handle_stats",
    "handle_test_proxy",
]
