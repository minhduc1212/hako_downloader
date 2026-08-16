"""
Novel Exporter: Generates Clean, Standard EPUB (.epub) and Text (.txt) Files from SQLite Database
"""

import hashlib
import html
from pathlib import Path
from typing import Optional, List, Dict, Any
import ebooklib
from ebooklib import epub
from ..database.models import Novel, Volume, Chapter
from ..database.repository import NovelRepository
from ..utils.helpers import sanitize_filename
from ..utils.logger import get_logger
from ..utils.vietnamese import clean_vietnamese_text

log = get_logger("exporter")

EPUB_CSS = """@charset "utf-8";

body {
    font-family: "Times New Roman", "Segoe UI", Roboto, Arial, sans-serif;
    font-size: 1.05em;
    line-height: 1.65;
    margin: 1em 1.2em;
    color: #111111;
    text-align: justify;
}

h1, h2, h3 {
    font-family: "Times New Roman", "Segoe UI", Roboto, Arial, sans-serif;
    text-align: center;
    margin: 1.2em 0 0.6em 0;
    font-weight: bold;
    color: #111111;
}

h1 { font-size: 1.6em; }
h2 { font-size: 1.3em; }
h3 { font-size: 1.1em; }

.meta {
    text-align: center;
    font-size: 0.9em;
    color: #555555;
    margin-bottom: 2em;
    border-bottom: 1px solid #dddddd;
    padding-bottom: 1em;
}

.meta p {
    margin: 0.3em 0;
    text-indent: 0;
}

.summary {
    margin: 1.5em 0;
    padding: 1em 1.2em;
    background: #f7f7f7;
    border-left: 4px solid #0066cc;
    border-radius: 4px;
}

.summary h3 {
    text-align: left;
    margin-top: 0;
}

p {
    margin: 0.6em 0;
    text-indent: 1.5em;
}

p.center, .center {
    text-align: center;
    text-indent: 0;
}

.date {
    font-size: 0.85em;
    color: #777777;
    font-style: italic;
    text-align: center;
    text-indent: 0;
    margin-bottom: 1.2em;
}

img {
    max-width: 100%;
    height: auto;
    display: block;
    margin: 1.2em auto;
    border-radius: 4px;
}
"""


