"""
Hako / Docln Crawler System - Main Entry Point
==============================================
Production-ready, modular light novel crawler with:
- SQLite full schema (Novels, Volumes, Chapters, Images, CrawlLogs, RetryQueue)
- Daily auto-sync engine & continuous daemon scheduler
- Anti-detection (Stealth Browser, Tor/Proxy support, Adaptive Token-Bucket Rate Limiter, Jitter)
- In-run retries & Post-retry Dead-Letter Queue
- Beautiful Rich console UI & structured file logging
"""

import sys
import io
import asyncio
import argparse
from pathlib import Path

# Ensure UTF-8 console output for Windows terminals
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from src.utils.logger import setup_logger, print_banner, console
from src.config import CONFIG, load_config
from src.cli.commands import (
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="Hako / Docln Light Novel Crawler & Daily Auto-Sync System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", type=str, default=None, help="Path to custom config.yaml")
    parser.add_argument("--log-level", type=str, default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Console log level")

    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # 1. crawl-all (Full catalog crawl / bootstrap with instant resume)
    p_all = subparsers.add_parser("crawl-all", help="Crawl the ENTIRE website novel catalog (resumes directly from SQLite DB)")
    p_all.add_argument("--start-page", type=int, default=1, help="Starting catalog page if rescanning (default: 1)")
    p_all.add_argument("--end-page", type=int, default=0, help="Ending catalog page if rescanning (0 = all pages)")
    p_all.add_argument("--workers", "-w", type=int, default=None, help="Number of concurrent workers")
    p_all.add_argument("--force", action="store_true", help="Force re-crawl already completed novels")
    p_all.add_argument("--rescan", action="store_true", help="Force re-scanning web catalog pages before crawling")
    p_all.add_argument("--limit", type=int, default=None, help="Limit number of novels to crawl")

    # 2. discover
    p_disc = subparsers.add_parser("discover", help="Scan catalog pages and index all novel URLs into DB without downloading chapters")
    p_disc.add_argument("--start-page", type=int, default=1, help="Starting catalog page (default: 1)")
    p_disc.add_argument("--end-page", type=int, default=0, help="Ending catalog page (0 = all pages)")
    p_disc.add_argument("--pages", "-p", type=str, default=None, help="Specific comma-separated page numbers to scan (e.g. 72,98,100,101)")

    # 3. daily
    p_daily = subparsers.add_parser("daily", help="Run daily incremental update synchronization")
    p_daily.add_argument("--pages", type=int, default=None, help="Number of 'Mới cập nhật' pages to scan (default: from config)")
    p_daily.add_argument("--force", action="store_true", help="Force sync all checked novels regardless of chapter cache")

    # 4. schedule
    p_sched = subparsers.add_parser("schedule", help="Start continuous daemon scheduler for automated daily crawl")
    p_sched.add_argument("--interval", type=int, default=None, help="Sync interval in hours (default: from config)")
    p_sched.add_argument("--no-immediate", action="store_true", help="Skip the initial sync pass on startup")

    # 5. crawl
    p_crawl = subparsers.add_parser("crawl", help="Manually crawl a single novel")
    p_crawl.add_argument("--url", "-u", type=str, required=True, help="Novel URL or slug (e.g. 15047-nguc-thanh)")
    p_crawl.add_argument("--force", action="store_true", help="Force re-download existing chapters")

    # 6. crawl-list
    p_list = subparsers.add_parser("crawl-list", help="Crawl a list of novels from a file")
    p_list.add_argument("--file", "-f", type=str, required=True, help="Path to .txt or .json file containing novel URLs")
    p_list.add_argument("--force", action="store_true", help="Force re-download existing chapters")

    # 7. recrawl
    p_recrawl = subparsers.add_parser("recrawl", help="Force re-crawl a novel and refresh all its metadata")
    p_recrawl.add_argument("--url", "-u", type=str, required=True, help="Novel URL or slug")

    # 8. recrawl-all
    subparsers.add_parser("recrawl-all", help="Check and re-sync all existing novels in the SQLite database")

    # 9. retry-failed
    subparsers.add_parser("retry-failed", help="Run post-retry processor on pending/failed items in retry queue")

    # 10. export
    p_export = subparsers.add_parser("export", help="Export novel from SQLite database into standard EPUB or TXT file")
    p_export.add_argument("--id", type=int, default=None, help="Novel ID in database")
    p_export.add_argument("--url", "-u", type=str, default=None, help="Novel URL or slug")
    p_export.add_argument("--format", choices=["epub", "txt", "all"], default="epub", help="Output format (epub, txt, or all, default: epub)")
    p_export.add_argument("--out", type=str, default=None, help="Custom output directory")

    # 11. export-all
    p_exp_all = subparsers.add_parser("export-all", help="Batch export all completed novels from SQLite DB into EPUB/TXT")
    p_exp_all.add_argument("--format", choices=["epub", "txt", "all"], default="all", help="Output format (epub, txt, or all, default: all)")
    p_exp_all.add_argument("--out", type=str, default=None, help="Custom output directory")

    # 12. stats
    subparsers.add_parser("stats", help="Display SQLite database statistics and recent crawl logs")

    # 13. test-proxy
    subparsers.add_parser("test-proxy", help="Test proxy/Tor connection and verify public IP")

    return parser


async def main_async():
    parser = build_parser()
    args = parser.parse_args()

    # If no command passed, print help and banner
    if not args.command:
        print_banner()
        parser.print_help()
        sys.exit(0)

    # Initialize settings and logger
    settings = load_config(args.config) if args.config else CONFIG
    setup_logger(
        log_level=args.log_level or settings.logging.level,
        log_file=settings.logging.log_file,
        rich_console=settings.logging.rich_console,
        max_bytes=settings.logging.max_bytes,
        backup_count=settings.logging.backup_count,
    )

    print_banner(subtitle=f"Command: {args.command.upper()}")

    if args.command == "crawl-all":
        await handle_crawl_all(
            start_page=args.start_page,
            end_page=args.end_page,
            workers=args.workers,
            force=args.force,
            rescan=args.rescan,
            limit=args.limit,
            config_path=args.config,
        )
    elif args.command == "discover":
        await handle_discover(
            start_page=args.start_page,
            end_page=args.end_page,
            pages_list=args.pages,
            config_path=args.config,
        )
    elif args.command == "daily":
        await handle_daily(pages=args.pages, force=args.force, config_path=args.config)
    elif args.command == "schedule":
        await handle_schedule(interval=args.interval, no_immediate=args.no_immediate, config_path=args.config)
    elif args.command == "crawl":
        await handle_crawl(url=args.url, force=args.force, config_path=args.config)
    elif args.command == "crawl-list":
        await handle_crawl_list(file_path=args.file, force=args.force, config_path=args.config)
    elif args.command == "recrawl":
        await handle_recrawl(url=args.url, config_path=args.config)
    elif args.command == "recrawl-all":
        await handle_recrawl_all(config_path=args.config)
    elif args.command == "retry-failed":
        await handle_retry_failed(config_path=args.config)
    elif args.command == "export":
        await handle_export(novel_id=args.id, url=args.url, export_format=args.format, output_dir=args.out)
    elif args.command == "export-all":
        await handle_export_all(export_format=args.format, output_dir=args.out)
    elif args.command == "stats":
        await handle_stats()
    elif args.command == "test-proxy":
        await handle_test_proxy(config_path=args.config)


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Process interrupted by user (Ctrl+C). Exiting safely.[/bold yellow]")
        sys.exit(0)
    except Exception as e:
        console.print(f"\n[bold red]Fatal execution error: {e}[/bold red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
