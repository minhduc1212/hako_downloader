"""
Database Repository: CRUD Operations for Novels, Volumes, Chapters, Images, Logs, and Retry Queue
"""

import json
import sqlite3
from typing import Optional, List, Set, Dict, Any, Tuple
from pathlib import Path
from .connection import DatabaseManager, get_db_manager
from .models import Novel, Volume, Chapter, Image, CrawlLog, RetryItem, DBStats
from ..utils.logger import get_logger

log = get_logger("repository")


class NovelRepository:
    """Repository handling all database queries and transactions."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db_manager = db_manager or get_db_manager()

    # ─────────────────────────────────────────────────────────────
    #  NOVELS
    # ─────────────────────────────────────────────────────────────
    async def upsert_novel(self, novel: Novel) -> int:
        """Insert or update a novel. Returns the novel id."""
        query = """
        INSERT INTO novels (
            url, slug, title, alternative_titles, author, artist,
            status, novel_type, cover_url, cover_local_path,
            summary, genres, total_words, views, likes, bookmarks,
            rating, rating_count, site_last_updated, crawl_status, error_message, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP
        )
        ON CONFLICT(url) DO UPDATE SET
            slug = excluded.slug,
            title = excluded.title,
            alternative_titles = COALESCE(NULLIF(excluded.alternative_titles, ''), novels.alternative_titles),
            author = COALESCE(NULLIF(excluded.author, ''), novels.author),
            artist = COALESCE(NULLIF(excluded.artist, ''), novels.artist),
            status = excluded.status,
            novel_type = excluded.novel_type,
            cover_url = COALESCE(NULLIF(excluded.cover_url, ''), novels.cover_url),
            cover_local_path = COALESCE(excluded.cover_local_path, novels.cover_local_path),
            summary = COALESCE(NULLIF(excluded.summary, ''), novels.summary),
            genres = excluded.genres,
            total_words = excluded.total_words,
            views = excluded.views,
            likes = excluded.likes,
            bookmarks = excluded.bookmarks,
            rating = excluded.rating,
            rating_count = excluded.rating_count,
            site_last_updated = excluded.site_last_updated,
            crawl_status = excluded.crawl_status,
            error_message = excluded.error_message,
            updated_at = CURRENT_TIMESTAMP
        RETURNING id;
        """
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute(
                query,
                (
                    novel.url,
                    novel.slug,
                    novel.title,
                    novel.alternative_titles,
                    novel.author,
                    novel.artist,
                    novel.status,
                    novel.novel_type,
                    novel.cover_url,
                    novel.cover_local_path,
                    novel.summary,
                    json.dumps(novel.genres, ensure_ascii=False),
                    novel.total_words,
                    novel.views,
                    novel.likes,
                    novel.bookmarks,
                    novel.rating,
                    novel.rating_count,
                    novel.site_last_updated,
                    novel.crawl_status,
                    novel.error_message,
                ),
            )
            row = await cursor.fetchone()
            await conn.commit()
            if row:
                return row["id"]
            return 0

    async def get_novel_by_url(self, url: str) -> Optional[Novel]:
        """Fetch novel by its URL."""
        query = "SELECT * FROM novels WHERE url = ? LIMIT 1;"
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute(query, (url,))
            row = await cursor.fetchone()
            if row:
                return self._row_to_novel(row)
        return None

    async def get_novel_by_id(self, novel_id: int) -> Optional[Novel]:
        """Fetch novel by its numerical ID."""
        query = "SELECT * FROM novels WHERE id = ? LIMIT 1;"
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute(query, (novel_id,))
            row = await cursor.fetchone()
            if row:
                return self._row_to_novel(row)
        return None

    async def get_all_novels(self, limit: int = 1000, offset: int = 0) -> List[Novel]:
        """Fetch list of all novels in database."""
        query = "SELECT * FROM novels ORDER BY updated_at DESC LIMIT ? OFFSET ?;"
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute(query, (limit, offset))
            rows = await cursor.fetchall()
            return [self._row_to_novel(r) for r in rows]

    async def get_novels_count(self) -> int:
        """Count total novels in database."""
        query = "SELECT COUNT(*) as cnt FROM novels;"
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute(query)
            row = await cursor.fetchone()
            return row["cnt"] if row else 0

    async def get_pending_novels_count(self) -> int:
        """Count pending / incomplete novels in database."""
        query = "SELECT COUNT(*) as cnt FROM novels WHERE crawl_status != 'completed';"
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute(query)
            row = await cursor.fetchone()
            return row["cnt"] if row else 0

    async def get_pending_novel_urls(self, limit: Optional[int] = None) -> List[str]:
        """Fetch URLs of novels that are pending or incomplete in SQLite DB."""
        query = "SELECT url FROM novels WHERE crawl_status != 'completed' ORDER BY id ASC"
        if limit and limit > 0:
            query += f" LIMIT {limit}"
        query += ";"
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute(query)
            rows = await cursor.fetchall()
            return [r["url"] for r in rows]

    async def update_novel_cover_local(self, novel_id: int, local_path: str):
        """Update local cover path for novel."""
        query = "UPDATE novels SET cover_local_path = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;"
        async with self.db_manager.get_connection() as conn:
            await conn.execute(query, (local_path, novel_id))
            await conn.commit()

    async def update_novel_status(self, novel_id: int, status: str, error_message: Optional[str] = None):
        """Update crawl status and error message of novel."""
        if error_message is not None:
            query = "UPDATE novels SET crawl_status = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;"
            params = (status, error_message, novel_id)
        else:
            query = "UPDATE novels SET crawl_status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;"
            params = (status, novel_id)
        async with self.db_manager.get_connection() as conn:
            await conn.execute(query, params)
            await conn.commit()

    # ─────────────────────────────────────────────────────────────
    #  VOLUMES
    # ─────────────────────────────────────────────────────────────
    async def upsert_volume(self, volume: Volume) -> int:
        """Insert or update a volume. Returns volume id."""
        query = """
        INSERT INTO volumes (novel_id, vol_index, title, url)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(novel_id, title) DO UPDATE SET
            vol_index = excluded.vol_index,
            url = COALESCE(excluded.url, volumes.url)
        RETURNING id;
        """
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute(
                query,
                (volume.novel_id, volume.vol_index, volume.title, volume.url),
            )
            row = await cursor.fetchone()
            await conn.commit()
            if row:
                return row["id"]
            return 0

    async def get_volumes_for_novel(self, novel_id: int) -> List[Volume]:
        """Get all volumes for a specific novel."""
        query = "SELECT * FROM volumes WHERE novel_id = ? ORDER BY vol_index ASC;"
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute(query, (novel_id,))
            rows = await cursor.fetchall()
            return [
                Volume(
                    id=r["id"],
                    novel_id=r["novel_id"],
                    vol_index=r["vol_index"],
                    title=r["title"],
                    url=r["url"],
                    created_at=r["created_at"],
                )
                for r in rows
            ]

    # ─────────────────────────────────────────────────────────────
    #  CHAPTERS
    # ─────────────────────────────────────────────────────────────
    async def upsert_chapter(self, chapter: Chapter) -> int:
        """Insert or update chapter details. Returns chapter id."""
        query = """
        INSERT INTO chapters (
            novel_id, volume_id, chapter_index, title, url,
            word_count, publish_date, text_content, html_content,
            images_json, crawl_status, error_message, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP
        )
        ON CONFLICT(url) DO UPDATE SET
            novel_id = excluded.novel_id,
            volume_id = COALESCE(excluded.volume_id, chapters.volume_id),
            chapter_index = excluded.chapter_index,
            title = excluded.title,
            word_count = excluded.word_count,
            publish_date = COALESCE(NULLIF(excluded.publish_date, ''), chapters.publish_date),
            text_content = excluded.text_content,
            html_content = excluded.html_content,
            images_json = excluded.images_json,
            crawl_status = excluded.crawl_status,
            error_message = excluded.error_message,
            updated_at = CURRENT_TIMESTAMP
        RETURNING id;
        """
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute(
                query,
                (
                    chapter.novel_id,
                    chapter.volume_id,
                    chapter.chapter_index,
                    chapter.title,
                    chapter.url,
                    chapter.word_count,
                    chapter.publish_date,
                    chapter.text_content,
                    chapter.html_content,
                    json.dumps(chapter.images, ensure_ascii=False),
                    chapter.crawl_status,
                    chapter.error_message,
                ),
            )
            row = await cursor.fetchone()
            await conn.commit()
            if row:
                return row["id"]
            return 0

    async def get_existing_chapter_urls(self, novel_id: int) -> Set[str]:
        """Get set of chapter URLs that have already been crawled successfully for a novel."""
        query = "SELECT url FROM chapters WHERE novel_id = ? AND crawl_status = 'completed';"
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute(query, (novel_id,))
            rows = await cursor.fetchall()
            return {r["url"] for r in rows}

    async def get_all_chapter_urls_in_db(self) -> Set[str]:
        """Get set of all chapter URLs stored in database."""
        query = "SELECT url FROM chapters WHERE crawl_status = 'completed';"
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute(query)
            rows = await cursor.fetchall()
            return {r["url"] for r in rows}

    async def get_chapters_for_novel(self, novel_id: int) -> List[Chapter]:
        """Get all chapters for a novel in ascending order."""
        query = "SELECT * FROM chapters WHERE novel_id = ? ORDER BY chapter_index ASC, id ASC;"
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute(query, (novel_id,))
            rows = await cursor.fetchall()
            return [self._row_to_chapter(r) for r in rows]

    async def get_chapter_by_url(self, url: str) -> Optional[Chapter]:
        """Fetch chapter by its URL."""
        query = "SELECT * FROM chapters WHERE url = ? LIMIT 1;"
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute(query, (url,))
            row = await cursor.fetchone()
            if row:
                return self._row_to_chapter(row)
        return None

    # ─────────────────────────────────────────────────────────────
    #  IMAGES
    # ─────────────────────────────────────────────────────────────
    async def record_image(
        self,
        novel_id: Optional[int],
        chapter_id: Optional[int],
        original_url: str,
        image_type: str = "chapter_illustration",
    ) -> int:
        """Register an image to be downloaded. Returns image ID."""
        query = """
        INSERT INTO images (novel_id, chapter_id, image_type, original_url, status)
        VALUES (?, ?, ?, ?, 'pending')
        ON CONFLICT(original_url) DO UPDATE SET
            novel_id = COALESCE(excluded.novel_id, images.novel_id),
            chapter_id = COALESCE(excluded.chapter_id, images.chapter_id),
            image_type = excluded.image_type
        RETURNING id;
        """
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute(query, (novel_id, chapter_id, image_type, original_url))
            row = await cursor.fetchone()
            await conn.commit()
            if row:
                return row["id"]
            return 0

    async def get_pending_images(self, limit: int = 100) -> List[Image]:
        """Get pending images awaiting download."""
        query = "SELECT * FROM images WHERE status = 'pending' LIMIT ?;"
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute(query, (limit,))
            rows = await cursor.fetchall()
            return [
                Image(
                    id=r["id"],
                    novel_id=r["novel_id"],
                    chapter_id=r["chapter_id"],
                    image_type=r["image_type"],
                    original_url=r["original_url"],
                    local_path=r["local_path"],
                    file_size=r["file_size"],
                    status=r["status"],
                    error_message=r["error_message"],
                    created_at=r["created_at"],
                )
                for r in rows
            ]

    async def mark_image_downloaded(self, image_id: int, local_path: str, file_size: int):
        """Mark an image as downloaded with its local file path."""
        query = "UPDATE images SET status = 'downloaded', local_path = ?, file_size = ?, error_message = NULL WHERE id = ?;"
        async with self.db_manager.get_connection() as conn:
            await conn.execute(query, (local_path, file_size, image_id))
            await conn.commit()

    async def mark_image_failed(self, image_id: int, error_message: str):
        """Mark an image download as failed."""
        query = "UPDATE images SET status = 'failed', error_message = ? WHERE id = ?;"
        async with self.db_manager.get_connection() as conn:
            await conn.execute(query, (error_message, image_id))
            await conn.commit()

    # ─────────────────────────────────────────────────────────────
    #  RETRY QUEUE (DEAD LETTER QUEUE / POST RETRY)
    # ─────────────────────────────────────────────────────────────
    async def add_to_retry_queue(
        self,
        item_type: str,
        target_url: str,
        target_id: Optional[int] = None,
        extra_data: Optional[Dict[str, Any]] = None,
        last_error: Optional[str] = None,
        max_attempts: int = 3,
    ):
        """Add or update a failed item in the retry queue."""
        extra_json = json.dumps(extra_data or {}, ensure_ascii=False)
        query = """
        INSERT INTO retry_queue (
            item_type, target_id, target_url, extra_data,
            attempts, max_attempts, status, last_error, updated_at
        ) VALUES (
            ?, ?, ?, ?, 0, ?, 'pending', ?, CURRENT_TIMESTAMP
        )
        ON CONFLICT(item_type, target_url) DO UPDATE SET
            attempts = retry_queue.attempts + 1,
            status = CASE WHEN retry_queue.attempts + 1 >= retry_queue.max_attempts THEN 'dead' ELSE 'pending' END,
            last_error = excluded.last_error,
            extra_data = excluded.extra_data,
            updated_at = CURRENT_TIMESTAMP;
        """
        async with self.db_manager.get_connection() as conn:
            await conn.execute(
                query,
                (item_type, target_id, target_url, extra_json, max_attempts, last_error),
            )
            await conn.commit()

    async def sync_failed_chapters_to_retry_queue(self):
        """Sync any failed chapters in chapters table into retry_queue to guarantee post-retry execution."""
        query = """
        INSERT INTO retry_queue (item_type, target_url, extra_data, attempts, max_attempts, status, last_error, updated_at)
        SELECT
            'chapter',
            c.url,
            json_object('novel_id', c.novel_id, 'volume_id', c.volume_id, 'chapter_index', c.chapter_index, 'title', c.title),
            0,
            3,
            'pending',
            COALESCE(c.error_message, 'Chapter marked failed'),
            CURRENT_TIMESTAMP
        FROM chapters c
        WHERE c.crawl_status = 'failed'
        ON CONFLICT(item_type, target_url) DO UPDATE SET
            status = CASE WHEN retry_queue.status = 'dead' THEN 'dead' ELSE 'pending' END,
            last_error = COALESCE(excluded.last_error, retry_queue.last_error),
            updated_at = CURRENT_TIMESTAMP;
        """
        async with self.db_manager.get_connection() as conn:
            await conn.execute(query)
            await conn.commit()

    async def get_pending_retries(self, limit: Optional[int] = None) -> List[RetryItem]:
        """Fetch pending items from retry queue. If limit is None or <=0, returns all pending items."""
        query = "SELECT * FROM retry_queue WHERE status = 'pending' AND attempts < max_attempts ORDER BY attempts ASC, id ASC"
        params = ()
        if limit is not None and limit > 0:
            query += " LIMIT ?;"
            params = (limit,)
        else:
            query += ";"

        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [
                RetryItem(
                    id=r["id"],
                    item_type=r["item_type"],
                    target_id=r["target_id"],
                    target_url=r["target_url"],
                    extra_data=json.loads(r["extra_data"] or "{}"),
                    attempts=r["attempts"],
                    max_attempts=r["max_attempts"],
                    status=r["status"],
                    last_error=r["last_error"],
                    next_retry_at=r["next_retry_at"],
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                )
                for r in rows
            ]

    async def resolve_retry(self, retry_id: int):
        """Mark retry item as successfully resolved."""
        query = "UPDATE retry_queue SET status = 'resolved', updated_at = CURRENT_TIMESTAMP WHERE id = ?;"
        async with self.db_manager.get_connection() as conn:
            await conn.execute(query, (retry_id,))
            await conn.commit()

    async def fail_retry(self, retry_id: int, error_msg: str):
        """Increment retry attempts and mark dead if max_attempts reached."""
        query = """
        UPDATE retry_queue SET
            attempts = attempts + 1,
            last_error = ?,
            status = CASE WHEN attempts + 1 >= max_attempts THEN 'dead' ELSE 'pending' END,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?;
        """
        async with self.db_manager.get_connection() as conn:
            await conn.execute(query, (error_msg, retry_id))
            await conn.commit()

    # ─────────────────────────────────────────────────────────────
    #  CRAWL LOGS
    # ─────────────────────────────────────────────────────────────
    async def create_crawl_log(self, crawl_type: str = "daily") -> int:
        """Create a new crawl log entry."""
        query = "INSERT INTO crawl_logs (crawl_type, status) VALUES (?, 'running') RETURNING id;"
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute(query, (crawl_type,))
            row = await cursor.fetchone()
            await conn.commit()
            return row["id"] if row else 0

    async def update_crawl_log(self, log_entry: CrawlLog):
        """Update an existing crawl log entry."""
        if not log_entry.id:
            return
        query = """
        UPDATE crawl_logs SET
            status = ?,
            items_checked = ?,
            items_updated = ?,
            new_chapters = ?,
            errors_count = ?,
            duration_seconds = ?,
            details = ?
        WHERE id = ?;
        """
        async with self.db_manager.get_connection() as conn:
            await conn.execute(
                query,
                (
                    log_entry.status,
                    log_entry.items_checked,
                    log_entry.items_updated,
                    log_entry.new_chapters,
                    log_entry.errors_count,
                    log_entry.duration_seconds,
                    json.dumps(log_entry.details, ensure_ascii=False),
                    log_entry.id,
                ),
            )
            await conn.commit()

    async def get_recent_logs(self, limit: int = 10) -> List[CrawlLog]:
        """Fetch most recent crawl logs."""
        query = "SELECT * FROM crawl_logs ORDER BY id DESC LIMIT ?;"
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute(query, (limit,))
            rows = await cursor.fetchall()
            return [
                CrawlLog(
                    id=r["id"],
                    crawl_type=r["crawl_type"],
                    status=r["status"],
                    items_checked=r["items_checked"],
                    items_updated=r["items_updated"],
                    new_chapters=r["new_chapters"],
                    errors_count=r["errors_count"],
                    duration_seconds=r["duration_seconds"],
                    details=json.loads(r["details"] or "{}"),
                    created_at=r["created_at"],
                )
                for r in rows
            ]

    # ─────────────────────────────────────────────────────────────
    #  METRICS & STATS
    # ─────────────────────────────────────────────────────────────
    async def get_db_stats(self) -> DBStats:
        """Get aggregate metrics about SQLite database."""
        stats = DBStats()
        async with self.db_manager.get_connection() as conn:
            # Novel count
            c = await conn.execute("SELECT COUNT(*) AS c FROM novels;")
            stats.total_novels = (await c.fetchone())["c"]

            # Volume count
            c = await conn.execute("SELECT COUNT(*) AS c FROM volumes;")
            stats.total_volumes = (await c.fetchone())["c"]

            # Chapter count
            c = await conn.execute("SELECT COUNT(*) AS c FROM chapters WHERE crawl_status = 'completed';")
            stats.total_chapters = (await c.fetchone())["c"]

            # Failed counts
            c = await conn.execute("SELECT COUNT(*) AS c FROM novels WHERE crawl_status IN ('error', 'failed');")
            stats.failed_novels = (await c.fetchone())["c"]

            c = await conn.execute("SELECT COUNT(*) AS c FROM chapters WHERE crawl_status = 'failed';")
            stats.failed_chapters = (await c.fetchone())["c"]

            # Image counts
            c = await conn.execute("SELECT COUNT(*) AS c FROM images;")
            stats.total_images = (await c.fetchone())["c"]

            c = await conn.execute("SELECT COUNT(*) AS c FROM images WHERE status = 'downloaded';")
            stats.downloaded_images = (await c.fetchone())["c"]

            # Retry counts
            c = await conn.execute("SELECT COUNT(*) AS c FROM retry_queue WHERE status = 'pending';")
            stats.pending_retries = (await c.fetchone())["c"]

            c = await conn.execute("SELECT COUNT(*) AS c FROM retry_queue WHERE status = 'dead';")
            stats.dead_retries = (await c.fetchone())["c"]

        # DB file size
        if self.db_manager.db_path.exists():
            stats.db_size_bytes = self.db_manager.db_path.stat().st_size

        return stats

    # ─────────────────────────────────────────────────────────────
    #  HELPERS
    # ─────────────────────────────────────────────────────────────
    def _row_to_novel(self, r: sqlite3.Row) -> Novel:
        return Novel(
            id=r["id"],
            url=r["url"],
            slug=r["slug"],
            title=r["title"],
            alternative_titles=r["alternative_titles"] or "",
            author=r["author"] or "",
            artist=r["artist"] or "",
            status=r["status"] or "Đang tiến hành",
            novel_type=r["novel_type"] or "Truyện dịch",
            cover_url=r["cover_url"] or "",
            cover_local_path=r["cover_local_path"],
            summary=r["summary"] or "",
            genres=json.loads(r["genres"] or "[]"),
            total_words=r["total_words"] or 0,
            views=r["views"] or 0,
            likes=r["likes"] or 0,
            bookmarks=r["bookmarks"] or 0,
            rating=r["rating"] or 0.0,
            rating_count=r["rating_count"] or 0,
            site_last_updated=r["site_last_updated"] or "",
            crawl_status=r["crawl_status"] or "completed",
            error_message=r["error_message"] if "error_message" in r.keys() else None,
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )

    def _row_to_chapter(self, r: sqlite3.Row) -> Chapter:
        return Chapter(
            id=r["id"],
            novel_id=r["novel_id"],
            volume_id=r["volume_id"],
            chapter_index=r["chapter_index"],
            title=r["title"],
            url=r["url"],
            word_count=r["word_count"],
            publish_date=r["publish_date"] or "",
            text_content=r["text_content"] or "",
            html_content=r["html_content"] or "",
            images=json.loads(r["images_json"] or "[]"),
            crawl_status=r["crawl_status"],
            error_message=r["error_message"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
