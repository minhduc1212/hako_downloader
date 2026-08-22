"""
Database Connection and Schema Manager with SQLite WAL mode
"""

import sqlite3
from pathlib import Path
from typing import Optional
import aiosqlite
from ..utils.logger import get_logger

log = get_logger("database")

SCHEMA_SQL = """
-- Bảng lưu trữ thông tin chi tiết từng truyện
CREATE TABLE IF NOT EXISTS novels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL,
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    alternative_titles TEXT DEFAULT '',
    author TEXT DEFAULT '',
    artist TEXT DEFAULT '',
    status TEXT DEFAULT 'Đang tiến hành',
    novel_type TEXT DEFAULT 'Truyện dịch',
    cover_url TEXT DEFAULT '',
    cover_local_path TEXT,
    summary TEXT DEFAULT '',
    genres TEXT DEFAULT '[]', -- JSON array
    total_words INTEGER DEFAULT 0,
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    bookmarks INTEGER DEFAULT 0,
    rating REAL DEFAULT 0.0,
    rating_count INTEGER DEFAULT 0,
    site_last_updated TEXT DEFAULT '',
    crawl_status TEXT DEFAULT 'completed',
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_novels_url ON novels(url);
CREATE INDEX IF NOT EXISTS idx_novels_slug ON novels(slug);
CREATE INDEX IF NOT EXISTS idx_novels_updated ON novels(updated_at);
CREATE INDEX IF NOT EXISTS idx_novels_status ON novels(crawl_status);

-- Bảng lưu trữ các Tập / Quyển (Volume)
CREATE TABLE IF NOT EXISTS volumes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    novel_id INTEGER NOT NULL,
    vol_index INTEGER DEFAULT 0,
    title TEXT NOT NULL,
    url TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(novel_id) REFERENCES novels(id) ON DELETE CASCADE,
    UNIQUE(novel_id, title)
);

CREATE INDEX IF NOT EXISTS idx_volumes_novel_id ON volumes(novel_id);

-- Bảng lưu trữ chi tiết từng chương (Chapter)
CREATE TABLE IF NOT EXISTS chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    novel_id INTEGER NOT NULL,
    volume_id INTEGER,
    chapter_index INTEGER DEFAULT 0,
    title TEXT NOT NULL,
    url TEXT UNIQUE NOT NULL,
    word_count INTEGER DEFAULT 0,
    publish_date TEXT DEFAULT '',
    text_content TEXT DEFAULT '',
    html_content TEXT DEFAULT '',
    images_json TEXT DEFAULT '[]', -- JSON array of image URLs
    crawl_status TEXT DEFAULT 'completed', -- completed, failed, pending
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(novel_id) REFERENCES novels(id) ON DELETE CASCADE,
    FOREIGN KEY(volume_id) REFERENCES volumes(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_chapters_novel_id ON chapters(novel_id);
CREATE INDEX IF NOT EXISTS idx_chapters_volume_id ON chapters(volume_id);
CREATE INDEX IF NOT EXISTS idx_chapters_url ON chapters(url);
CREATE INDEX IF NOT EXISTS idx_chapters_status ON chapters(crawl_status);

-- Bảng quản lý tất cả ảnh (Ảnh bìa & Ảnh minh họa trong chương)
CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    novel_id INTEGER,
    chapter_id INTEGER,
    image_type TEXT DEFAULT 'chapter_illustration', -- cover, chapter_illustration
    original_url TEXT UNIQUE NOT NULL,
    local_path TEXT,
    file_size INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending', -- pending, downloaded, failed
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(novel_id) REFERENCES novels(id) ON DELETE CASCADE,
    FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_images_novel_id ON images(novel_id);
CREATE INDEX IF NOT EXISTS idx_images_chapter_id ON images(chapter_id);
CREATE INDEX IF NOT EXISTS idx_images_status ON images(status);

-- Bảng ghi nhận lịch sử và thống kê các lượt crawl
CREATE TABLE IF NOT EXISTS crawl_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    crawl_type TEXT DEFAULT 'daily', -- daily, manual, recrawl, post_retry
    status TEXT DEFAULT 'running', -- running, success, failed, partial
    items_checked INTEGER DEFAULT 0,
    items_updated INTEGER DEFAULT 0,
    new_chapters INTEGER DEFAULT 0,
    errors_count INTEGER DEFAULT 0,
    duration_seconds REAL DEFAULT 0.0,
    details TEXT DEFAULT '{}', -- JSON object
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Bảng Dead Letter Queue / Post-Retry Queue để retry các item bị lỗi
CREATE TABLE IF NOT EXISTS retry_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_type TEXT NOT NULL, -- novel, chapter, image
    target_id INTEGER,
    target_url TEXT NOT NULL,
    extra_data TEXT DEFAULT '{}', -- JSON object
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    status TEXT DEFAULT 'pending', -- pending, processing, resolved, dead
    last_error TEXT,
    next_retry_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(item_type, target_url)
);

CREATE INDEX IF NOT EXISTS idx_retry_status ON retry_queue(status);
"""


class DatabaseManager:
    """Manages SQLite database connections, WAL mode, and schema migrations."""

    def __init__(self, db_path: Path, wal_mode: bool = True, busy_timeout_ms: int = 30000):
        self.db_path = Path(db_path)
        self.wal_mode = wal_mode
        self.busy_timeout_ms = busy_timeout_ms
        self._init_db()

    def _init_db(self):
        """Ensure parent dir exists and apply schema."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            if self.wal_mode:
                cursor.execute("PRAGMA journal_mode = WAL;")
                cursor.execute("PRAGMA synchronous = NORMAL;")
            cursor.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms};")
            cursor.execute("PRAGMA foreign_keys = ON;")
            cursor.executescript(SCHEMA_SQL)
            try:
                cursor.execute("ALTER TABLE novels ADD COLUMN error_message TEXT;")
            except sqlite3.OperationalError:
                pass
            conn.commit()
        log.debug(f"SQLite DB initialized at {self.db_path} (WAL={self.wal_mode})")

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def get_connection(self):
        """Async context manager providing a configured aiosqlite connection."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            if self.wal_mode:
                await conn.execute("PRAGMA journal_mode = WAL;")
                await conn.execute("PRAGMA synchronous = NORMAL;")
            await conn.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms};")
            await conn.execute("PRAGMA foreign_keys = ON;")
            yield conn

    def get_sync_connection(self) -> sqlite3.Connection:
        """Get a synchronous SQLite connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        if self.wal_mode:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms};")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn


_db_manager_instance: Optional[DatabaseManager] = None


def get_db_manager(db_path: Optional[Path] = None) -> DatabaseManager:
    """Singleton getter for DatabaseManager."""
    global _db_manager_instance
    if _db_manager_instance is None:
        from ..config import CONFIG
        target_path = db_path or CONFIG.database.db_path
        _db_manager_instance = DatabaseManager(
            db_path=target_path,
            wal_mode=CONFIG.database.wal_mode,
            busy_timeout_ms=CONFIG.database.busy_timeout_ms,
        )
    return _db_manager_instance
