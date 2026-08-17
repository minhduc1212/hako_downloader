"""
Chapter Content and Inline Image Parser with Noise/Ad Filtering and Direct Decryption Engine
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional
from bs4 import BeautifulSoup
from ..utils.helpers import normalize_url, clean_text
from ..utils.vietnamese import clean_vietnamese_text
from ..utils.crypto import decrypt_hako_chapter


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
    """Parses raw chapter HTML into clean text, html, and image lists with direct XOR decryption."""

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

        text_content = ""
        html_content = ""
        image_urls: List[str] = []

        # ── 1. Check for Encrypted Chapter Payload (data-c + data-k) ──
        protected_div = soup.find(attrs={"data-c": True, "data-k": True}) or soup.find("div", id="chapter-c-protected")

        if protected_div and protected_div.get("data-c") and protected_div.get("data-k"):
            data_c = protected_div.get("data-c")
            data_k = protected_div.get("data-k")
            decrypted_html = decrypt_hako_chapter(data_c, data_k)

            if decrypted_html:
                c_soup = BeautifulSoup(decrypted_html, "html.parser")

                # Extract images
                for img in c_soup.select("img"):
                    src = img.get("src") or img.get("data-src")
                    if src:
                        full_img_url = normalize_url(src, base_url)
                        if full_img_url not in image_urls:
                            image_urls.append(full_img_url)

                # Extract paragraphs
                p_tags = c_soup.find_all("p")
                paragraphs: List[str] = []
                if p_tags:
                    for p in p_tags:
                        p_str = p.get_text().strip()
                        if not p_str:
                            continue
                        p_lower = p_str.lower()
                        if any(kw in p_lower for kw in ChapterParser.AD_KEYWORDS):
                            continue
                        if p_str == title or p_str == volume_title:
                            continue
                        paragraphs.append(p_str)
                    text_content = "\n\n".join(paragraphs)
                else:
                    text_content = clean_text(c_soup.get_text())

                html_content = decrypted_html

        # ── 2. Fallback: Standard Unprotected Chapter Content ──
        if not text_content:
            content_el = soup.select_one("#chapter-content, .chapter-content, .reading-content")
            if content_el:
                # Extract inline images
                for img in content_el.select("img"):
                    src = img.get("src") or img.get("data-src")
                    if src:
                        full_img_url = normalize_url(src, base_url)
                        if full_img_url not in image_urls:
                            image_urls.append(full_img_url)

                # Clean noise
                for el in content_el.select("script, style, noscript, [style*='display: none'], [style*='display:none']"):
                    el.decompose()
                for ad_box in content_el.select(".notice-item, .alert, .reading-notice, a[href*='shopee'], a[href*='lazada']"):
                    ad_box.decompose()

                p_tags = content_el.find_all("p")
                paragraphs: List[str] = []
                if p_tags:
                    for p in p_tags:
                        p_str = p.get_text().strip()
                        if not p_str:
                            continue
                        p_lower = p_str.lower()
                        if any(kw in p_lower for kw in ChapterParser.AD_KEYWORDS):
                            continue
                        if p_str == title or p_str == volume_title:
                            continue
                        paragraphs.append(p_str)
                    text_content = "\n\n".join(paragraphs)
                else:
                    raw_text = clean_text(content_el.get_text())
                    lines = [line.strip() for line in raw_text.split("\n") if line.strip()]
                    filtered = [l for l in lines if not any(kw in l.lower() for kw in ChapterParser.AD_KEYWORDS) and l != title and l != volume_title]
                    text_content = "\n\n".join(filtered)

                html_content = str(content_el)

        text_content = clean_vietnamese_text(text_content)
        title = clean_vietnamese_text(title)
        volume_title = clean_vietnamese_text(volume_title)

        if word_count == 0 and text_content:
            word_count = len(text_content.split())

        return ParsedChapterContent(
            title=title,
            volume_title=volume_title,
            word_count=word_count,
            publish_date=publish_date,
            text_content=text_content,
            html_content=html_content,
            image_urls=image_urls,
        )
