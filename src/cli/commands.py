"""
CLI Command Handlers
"""

import asyncio
import json
from pathlib import Path
from typing import Optional
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn
from ..config import Settings, CONFIG, load_config
from ..database.repository import NovelRepository
from ..crawler.engine import CrawlerEngine
from ..crawler.daily_crawler import DailySyncEngine
from ..crawler.scheduler import DailyScheduler
from ..crawler.catalog_crawler import CatalogCrawler
from ..core.proxy_manager import get_proxy_manager
from ..utils.exporter import NovelExporter
from ..utils.helpers import format_bytes, format_duration
from ..utils.logger import get_logger, console

log = get_logger("cli")


async def handle_crawl_all(
    start_page: int = 1,
    end_page: int = 0,
    workers: Optional[int] = None,
    force: bool = False,
    rescan: bool = False,
    limit: Optional[int] = None,
    config_path: Optional[str] = None,
):
    """Scrape and crawl the entire website novel catalog (all pages) with instant DB resume."""
    settings = load_config(config_path) if config_path else CONFIG
    crawler = CatalogCrawler(settings)
    await crawler.crawl_all_catalog(
        start_page=start_page,
        end_page=end_page,
        workers=workers,
        force_recrawl=force,
        rescan=rescan,
        limit=limit,
    )


async def handle_discover(
    start_page: int = 1,
    end_page: int = 0,
    pages_list: Optional[str] = None,
    config_path: Optional[str] = None,
):
    """Scan catalog pages to register all novel URLs into SQLite DB without crawling chapters."""
    settings = load_config(config_path) if config_path else CONFIG
    crawler = CatalogCrawler(settings)

    specific = None
    if pages_list:
        try:
            specific = [int(p.strip()) for p in pages_list.split(",") if p.strip().isdigit()]
        except Exception:
            specific = None

    urls = await crawler.discover_catalog_urls(
        start_page=start_page,
        end_page=end_page,
        specific_pages=specific,
    )
    console.print(f"[bold green]✓ Discovered and indexed {len(urls)} novels into SQLite database![/bold green]")


async def handle_daily(pages: Optional[int] = None, force: bool = False, config_path: Optional[str] = None):
    """Run daily incremental update crawl."""
    settings = load_config(config_path) if config_path else CONFIG
    engine = DailySyncEngine(settings)
    await engine.run_sync(max_pages=pages, force_all=force)


async def handle_schedule(interval: Optional[int] = None, no_immediate: bool = False, config_path: Optional[str] = None):
    """Start automatic daemon scheduler for daily crawl."""
    settings = load_config(config_path) if config_path else CONFIG
    scheduler = DailyScheduler(interval_hours=interval, settings=settings)
    try:
        await scheduler.start(run_immediately=not no_immediate)
    except (KeyboardInterrupt, asyncio.CancelledError):
        scheduler.stop()


async def handle_crawl(url: str, force: bool = False, config_path: Optional[str] = None):
    """Manually crawl a single novel by URL or slug."""
    settings = load_config(config_path) if config_path else CONFIG
    if not url.startswith("http"):
        url = f"https://docln.sbs/truyen/{url}"

    engine = CrawlerEngine(settings)
    success = await engine.crawl_single_novel(novel_url=url, force_recrawl=force)
    if success:
        console.print(f"[bold green]✓ Successfully crawled novel:[/bold green] {url}")
    else:
        console.print(f"[bold red]✗ Failed to crawl novel:[/bold red] {url}")


async def handle_crawl_list(file_path: str, force: bool = False, config_path: Optional[str] = None):
    """Crawl a list of novels from a JSON or TXT file."""
    path = Path(file_path)
    if not path.exists():
        console.print(f"[bold red]Error: File {file_path} does not exist.[/bold red]")
        return

    urls = []
    if path.suffix == ".json":
        with open(path, "r", encoding="utf-8") as f:
            urls = json.load(f)
    else:
        with open(path, "r", encoding="utf-8") as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    settings = load_config(config_path) if config_path else CONFIG
    engine = CrawlerEngine(settings)
    await engine.crawl_urls(urls=urls, force_recrawl=force)


async def handle_recrawl(url: str, config_path: Optional[str] = None):
    """Force re-crawl an entire novel (updates metadata and checks missing chapters)."""
    await handle_crawl(url=url, force=True, config_path=config_path)


async def handle_recrawl_all(config_path: Optional[str] = None):
    """Re-crawl all existing novels in the database to fetch any missing chapters."""
    settings = load_config(config_path) if config_path else CONFIG
    repo = NovelRepository()
    novels = await repo.get_all_novels(limit=5000)
    if not novels:
        console.print("[yellow]No novels found in database.[/yellow]")
        return

    console.print(f"[bold cyan]Found {len(novels)} novels in database. Checking for updates...[/bold cyan]")
    urls = [n.url for n in novels]
    engine = CrawlerEngine(settings)
    await engine.crawl_urls(urls=urls, force_recrawl=False)


async def handle_retry_failed(config_path: Optional[str] = None):
    """Run post-retry processor on all pending items in retry queue."""
    settings = load_config(config_path) if config_path else CONFIG
    engine = CrawlerEngine(settings)
    resolved = await engine.post_retry_worker.process_pending_retries(engine, batch_size=100)
    console.print(f"[bold green]✓ Post-Retry complete: {resolved} items resolved.[/bold green]")


