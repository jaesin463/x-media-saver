"""X target parsing, preview metadata, and local download matching."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from core import parse_handle
from desktop_store import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS


STATUS = re.compile(r"^/([A-Za-z0-9_]{1,15})/status/(\d+)(?:/.*)?$")


def parse_target(value: str) -> tuple[str, str]:
    value = value.strip()
    if "://" in value:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"
        }:
            raise ValueError("x.com 또는 twitter.com 주소를 입력하세요.")
        match = STATUS.match(parsed.path)
        if match:
            handle = parse_handle(match.group(1))
            return handle, f"https://x.com/{handle}/status/{match.group(2)}"
    handle = parse_handle(value)
    return handle, f"https://x.com/{handle}/media"


def parse_catalog_line(line: str) -> dict | None:
    try:
        data = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, list) or len(data) < 3 or data[0] != 3:
        return None
    url, meta = data[1], data[2]
    if not isinstance(meta, dict):
        return None
    tweet_id = str(meta.get("tweet_id") or "")
    num = meta.get("num")
    if not tweet_id.isdigit() or not isinstance(num, int):
        return None
    extension = str(meta.get("extension") or "").lower()
    kind = "video" if extension in {ext[1:] for ext in VIDEO_EXTENSIONS} else "image"
    if kind == "image" and extension not in {ext[1:] for ext in IMAGE_EXTENSIONS}:
        return None
    user = meta.get("user") if isinstance(meta.get("user"), dict) else {}
    author = meta.get("author") if isinstance(meta.get("author"), dict) else {}
    return {
        "key": f"{tweet_id}:{num}", "tweet_id": tweet_id, "num": num,
        "url": url, "kind": kind, "extension": extension,
        "media_type": str(meta.get("type") or ""), "poster_url": "",
        "text": str(meta.get("content") or "")[:240],
        "date": str(meta.get("date") or "")[:19],
        "profile_name": str(user.get("nick") or author.get("nick") or ""),
        "profile_image": str(user.get("profile_image") or author.get("profile_image") or ""),
        "post_url": f"https://x.com/{user.get('name') or author.get('name') or 'i'}/status/{tweet_id}",
    }


def downloaded_keys(folders: list[Path]) -> set[str]:
    """Use gallery-dl archives, with a filename fallback for older downloads."""
    keys: set[str] = set()
    for folder in set(folders):
        archive = folder / "download-archive.sqlite3"
        archive_found = False
        if archive.is_file():
            try:
                with sqlite3.connect(f"file:{archive.as_posix()}?mode=ro", uri=True) as db:
                    for (entry,) in db.execute("SELECT entry FROM archive WHERE entry LIKE 'twitter%'"):
                        match = re.fullmatch(r"twitter(\d+)_\d+_(\d+)", entry)
                        if match:
                            keys.add(f"{match.group(1)}:{int(match.group(2))}")
                            archive_found = True
            except (sqlite3.Error, OSError):
                pass
        if folder.is_dir() and not archive_found:
            for path in folder.rglob("*"):
                if path.suffix.lower() not in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS:
                    continue
                match = re.search(r"(?:^|\D)(\d{15,20})_(\d+)(?:\D|$)", path.stem)
                if match:
                    keys.add(f"{match.group(1)}:{int(match.group(2))}")
    return keys


def selection_filters(keys: set[str], max_length: int = 7000) -> list[str]:
    """Split exact media selections into Windows command-line-safe expressions."""
    posts: dict[str, set[int]] = defaultdict(set)
    for key in keys:
        tweet_id, _, number = key.partition(":")
        if tweet_id.isdigit() and number.isdigit():
            posts[tweet_id].add(int(number))
    terms = [f"(tweet_id == {tweet_id} and num in {tuple(sorted(nums))!r})"
             for tweet_id, nums in sorted(posts.items())]
    filters: list[str] = []
    current = ""
    for term in terms:
        if current and len(current) + len(term) + 4 > max_length:
            filters.append(current)
            current = ""
        current = term if not current else current + " or " + term
    if current:
        filters.append(current)
    return filters
