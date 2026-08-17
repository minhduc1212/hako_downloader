"""
Logging System: Rich Console & Windows-Safe Rotating File Handler
"""

import sys
import os
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme
from rich.panel import Panel
from rich.text import Text

custom_theme = Theme({
    "info": "cyan",
    "warning": "yellow",
    "error": "bold red",
    "critical": "bold white on red",
    "success": "bold green",
    "highlight": "magenta",
    "novel": "bold blue",
    "chapter": "dim cyan",
    "stats": "green",
})

console = Console(theme=custom_theme, color_system="auto")

_logger_initialized = False


class SafeRotatingFileHandler(RotatingFileHandler):
    """
    Windows-safe RotatingFileHandler that closes the underlying file stream before
    rotating and gracefully handles Windows file lock PermissionError.
    """

    def doRollover(self):
        if self.stream:
            try:
                self.stream.flush()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

        try:
            super().doRollover()
        except (PermissionError, OSError):
            # On Windows, if file is temporarily held by another thread/process,
            # truncate cleanly to prevent logging crash
            try:
                if self.baseFilename and Path(self.baseFilename).exists():
                    with open(self.baseFilename, "w", encoding=self.encoding) as f:
                        f.write(f"--- Log rotated after Windows lock recovery ---\n")
            except Exception:
                pass
        finally:
            if not self.stream:
                self.stream = self._open()


def setup_logger(
    log_level: str = "INFO",
    log_file: Optional[Path] = None,
    rich_console: bool = True,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.Logger:
    """Setup and configure the global root logger."""
    global _logger_initialized
    root_logger = logging.getLogger("hako_crawler")
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Properly close and remove existing handlers to prevent Windows file lock conflicts
    for h in root_logger.handlers[:]:
        try:
            h.close()
        except Exception:
            pass
        root_logger.removeHandler(h)

    # Rich Console Handler
    if rich_console:
        rich_handler = RichHandler(
            console=console,
            show_time=True,
            show_level=True,
            show_path=False,
            rich_tracebacks=True,
            tracebacks_show_locals=False,
            markup=True,
        )
        rich_handler.setLevel(getattr(logging, log_level.upper(), logging.INFO))
        root_logger.addHandler(rich_handler)
    else:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        stream_handler.setFormatter(stream_formatter)
        root_logger.addHandler(stream_handler)

    # File Handler
    if log_file:
        log_file_path = Path(log_file)
        log_file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = SafeRotatingFileHandler(
            log_file_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_formatter = logging.Formatter(
            "%(asctime)s [%(levelname)-8s] [%(name)s:%(lineno)d] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(logging.DEBUG)  # Always log DEBUG to file
        root_logger.addHandler(file_handler)

    _logger_initialized = True
    return root_logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Get a logger instance."""
    global _logger_initialized
    if not _logger_initialized:
        setup_logger()
    if name:
        return logging.getLogger(f"hako_crawler.{name}")
    return logging.getLogger("hako_crawler")


def print_banner(title: str = "HAKO NOVEL CRAWLER SYSTEM", subtitle: str = "Daily Auto-Sync Engine"):
    """Display a rich banner at startup."""
    banner_text = Text()
    banner_text.append(f"  {title}\n", style="bold cyan")
    banner_text.append(f"  {subtitle} | Always Up-To-Date SQLite DB\n", style="dim italic")
    banner_text.append("  Anti-Bot Protection • Proxy/Tor • Rich Logging • Dead-Letter Post-Retry", style="dim green")

    panel = Panel(
        banner_text,
        border_style="cyan",
        title="[bold yellow]SYSTEM STATUS[/bold yellow]",
        title_align="left",
        padding=(1, 2),
    )
    console.print(panel)