async def handle_export(
    novel_id: Optional[int] = None,
    url: Optional[str] = None,
    export_format: str = "epub",
    output_dir: Optional[str] = None,
):
    """Export a novel from SQLite database into EPUB or TXT file."""
    repo = NovelRepository()
    novel = None

    if novel_id:
        novel = await repo.get_novel_by_id(novel_id)
    elif url:
        if not url.startswith("http"):
            url = f"https://docln.sbs/truyen/{url}"
        novel = await repo.get_novel_by_url(url)

    if not novel:
        console.print("[bold red]Novel not found in database. Please provide valid --id or --url.[/bold red]")
        return

    out_path_dir = Path(output_dir) if output_dir else CONFIG.app.output_dir / "novels"
    exporter = NovelExporter(repo, out_path_dir)

    if export_format.lower() == "txt":
        path = await exporter.export_novel_txt(novel.id)
    elif export_format.lower() == "all":
        p1 = await exporter.export_novel_epub(novel.id)
        p2 = await exporter.export_novel_txt(novel.id)
        path = p1
    else:
        path = await exporter.export_novel_epub(novel.id)

    if path:
        console.print(f"[bold green]✓ Exported novel to:[/bold green] {path.parent}")


async def handle_export_all(
    export_format: str = "all",
    output_dir: Optional[str] = None,
):
    """Export all completed novels currently in SQLite database into EPUB and/or TXT files."""
    repo = NovelRepository()
    out_path_dir = Path(output_dir) if output_dir else CONFIG.app.output_dir / "novels"
    exporter = NovelExporter(repo, out_path_dir)

    async with repo.db_manager.get_connection() as conn:
        cursor = await conn.execute(
            "SELECT id, title FROM novels WHERE crawl_status = 'completed' ORDER BY id ASC;"
        )
        rows = await cursor.fetchall()

    if not rows:
        console.print("[bold yellow]No completed novels found in database to export.[/bold yellow]")
        return

    console.print(f"[bold cyan]Exporting {len(rows)} completed novels to {out_path_dir}...[/bold cyan]")

    count = 0
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]Exporting novels...", total=len(rows))
        for row in rows:
            nid = row["id"]
            if export_format.lower() in ("epub", "all"):
                await exporter.export_novel_epub(nid)
            if export_format.lower() in ("txt", "all"):
                await exporter.export_novel_txt(nid)
            count += 1
            progress.advance(task)

    console.print(f"[bold green]✓ Successfully exported {count} novels to {out_path_dir.resolve()}![/bold green]")


async def handle_stats():
    """Display a rich dashboard of SQLite database metrics and recent crawl logs."""
    repo = NovelRepository()
    stats = await repo.get_db_stats()
    logs = await repo.get_recent_logs(limit=5)

    # Overview Table
    table = Table(title="Database & Crawler Overview", border_style="cyan", show_header=True)
    table.add_column("Metric", style="bold cyan")
    table.add_column("Count / Value", style="bold green")

    table.add_row("Total Novels in DB", f"{stats.total_novels:,}")
    table.add_row("Total Volumes", f"{stats.total_volumes:,}")
    table.add_row("Total Completed Chapters", f"{stats.total_chapters:,}")
    table.add_row("Total Registered Images", f"{stats.total_images:,}")
    table.add_row("Downloaded Images", f"{stats.downloaded_images:,}")
    table.add_row("Pending Retry Queue", f"[yellow]{stats.pending_retries}[/yellow]" if stats.pending_retries else "0")
    table.add_row("Dead / Exceeded Retries", f"[red]{stats.dead_retries}[/red]" if stats.dead_retries else "0")
    table.add_row("SQLite DB File Size", format_bytes(stats.db_size_bytes))
    console.print(table)

    # Recent Crawl Logs Table
    if logs:
        log_table = Table(title="Recent Crawl Activity Logs", border_style="blue", show_header=True)
        log_table.add_column("ID", style="dim")
        log_table.add_column("Type", style="bold yellow")
        log_table.add_column("Status", style="bold")
        log_table.add_column("Checked", justify="right")
        log_table.add_column("Updated", justify="right")
        log_table.add_column("New Chapters", justify="right")
        log_table.add_column("Errors", justify="right")
        log_table.add_column("Duration", justify="right")
        log_table.add_column("Timestamp", style="dim")

        for l in logs:
            status_style = "green" if l.status == "success" else ("yellow" if l.status == "partial" else "red")
            log_table.add_row(
                str(l.id),
                l.crawl_type.upper(),
                f"[{status_style}]{l.status.upper()}[/{status_style}]",
                str(l.items_checked),
                str(l.items_updated),
                f"+{l.new_chapters}",
                str(l.errors_count),
                f"{l.duration_seconds:.1f}s",
                str(l.created_at),
            )
        console.print(log_table)


async def handle_test_proxy(config_path: Optional[str] = None):
    """Test proxy / Tor connection and print public IP verification."""
    settings = load_config(config_path) if config_path else CONFIG
    pm = get_proxy_manager()
    res = await pm.test_connection()
    if res.get("status") == "success":
        console.print(f"[bold green]✓ Proxy/Tor verified successfully! Public IP: {res.get('ip')}[/bold green]")
    else:
        console.print(f"[bold red]✗ Proxy test failed: {res.get('error') or res}[/bold red]")
