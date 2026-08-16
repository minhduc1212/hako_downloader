"""
Novel Page Parser: Extracts Metadata, Volumes, and Chapter Lists with Zero-Miss Guarantee
"""

import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Set
from bs4 import BeautifulSoup
from ..utils.helpers import extract_novel_slug, normalize_url, clean_text


@dataclass
class ParsedChapterRef:
    title: str
    url: str
    publish_date: str = ""
    chapter_index: int = 0


@dataclass
class ParsedVolumeInfo:
    title: str
    url: Optional[str] = None
    vol_index: int = 0
    chapters: List[ParsedChapterRef] = field(default_factory=list)


@dataclass
class ParsedNovelInfo:
    url: str
    slug: str
    title: str
    alternative_titles: str = ""
    author: str = ""
    artist: str = ""
    status: str = "Đang tiến hành"
    novel_type: str = "Truyện dịch"
    cover_url: str = ""
    summary: str = ""
    genres: List[str] = field(default_factory=list)
    total_words: int = 0
    views: int = 0
    likes: int = 0
    bookmarks: int = 0
    rating: float = 0.0
    rating_count: int = 0
    site_last_updated: str = ""
    volumes: List[ParsedVolumeInfo] = field(default_factory=list)


class NovelParser:
    """Parses novel detail pages into structured data objects with zero-miss chapter extraction."""

    @staticmethod
    def parse_novel_html(html: str, novel_url: str, base_url: str = "https://docln.sbs") -> ParsedNovelInfo:
        soup = BeautifulSoup(html, "html.parser")
        slug = extract_novel_slug(novel_url)

        # 1. Title
        title_el = soup.select_one("span.series-name a, span.series-name, h1.series-name, .series-name")
        title = title_el.text.strip() if title_el else slug

        # 2. Novel Type
        type_el = soup.select_one(".series-type span, .series-type")
        novel_type = type_el.text.strip() if type_el else "Truyện dịch"

        # 3. Cover Image
        cover_url = ""
        cover_el = soup.select_one(".series-cover .img-in-ratio, .series-cover div.content, .series-cover div")
        if cover_el:
            style = cover_el.get("style", "")
            match = re.search(r'url\([\'"]?(.*?)[\'"]?\)', style)
            if match:
                cover_url = normalize_url(match.group(1), base_url)
        if not cover_url:
            img_el = soup.select_one(".series-cover img, .series-cover .content img, .series-cover .a6-ratio img")
            if img_el and (img_el.get("src") or img_el.get("data-src")):
                cover_url = normalize_url(img_el.get("src") or img_el.get("data-src"), base_url)

        # 4. Information Items (Author, Artist, Status, Alt names)
        author = ""
        artist = ""
        status = "Đang tiến hành"
        alt_titles = ""

        for item in soup.select("div.series-information .info-item, .series-information .info-item"):
            name_el = item.select_one(".info-name")
            val_el = item.select_one(".info-value")
            if not name_el or not val_el:
                continue

            name = name_el.text.strip().lower()
            val = val_el.text.strip()

            if "tác giả" in name:
                author = val
            elif "họa sĩ" in name or "minh họa" in name:
                artist = val
            elif "tình trạng" in name:
                status = val
            elif "tên khác" in name or "khác" in name:
                alt_titles = val

        # 5. Genres
        genres = []
        for g in soup.select('a[href*="/the-loai/"], .series-gerne-item, .badge-genre, a.genre-item'):
            genre_text = g.text.strip()
            if genre_text and genre_text not in genres:
                genres.append(genre_text)

        # 6. Statistics (words, views, rating, last updated)
        total_words = 0
        views = 0
        likes = 0
        bookmarks = 0
        rating = 0.0
        rating_count = 0
        site_last_updated = ""

        for stat in soup.select(".statistic-item"):
            name_el = stat.select_one(".statistic-name")
            val_el = stat.select_one(".statistic-value")
            if not name_el or not val_el:
                continue

            s_name = name_el.text.strip().lower()
            s_val = val_el.text.strip()

            if "số từ" in s_name or "từ" in s_name:
                num_match = re.sub(r"[^\d]", "", s_val)
                total_words = int(num_match) if num_match else 0
            elif "lượt xem" in s_name or "xem" in s_name:
                num_match = re.sub(r"[^\d]", "", s_val)
                views = int(num_match) if num_match else 0
            elif "theo dõi" in s_name or "bookmark" in s_name:
                num_match = re.sub(r"[^\d]", "", s_val)
                bookmarks = int(num_match) if num_match else 0
            elif "đánh giá" in s_name:
                r_match = re.search(r"([\d\.,]+)\s*/\s*(\d+)", s_val)
                if r_match:
                    r_str = r_match.group(1).replace(",", ".")
                    try:
                        rating = float(r_str)
                    except ValueError:
                        rating = 0.0
                    rating_count = int(r_match.group(2))
            elif "lần cuối" in s_name or "cập nhật" in s_name:
                site_last_updated = s_val.replace("\r", " ").replace("\n", " ").strip()

        # 7. Summary
        summary_el = soup.select_one(".summary-content, .series-summary .content, .summary-wrapper .content")
        summary = clean_text(summary_el.text) if summary_el else ""

        # 8. Volumes and Chapters Extraction
        volumes: List[ParsedVolumeInfo] = []
        vol_sections = soup.select("section.volume-list, div.volume-list")
        seen_chapter_urls: Set[str] = set()
        global_chapter_idx = 0

        if vol_sections:
            for v_idx, vol in enumerate(vol_sections, 1):
                vol_title_el = vol.select_one("header.sear-head, header.title-item, .sect-title, .sect-header, span.sect-title")
                vol_title = vol_title_el.text.strip() if vol_title_el else f"Quyển {v_idx}"

                vol_info = ParsedVolumeInfo(
                    title=vol_title,
                    vol_index=v_idx,
                )

                chap_els = vol.select("ul.list-chapters .chapter-name a, ul.list-chapters a")
                date_els = vol.select("ul.list-chapters .chapter-time")

                for c_idx, chap_a in enumerate(chap_els):
                    href = chap_a.get("href", "")
                    if not href:
                        continue
                    full_ch_url = normalize_url(href, base_url)
                    if full_ch_url in seen_chapter_urls:
                        continue

                    seen_chapter_urls.add(full_ch_url)
                    global_chapter_idx += 1
                    c_title = chap_a.text.strip() or f"Chương {global_chapter_idx}"
                    c_date = date_els[c_idx].text.strip() if c_idx < len(date_els) else ""

                    vol_info.chapters.append(
                        ParsedChapterRef(
                            title=c_title,
                            url=full_ch_url,
                            publish_date=c_date,
                            chapter_index=global_chapter_idx,
                        )
                    )

                if vol_info.chapters:
                    volumes.append(vol_info)

        # ── Zero-Miss Safety Sweep ──
        # Check all links on the page for any chapter URLs matching /c\d+ that might have been missed
        all_ch_links = soup.find_all("a", href=re.compile(r"/c\d+-[^/]+"))
        missed_chapters = []

        for a in all_ch_links:
            href = a.get("href", "")
            if not href:
                continue
            full_ch_url = normalize_url(href, base_url)
            if full_ch_url not in seen_chapter_urls:
                seen_chapter_urls.add(full_ch_url)
                global_chapter_idx += 1
                missed_title = a.text.strip() or f"Chương {global_chapter_idx}"
                missed_chapters.append(
                    ParsedChapterRef(
                        title=missed_title,
                        url=full_ch_url,
                        publish_date="",
                        chapter_index=global_chapter_idx,
                    )
                )

        if missed_chapters:
            if volumes:
                volumes[-1].chapters.extend(missed_chapters)
            else:
                fallback_vol = ParsedVolumeInfo(
                    title="Quyển 1",
                    vol_index=1,
                    chapters=missed_chapters,
                )
                volumes.append(fallback_vol)

        # If still no volumes found, create empty volume container
        if not volumes:
            volumes.append(ParsedVolumeInfo(title="Quyển 1", vol_index=1, chapters=[]))

        return ParsedNovelInfo(
            url=novel_url,
            slug=slug,
            title=title,
            alternative_titles=alt_titles,
            author=author,
            artist=artist,
            status=status,
            novel_type=novel_type,
            cover_url=cover_url,
            summary=summary,
            genres=genres,
            total_words=total_words,
            views=views,
            likes=likes,
            bookmarks=bookmarks,
            rating=rating,
            rating_count=rating_count,
            site_last_updated=site_last_updated,
            volumes=volumes,
        )