class NovelExporter:
    """Exports novels from SQLite database into standard EPUB and TXT files."""

    def __init__(self, repository: NovelRepository, output_dir: Path = Path("output/novels")):
        self.repository = repository
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def export_novel_epub(self, novel_id: int) -> Optional[Path]:
        """Export novel to clean, simple, standard EPUB file (.epub)."""
        novel = await self.repository.get_novel_by_id(novel_id)
        if not novel:
            log.warning(f"[Exporter] Novel #{novel_id} not found in database.")
            return None

        volumes = await self.repository.get_volumes_for_novel(novel_id)
        chapters = await self.repository.get_chapters_for_novel(novel_id)

        if not chapters:
            log.warning(f"[Exporter] Novel '{novel.title}' has no chapters to export.")
            return None

        clean_novel_title = clean_vietnamese_text(novel.title)
        clean_summary = clean_vietnamese_text(novel.summary)
        safe_name = sanitize_filename(clean_novel_title)
        out_path = self.output_dir / f"{safe_name}.epub"

        # 1. Initialize Book
        book = epub.EpubBook()
        book.set_identifier(novel.slug or f"hako-{novel.id}")
        book.set_title(clean_novel_title)
        book.set_language("vi")

        # Metadata
        if novel.author:
            book.add_author(clean_vietnamese_text(novel.author))
        if clean_summary:
            book.add_metadata("DC", "description", clean_summary)
        for genre in novel.genres:
            book.add_metadata("DC", "subject", clean_vietnamese_text(genre))
        if novel.url:
            book.add_metadata("DC", "source", novel.url)

        # 2. Add Stylesheet
        style_item = epub.EpubItem(
            uid="style_nav",
            file_name="style/stylesheet.css",
            media_type="text/css",
            content=EPUB_CSS.encode("utf-8"),
        )
        book.add_item(style_item)

        # 3. Add Cover
        if novel.cover_local_path and Path(novel.cover_local_path).exists():
            try:
                cover_bytes = Path(novel.cover_local_path).read_bytes()
                ext = Path(novel.cover_local_path).suffix.lower()
                book.set_cover(f"cover{ext}", cover_bytes)
            except Exception as e:
                log.warning(f"[Exporter] Could not embed cover: {e}")

        # 4. Introduction Page
        info_parts = [f"<h1>{html.escape(clean_novel_title)}</h1>", '<div class="meta">']
        if novel.alternative_titles:
            info_parts.append(f"<p><strong>Tên khác:</strong> {html.escape(clean_vietnamese_text(novel.alternative_titles))}</p>")
        info_parts.append(f"<p><strong>Tác giả:</strong> {html.escape(clean_vietnamese_text(novel.author or 'Chưa rõ'))}</p>")
        if novel.artist:
            info_parts.append(f"<p><strong>Họa sĩ:</strong> {html.escape(clean_vietnamese_text(novel.artist))}</p>")
        if novel.genres:
            info_parts.append(f"<p><strong>Thể loại:</strong> {html.escape(', '.join(novel.genres))}</p>")
        info_parts.append(f"<p><strong>Tình trạng:</strong> {html.escape(novel.status)}</p>")
        if novel.url:
            info_parts.append(f'<p><strong>Nguồn:</strong> <a href="{html.escape(novel.url)}">{html.escape(novel.url)}</a></p>')
        info_parts.append("</div>")

        if clean_summary:
            info_parts.append('<div class="summary">')
            info_parts.append("<h3>Tóm tắt nội dung</h3>")
            for p in clean_summary.split("\n"):
                p_clean = p.strip()
                if p_clean:
                    info_parts.append(f"<p>{html.escape(p_clean)}</p>")
            info_parts.append("</div>")

        intro_page = epub.EpubHtml(title="Giới thiệu", file_name="intro.xhtml", lang="vi")
        intro_page.content = "\n".join(info_parts)
        intro_page.add_item(style_item)
        book.add_item(intro_page)

        # 5. Build Chapters
        vol_map: Dict[Optional[int], str] = {v.id: clean_vietnamese_text(v.title) for v in volumes}
        spine: List[Any] = ["nav", intro_page]
        toc_tree: List[Any] = [intro_page]

        current_vol_id = None
        current_vol_chapters: List[epub.EpubHtml] = []
        current_vol_title = ""
        embedded_images: Dict[str, str] = {}

        for ch_idx, ch in enumerate(chapters, 1):
            clean_ch_title = clean_vietnamese_text(ch.title)
            clean_ch_text = clean_vietnamese_text(ch.text_content)

            # Volume transition
            if ch.volume_id != current_vol_id:
                if current_vol_chapters and current_vol_title:
                    toc_tree.append((epub.Section(current_vol_title), current_vol_chapters))
                    current_vol_chapters = []

                current_vol_id = ch.volume_id
                current_vol_title = vol_map.get(current_vol_id, f"Tập {len(toc_tree)}")

            ch_parts = [f"<h2>{html.escape(clean_ch_title)}</h2>"]
            if ch.publish_date or ch.word_count:
                meta_info = []
                if ch.publish_date:
                    meta_info.append(f"Cập nhật: {html.escape(ch.publish_date)}")
                if ch.word_count:
                    meta_info.append(f"{ch.word_count:,} từ")
                ch_parts.append(f'<p class="date">{" — ".join(meta_info)}</p>')

            # Illustrations
            if ch.images:
                for img_url in ch.images:
                    img_hash = hashlib.md5(img_url.encode("utf-8")).hexdigest()[:12]
                    ext = ".jpg"
                    if ".png" in img_url.lower():
                        ext = ".png"
                    elif ".webp" in img_url.lower():
                        ext = ".webp"

                    epub_img_name = f"images/img_{img_hash}{ext}"
                    local_img_path = Path("output/media/chapters") / sanitize_filename(novel.slug) / f"{img_hash}{ext}"

                    if local_img_path.exists() and epub_img_name not in embedded_images:
                        try:
                            img_data = local_img_path.read_bytes()
                            media_type = "image/png" if ext == ".png" else ("image/webp" if ext == ".webp" else "image/jpeg")
                            img_item = epub.EpubItem(
                                uid=f"img_{img_hash}",
                                file_name=epub_img_name,
                                media_type=media_type,
                                content=img_data,
                            )
                            book.add_item(img_item)
                            embedded_images[epub_img_name] = epub_img_name
                        except Exception as e:
                            log.debug(f"Could not load image {local_img_path}: {e}")

                    if epub_img_name in embedded_images:
                        ch_parts.append(f'<p class="center"><img src="{epub_img_name}" alt="Minh họa" /></p>')
                    else:
                        ch_parts.append(f'<p class="center"><img src="{html.escape(img_url)}" alt="Minh họa" /></p>')

            # Text Paragraphs
            for p in clean_ch_text.split("\n\n"):
                p_clean = p.strip()
                if p_clean:
                    p_formatted = "<br />".join(html.escape(line.strip()) for line in p_clean.split("\n") if line.strip())
                    ch_parts.append(f"<p>{p_formatted}</p>")

            ch_file = f"chap_{ch_idx}.xhtml"
            ch_page = epub.EpubHtml(title=clean_ch_title, file_name=ch_file, lang="vi")
            ch_page.content = "\n".join(ch_parts)
            ch_page.add_item(style_item)
            book.add_item(ch_page)

            spine.append(ch_page)
            current_vol_chapters.append(ch_page)

        if current_vol_chapters:
            if current_vol_title:
                toc_tree.append((epub.Section(current_vol_title), current_vol_chapters))
            else:
                toc_tree.extend(current_vol_chapters)

        # 6. TOC & Navigation
        book.toc = toc_tree
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        book.spine = spine

        # 7. Write EPUB
        epub.write_epub(str(out_path), book, {})
        log.info(f"[Exporter] Successfully exported EPUB: [bold green]{out_path.name}[/bold green] ({len(chapters)} chapters)")
        return out_path

    async def export_novel_txt(self, novel_id: int) -> Optional[Path]:
        """Export novel to clean text file (.txt)."""
        novel = await self.repository.get_novel_by_id(novel_id)
        if not novel:
            log.warning(f"[Exporter] Novel #{novel_id} not found.")
            return None

        chapters = await self.repository.get_chapters_for_novel(novel_id)
        if not chapters:
            log.warning(f"[Exporter] No chapters found for novel {novel.title}.")
            return None

        clean_novel_title = clean_vietnamese_text(novel.title)
        safe_name = sanitize_filename(clean_novel_title)
        out_path = self.output_dir / f"{safe_name}.txt"

        lines: List[str] = []
        lines.append("=" * 60)
        lines.append(f"{clean_novel_title.upper()}")
        if novel.alternative_titles:
            lines.append(f"Tên khác: {clean_vietnamese_text(novel.alternative_titles)}")
        lines.append(f"Tác giả: {clean_vietnamese_text(novel.author or 'Chưa rõ')}")
        if novel.artist:
            lines.append(f"Họa sĩ: {clean_vietnamese_text(novel.artist)}")
        lines.append(f"Thể loại: {', '.join(novel.genres)}")
        lines.append(f"Tình trạng: {novel.status}")
        lines.append(f"Nguồn: {novel.url}")
        lines.append("=" * 60)
        lines.append("")

        if novel.summary:
            lines.append("TÓM TẮT NỘI DUNG:")
            lines.append(clean_vietnamese_text(novel.summary))
            lines.append("")
            lines.append("-" * 60)
            lines.append("")

        for ch in chapters:
            clean_ch_title = clean_vietnamese_text(ch.title)
            clean_ch_text = clean_vietnamese_text(ch.text_content)
            lines.append(f"--- {clean_ch_title.upper()} ---")
            if ch.publish_date:
                lines.append(f"[{ch.publish_date}]")
            lines.append("")
            lines.append(clean_ch_text)
            lines.append("")
            lines.append("-" * 60)
            lines.append("")

        out_path.write_text("\n".join(lines), encoding="utf-8")
        log.info(f"[Exporter] Exported TXT: [bold green]{out_path.name}[/bold green] ({len(chapters)} chapters)")
        return out_path
