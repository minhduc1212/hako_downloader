"""
Database Data Models
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime


@dataclass
class Novel:
    id: Optional[int] = None
    url: str = ""
    slug: str = ""
    title: str = ""
    alternative_titles: str = ""
    author: str = ""
    artist: str = ""
    status: str = "Đang tiến hành"
    novel_type: str = "Truyện dịch"
    cover_url: str = ""
    cover_local_path: Optional[str] = None
    summary: str = ""
    genres: List[str] = field(default_factory=list)
    total_words: int = 0
    views: int = 0
    likes: int = 0
    bookmarks: int = 0
    rating: float = 0.0
    rating_count: int = 0
    site_last_updated: str = ""
    crawl_status: str = "completed"  # pending, crawling, completed, partial, error
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class Volume:
    id: Optional[int] = None
    novel_id: int = 0
    vol_index: int = 0
    title: str = ""
    url: Optional[str] = None
    created_at: Optional[str] = None


@dataclass
class Chapter:
    id: Optional[int] = None
    novel_id: int = 0
    volume_id: Optional[int] = None
    chapter_index: int = 0
    title: str = ""
    url: str = ""
    word_count: int = 0
    publish_date: str = ""
    text_content: str = ""
    html_content: str = ""
    images: List[str] = field(default_factory=list)  # URLs of inline images
    crawl_status: str = "completed"  # pending, completed, failed
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class Image:
    id: Optional[int] = None
    novel_id: Optional[int] = None
    chapter_id: Optional[int] = None
    image_type: str = "chapter_illustration"  # cover, chapter_illustration
    original_url: str = ""
    local_path: Optional[str] = None
    file_size: int = 0
    status: str = "pending"  # pending, downloaded, failed
    error_message: Optional[str] = None
    created_at: Optional[str] = None


@dataclass
class CrawlLog:
    id: Optional[int] = None
    crawl_type: str = "daily"  # daily, manual, recrawl, post_retry
    status: str = "running"  # running, success, failed, partial
    items_checked: int = 0
    items_updated: int = 0
    new_chapters: int = 0
    errors_count: int = 0
    duration_seconds: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[str] = None


@dataclass
class RetryItem:
    id: Optional[int] = None
    item_type: str = "chapter"  # novel, chapter, image
    target_id: Optional[int] = None
    target_url: str = ""
    extra_data: Dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    max_attempts: int = 3
    status: str = "pending"  # pending, processing, resolved, dead
    last_error: Optional[str] = None
    next_retry_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class DBStats:
    total_novels: int = 0
    total_volumes: int = 0
    total_chapters: int = 0
    failed_novels: int = 0
    failed_chapters: int = 0
    total_images: int = 0
    downloaded_images: int = 0
    pending_retries: int = 0
    dead_retries: int = 0
    db_size_bytes: int = 0
