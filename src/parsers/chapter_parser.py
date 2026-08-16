"""
Chapter Content and Inline Image Parser with Noise and Ad Filtering
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional
from bs4 import BeautifulSoup
from ..utils.helpers import normalize_url, clean_text
from ..utils.vietnamese import clean_vietnamese_text


@dataclass
class ParsedChapterContent:
    title: str
    volume_title: str = ""
    word_count: int = 0
    publish_date: str = ""
    text_content: str = ""
    html_content: str = ""
    image_urls: List[str] = field(default_factory=list)


class ChapterParser:
    """Parses rendered chapter HTML into clean text, html, and image lists."""

    # Unwanted advertisement / site notice phrases to filter out
    AD_KEYWORDS = [
        "click vào link khi mua bất kỳ",
        "shopee để ủng hộ",
        "mã free ship",
        "ủng hộ hako",
        "mua bất cứ sản phẩm",
    ]

    @staticmethod
    def parse_chapter_html(
        html: str,
        chapter_url: str,
        base_url: str = "https://docln.sbs",
    ) -> ParsedChapterContent:
        soup = BeautifulSoup(html, "html.parser")

        # Chapter Title
        title_el = soup.select_one(".title-top h4, h4.title-item, .chapter-title")
        title = title_el.text.strip() if title_el else "Chương"

        # Volume / Subtitle
        vol_el = soup.select_one(".title-top h6, h6.title-item")
        volume_title = ""
        publish_date = ""
        word_count = 0

        if vol_el:
            vol_raw = vol_el.text.strip()
            volume_title = vol_raw
            # Parse word count if present: e.g. "Độ dài: 1,054 từ"
            wc_match = re.search(r"Độ dài:\s*([\d\.,]+)\s*từ", vol_raw, re.IGNORECASE)
            if wc_match:
                wc_str = re.sub(r"[^\d]", "", wc_match.group(1))
                if wc_str:
                    word_count = int(wc_str)

            # Parse update date: e.g. "Cập nhật: 14/05/2023" or "1 giờ trước"
            date_match = re.search(r"Cập nhật:\s*([^\n\r]+)", vol_raw, re.IGNORECASE)
            if date_match:
                publish_date = date_match.group(1).strip()

        # Content container
        content_el = soup.select_one("#chapter-content, .chapter-content, .reading-content")

        text_content = ""
        html_content = ""
        image_urls: List[str] = []

        if content_el:
            # 1. Extract all inline images
            for img in content_el.select("img"):
                src = img.get("src") or img.get("data-src")
                if src:
                    full_img_url = normalize_url(src, base_url)
                    if full_img_url not in image_urls:
                        image_urls.append(full_img_url)

            # 2. Clean unwanted elements (display:none, script, style, ads, duplicate titles)
            for el in content_el.select("script, style, noscript, [style*='display: none'], [style*='display:none']"):
                el.decompose()

            for ad_box in content_el.select(".notice-item, .alert, .reading-notice, a[href*='shopee'], a[href*='lazada']"):
                ad_box.decompose()

            # 3. Extract clean paragraphs (<p> elements)
            p_tags = content_el.find_all("p")
            paragraphs: List[str] = []

            if p_tags:
                for p in p_tags:
                    # Ignore p tags inside banners or ads
                    p_str = p.get_text().strip()
                    if not p_str:
                        continue
                    # Check for ad keywords
                    p_lower = p_str.lower()
                    if any(kw in p_lower for kw in ChapterParser.AD_KEYWORDS):
                        continue
                    # Check if p is just the chapter title or volume title repeated
                    if p_str == title or p_str == volume_title:
                        continue
                    paragraphs.append(p_str)
                text_content = "\n\n".join(paragraphs)
            else:
                # Fallback if no <p> tags exist
                raw_text = clean_text(content_el.get_text())
                lines = raw_text.split("\n")
                filtered_lines = []
                for line in lines:
                    line_s = line.strip()
                    if not line_s:
                        continue
                    line_lower = line_s.lower()
                    if any(kw in line_lower for kw in ChapterParser.AD_KEYWORDS):
                        continue
                    if line_s == title or line_s == volume_title:
                        continue
                    filtered_lines.append(line_s)
                text_content = "\n\n".join(filtered_lines)

            html_content = str(content_el)
            text_content = clean_vietnamese_text(text_content)

            # Recalculate word count if not available from header
            if word_count == 0 and text_content:
                word_count = len(text_content.split())

        title = clean_vietnamese_text(title)
        volume_title = clean_vietnamese_text(volume_title)

        return ParsedChapterContent(
            title=title,
            volume_title=volume_title,
            word_count=word_count,
            publish_date=publish_date,
            text_content=text_content,
            html_content=html_content,
            image_urls=image_urls,
        )
