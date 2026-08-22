"""
Helper functions for URL parsing, string sanitization, and formatting
"""

import re
import hashlib
from typing import Optional
from urllib.parse import urlparse, urljoin


def sanitize_filename(name: str, max_length: int = 120) -> str:
    """Sanitize string to be safe for filenames on Windows/Linux/macOS."""
    if not name:
        return "untitled"
    # Remove forbidden characters: \ / : * ? " < > |
    sanitized = re.sub(r'[\\/*?:"<>|]', "", name)
    # Replace multiple whitespaces/newlines with a single space
    sanitized = re.sub(r'\s+', " ", sanitized).strip()
    # Truncate to max_length
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].rstrip()
    return sanitized or "untitled"


def extract_novel_slug(url: str) -> str:
    """Extract novel slug from URL. E.g. https://docln.sbs/truyen/12345-title -> 12345-title"""
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    parts = path.split("/")
    if len(parts) >= 2 and parts[0] in ("truyen", "sang-tac", "convert"):
        return parts[1]
    return path.replace("/", "_") or "novel"


def extract_novel_id(url: str) -> Optional[int]:
    """Extract novel numerical ID if present in slug."""
    slug = extract_novel_slug(url)
    match = re.match(r"^(\d+)", slug)
    if match:
        return int(match.group(1))
    return None


def extract_chapter_id(url: str) -> Optional[int]:
    """Extract chapter numerical ID if present in URL. E.g. /c112903-chuong-1 -> 112903"""
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    parts = path.split("/")
    for part in parts:
        match = re.match(r"^c(\d+)", part)
        if match:
            return int(match.group(1))
    return None


def normalize_url(url: str, base_url: str = "https://docln.sbs") -> str:
    """Ensure URL is fully qualified and normalized."""
    if not url:
        return ""
    url = url.strip()
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("/"):
        return urljoin(base_url, url)
    return urljoin(base_url.rstrip("/") + "/", url)



def calculate_hash(data: bytes) -> str:
    """Calculate SHA256 hash of bytes data."""
    return hashlib.sha256(data).hexdigest()


def format_bytes(size: int) -> str:
    """Format bytes into human readable format."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size) < 1024.0:
            return f"{size:3.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def format_duration(seconds: float) -> str:
    """Format duration in seconds into human-readable string."""
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes > 0:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def clean_text(text: str) -> str:
    """Clean and normalize chapter text content."""
    if not text:
        return ""
    # Normalize carriage returns
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Remove multiple consecutive blank lines (limit to max 2)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()
