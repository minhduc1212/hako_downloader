"""
Configuration Manager for Hako / Docln Crawler System
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List
import yaml


@dataclass
class AppConfig:
    name: str = "Hako Crawler System"
    version: str = "2.0.0"
    data_dir: Path = Path("data")
    output_dir: Path = Path("output")
    log_dir: Path = Path("logs")


@dataclass
class DatabaseConfig:
    db_path: Path = Path("data/hako.db")
    wal_mode: bool = True
    busy_timeout_ms: int = 30000


@dataclass
class CrawlerEngineConfig:
    base_url: str = "https://docln.sbs"
    user_data_dir: str = "./hako_session"
    headless: bool = True
    num_workers: int = 3

    # Adaptive Rate Limiter
    max_rps: float = 0.95
    min_rps: float = 0.4
    chapter_delay_min: float = 0.05
    chapter_delay_max: float = 0.15
    page_delay_min: float = 0.3
    page_delay_max: float = 0.8
    random_jitter: bool = True

    # 429 & Backoff handling
    backoff_429_min: float = 15.0
    backoff_429_max: float = 30.0

    # Timeouts & Retries
    goto_timeout_ms: int = 60000
    selector_timeout_ms: int = 20000
    max_retries: int = 3
    retry_backoff_base: float = 2.0

    # Post-retry
    enable_post_retry: bool = True
    post_retry_max_attempts: int = 3
    post_retry_delay_sec: float = 5.0

    # Resource blocker
    block_resources: bool = True


@dataclass
class MediaConfig:
    download_images: bool = True
    download_covers: bool = True
    download_chapter_illustrations: bool = True
    cover_dir: Path = Path("output/media/covers")
    chapter_img_dir: Path = Path("output/media/chapters")
    max_image_workers: int = 5
    image_timeout_sec: float = 30.0


@dataclass
class ProxyConfig:
    enabled: bool = False
    proxy_type: str = "socks5"  # socks5, http, tor, list
    proxy_url: str = "socks5://127.0.0.1:9050"
    proxy_list_file: Path = Path("config/proxies.txt")
    rotate_interval_requests: int = 25
    proxy_list: List[str] = field(default_factory=list)


@dataclass
class DailyConfig:
    check_interval_hours: int = 6
    latest_updates_max_pages: int = 5
    auto_retry_failed: bool = True
    export_txt_on_complete: bool = True
    feed_url_template: str = (
        "https://docln.sbs/danh-sach?truyendich=1&sangtac=1&convert=1&dangtienhanh=1&tamngung=1&hoanthanh=1&sapxep=capnhat&page={page}"
    )


@dataclass
class ExportConfig:
    auto_export_on_complete: bool = True
    formats: List[str] = field(default_factory=lambda: ["epub", "txt"])
    output_dir: Path = Path("output/novels")


@dataclass
class LoggingConfig:
    level: str = "INFO"
    rich_console: bool = True
    log_file: Path = Path("logs/crawler.log")
    max_bytes: int = 10 * 1024 * 1024  # 10MB
    backup_count: int = 5


@dataclass
class Settings:
    app: AppConfig = field(default_factory=AppConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    crawler: CrawlerEngineConfig = field(default_factory=CrawlerEngineConfig)
    media: MediaConfig = field(default_factory=MediaConfig)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    daily: DailyConfig = field(default_factory=DailyConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    def ensure_directories(self):
        """Create necessary directories if they do not exist."""
        self.app.data_dir.mkdir(parents=True, exist_ok=True)
        self.app.output_dir.mkdir(parents=True, exist_ok=True)
        self.app.log_dir.mkdir(parents=True, exist_ok=True)
        self.database.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.media.cover_dir.mkdir(parents=True, exist_ok=True)
        self.media.chapter_img_dir.mkdir(parents=True, exist_ok=True)
        self.export.output_dir.mkdir(parents=True, exist_ok=True)
        (self.app.output_dir / "novels").mkdir(parents=True, exist_ok=True)


def load_config(config_path: Optional[str] = None) -> Settings:
    """Load configuration from YAML file or return defaults."""
    path = Path(config_path) if config_path else Path("config/config.yaml")
    settings = Settings()

    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            if "app" in data:
                app_data = data["app"]
                settings.app = AppConfig(
                    name=app_data.get("name", settings.app.name),
                    version=app_data.get("version", settings.app.version),
                    data_dir=Path(app_data.get("data_dir", settings.app.data_dir)),
                    output_dir=Path(app_data.get("output_dir", settings.app.output_dir)),
                    log_dir=Path(app_data.get("log_dir", settings.app.log_dir)),
                )

            if "database" in data:
                db_data = data["database"]
                settings.database = DatabaseConfig(
                    db_path=Path(db_data.get("db_path", settings.database.db_path)),
                    wal_mode=db_data.get("wal_mode", settings.database.wal_mode),
                    busy_timeout_ms=db_data.get("busy_timeout_ms", settings.database.busy_timeout_ms),
                )

            if "crawler" in data:
                c_data = data["crawler"]
                settings.crawler = CrawlerEngineConfig(
                    base_url=c_data.get("base_url", settings.crawler.base_url),
                    user_data_dir=c_data.get("user_data_dir", settings.crawler.user_data_dir),
                    headless=c_data.get("headless", settings.crawler.headless),
                    num_workers=c_data.get("num_workers", settings.crawler.num_workers),
                    max_rps=float(c_data.get("max_rps", settings.crawler.max_rps)),
                    min_rps=float(c_data.get("min_rps", settings.crawler.min_rps)),
                    chapter_delay_min=float(c_data.get("chapter_delay_min", settings.crawler.chapter_delay_min)),
                    chapter_delay_max=float(c_data.get("chapter_delay_max", settings.crawler.chapter_delay_max)),
                    page_delay_min=float(c_data.get("page_delay_min", settings.crawler.page_delay_min)),
                    page_delay_max=float(c_data.get("page_delay_max", settings.crawler.page_delay_max)),
                    random_jitter=c_data.get("random_jitter", settings.crawler.random_jitter),
                    backoff_429_min=float(c_data.get("backoff_429_min", settings.crawler.backoff_429_min)),
                    backoff_429_max=float(c_data.get("backoff_429_max", settings.crawler.backoff_429_max)),
                    goto_timeout_ms=int(c_data.get("goto_timeout_ms", settings.crawler.goto_timeout_ms)),
                    selector_timeout_ms=int(c_data.get("selector_timeout_ms", settings.crawler.selector_timeout_ms)),
                    max_retries=int(c_data.get("max_retries", settings.crawler.max_retries)),
                    retry_backoff_base=float(c_data.get("retry_backoff_base", settings.crawler.retry_backoff_base)),
                    enable_post_retry=c_data.get("enable_post_retry", settings.crawler.enable_post_retry),
                    post_retry_max_attempts=int(c_data.get("post_retry_max_attempts", settings.crawler.post_retry_max_attempts)),
                    post_retry_delay_sec=float(c_data.get("post_retry_delay_sec", settings.crawler.post_retry_delay_sec)),
                    block_resources=c_data.get("block_resources", settings.crawler.block_resources),
                )

            if "media" in data:
                m_data = data["media"]
                settings.media = MediaConfig(
                    download_images=m_data.get("download_images", settings.media.download_images),
                    download_covers=m_data.get("download_covers", settings.media.download_covers),
                    download_chapter_illustrations=m_data.get("download_chapter_illustrations", settings.media.download_chapter_illustrations),
                    cover_dir=Path(m_data.get("cover_dir", settings.media.cover_dir)),
                    chapter_img_dir=Path(m_data.get("chapter_img_dir", settings.media.chapter_img_dir)),
                    max_image_workers=int(m_data.get("max_image_workers", settings.media.max_image_workers)),
                    image_timeout_sec=float(m_data.get("image_timeout_sec", settings.media.image_timeout_sec)),
                )

            if "proxy" in data:
                p_data = data["proxy"]
                proxy_list_file = Path(p_data.get("proxy_list_file", settings.proxy.proxy_list_file))
                proxy_list = []
                if proxy_list_file.exists():
                    with open(proxy_list_file, "r", encoding="utf-8") as pf:
                        proxy_list = [line.strip() for line in pf if line.strip() and not line.startswith("#")]

                settings.proxy = ProxyConfig(
                    enabled=p_data.get("enabled", settings.proxy.enabled),
                    proxy_type=p_data.get("proxy_type", settings.proxy.proxy_type),
                    proxy_url=p_data.get("proxy_url", settings.proxy.proxy_url),
                    proxy_list_file=proxy_list_file,
                    rotate_interval_requests=int(p_data.get("rotate_interval_requests", settings.proxy.rotate_interval_requests)),
                    proxy_list=proxy_list,
                )

            if "daily" in data:
                d_data = data["daily"]
                settings.daily = DailyConfig(
                    check_interval_hours=int(d_data.get("check_interval_hours", settings.daily.check_interval_hours)),
                    latest_updates_max_pages=int(d_data.get("latest_updates_max_pages", settings.daily.latest_updates_max_pages)),
                    auto_retry_failed=d_data.get("auto_retry_failed", settings.daily.auto_retry_failed),
                    export_txt_on_complete=d_data.get("export_txt_on_complete", settings.daily.export_txt_on_complete),
                    feed_url_template=d_data.get("feed_url_template", settings.daily.feed_url_template),
                )

            if "export" in data:
                e_data = data["export"]
                settings.export = ExportConfig(
                    auto_export_on_complete=e_data.get("auto_export_on_complete", settings.export.auto_export_on_complete),
                    formats=e_data.get("formats", settings.export.formats),
                    output_dir=Path(e_data.get("output_dir", settings.export.output_dir)),
                )

            if "logging" in data:
                l_data = data["logging"]
                settings.logging = LoggingConfig(
                    level=l_data.get("level", settings.logging.level),
                    rich_console=l_data.get("rich_console", settings.logging.rich_console),
                    log_file=Path(l_data.get("log_file", settings.logging.log_file)),
                    max_bytes=int(l_data.get("max_bytes", settings.logging.max_bytes)),
                    backup_count=int(l_data.get("backup_count", settings.logging.backup_count)),
                )

        except Exception as e:
            print(f"[Warning] Failed to parse config file {path}: {e}. Using defaults.")

    settings.ensure_directories()
    return settings


# Global settings singleton
CONFIG = load_config()
