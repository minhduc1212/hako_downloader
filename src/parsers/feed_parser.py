"""
Feed and Catalog Parser for Daily Updates and Full Listing Discovery
"""

import re
from dataclasses import dataclass
from typing import List, Optional
from bs4 import BeautifulSoup
from ..utils.helpers import normalize_url


@dataclass
class UpdatedFeedItem:
    novel_url: str
    novel_title: str
    latest_chapter_title: str = ""
    latest_chapter_url: str = ""
    updated_time: str = ""


class FeedParser:
    """Parses update feeds and catalog list pages."""

    @staticmethod
    def parse_latest_updates(html: str, base_url: str = "https://docln.sbs") -> List[UpdatedFeedItem]:
        """Parses the 'Mới cập nhật' page for recent novel updates."""
        soup = BeautifulSoup(html, "html.parser")
        items: List[UpdatedFeedItem] = []

        # Find items in thumb-item-flow or series card listings
        card_selectors = [
            ".thumb-item-flow",
            ".thumb-section-flow .thumb-item-flow",
            ".row.list-content .thumb-item-flow",
            ".series-item",
        ]

        found_cards = []
        for selector in card_selectors:
            elements = soup.select(selector)
            if elements:
                found_cards = elements
                break

        for card in found_cards:
            title_a = card.select_one(".series-title a, .thumb_attr.series-title a, h5.series-title a")
            if not title_a:
                continue

            novel_href = title_a.get("href", "")
            novel_title = title_a.text.strip()
            novel_url = normalize_url(novel_href, base_url)

            # Latest chapter link if present
            chap_a = card.select_one(".chapter-title a, .thumb_attr.chapter-title a")
            latest_chap_title = chap_a.text.strip() if chap_a else ""
            latest_chap_url = normalize_url(chap_a.get("href", ""), base_url) if chap_a else ""

            # Time tag if present
            time_el = card.select_one("time, .time-ago, .thumb_attr .time")
            updated_time = time_el.text.strip() if time_el else ""

            items.append(
                UpdatedFeedItem(
                    novel_url=novel_url,
                    novel_title=novel_title,
                    latest_chapter_title=latest_chap_title,
                    latest_chapter_url=latest_chap_url,
                    updated_time=updated_time,
                )
            )

        # Fallback if card layout not matched: search direct series links
        if not items:
            for a in soup.select("div.thumb_attr.series-title a"):
                href = a.get("href", "")
                if href:
                    items.append(
                        UpdatedFeedItem(
                            novel_url=normalize_url(href, base_url),
                            novel_title=a.text.strip(),
                        )
                    )

        return items

    @staticmethod
    def parse_catalog_novel_urls(html: str, base_url: str = "https://docln.sbs") -> List[str]:
        """Extracts all novel URLs from a catalog page."""
        soup = BeautifulSoup(html, "html.parser")
        urls: List[str] = []

        for a in soup.select("div.thumb_attr.series-title a, .series-title a, .thumb-item-flow a.series-cover"):
            href = a.get("href", "")
            if href:
                full_url = normalize_url(href, base_url)
                if full_url not in urls and ("/truyen/" in full_url or "/sang-tac/" in full_url or "/convert/" in full_url):
                    urls.append(full_url)

        return urls

    @staticmethod
    def extract_max_page(html: str) -> int:
        """Extract total number of pages from pagination component."""
        soup = BeautifulSoup(html, "html.parser")
        max_page = 1

        pagination_links = soup.select(".pagination li a, .paging_item a, .pagination_wrap a")
        for link in pagination_links:
            text = link.text.strip()
            if text.isdigit():
                max_page = max(max_page, int(text))
            else:
                href = link.get("href", "")
                match = re.search(r"page=(\d+)", href)
                if match:
                    max_page = max(max_page, int(match.group(1)))

        return max_page
