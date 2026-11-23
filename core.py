"""Input validation and gallery-dl command construction."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlparse


HANDLE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
ALLOWED_BROWSERS = {"프로그램 전용 Chrome", "없음", "Chrome", "Edge", "Firefox", "Brave"}


def gallery_dl_command() -> list[str]:
    """Select the bundled worker in an installed build, Python in source runs."""
    if getattr(sys, "frozen", False):
        helper = Path(sys.executable).with_name("XMediaSaverCLI.exe")
        if not helper.is_file():
            raise FileNotFoundError(f"다운로드 실행 파일을 찾지 못했습니다: {helper}")
        return [str(helper)]
    return [sys.executable, "-m", "gallery_dl"]


def parse_handle(value: str) -> str:
    value = value.strip()
    if value.startswith("@"):
        value = value[1:]
    elif "://" in value:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"
        }:
            raise ValueError("x.com 또는 twitter.com 계정 주소를 입력하세요.")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 1 and not (len(parts) == 2 and parts[1].lower() == "media"):
            raise ValueError("게시물 주소가 아닌 계정 주소를 입력하세요.")
        value = parts[0] if parts else ""
    if not HANDLE.fullmatch(value):
        raise ValueError("계정 아이디는 영문, 숫자, 밑줄 1~15자로 입력하세요.")
    if value.lower() in {"home", "explore", "search", "i", "settings", "notifications", "messages"}:
        raise ValueError("X 계정 아이디를 입력하세요.")
    return value


def profile_handle_from_url(value: str) -> str | None:
    """Return an X handle only for a profile page or one of its tabs."""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"
    }:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if not parts or len(parts) > 2:
        return None
    if len(parts) == 2 and parts[1].lower() not in {
        "media", "with_replies", "highlights", "articles", "likes",
        "followers", "following", "verified_followers"
    }:
        return None
    try:
        return parse_handle(parts[0])
    except ValueError:
        return None


def build_command(handle: str, folder: Path, browser: str, cookie_file: str = "",
                  filename: str = "", target_url: str = "") -> list[str]:
    if not HANDLE.fullmatch(handle):
        raise ValueError("잘못된 계정 아이디입니다.")
    if browser not in ALLOWED_BROWSERS:
        raise ValueError("지원하지 않는 브라우저입니다.")
    if browser != "없음" and cookie_file:
        raise ValueError("브라우저와 쿠키 파일 중 하나만 선택하세요.")

    command = [
        *gallery_dl_command(), "--config-ignore",
        "--directory", str(folder),
        "--download-archive", str(folder / "download-archive.sqlite3"),
        "--option", "extractor.twitter.retweets=false",
        "--option", "extractor.twitter.videos=true",
        "--option", "extractor.twitter.ratelimit=wait",
        "--windows-filenames",
    ]
    if filename:
        command.extend(("--filename", filename))
    command.extend(_auth_args(browser, cookie_file))
    command.append(target_url or f"https://x.com/{handle}/media")
    return command


def _auth_args(browser: str, cookie_file: str) -> list[str]:
    if browser not in ALLOWED_BROWSERS:
        raise ValueError("지원하지 않는 브라우저입니다.")
    if browser != "없음" and cookie_file:
        raise ValueError("브라우저와 쿠키 파일 중 하나만 선택하세요.")
    if browser == "프로그램 전용 Chrome":
        raise ValueError("프로그램 전용 Chrome 로그인 쿠키를 먼저 준비하세요.")
    if cookie_file:
        path = Path(cookie_file)
        if not path.is_file():
            raise ValueError("쿠키 파일을 찾을 수 없습니다.")
        return ["--cookies", str(path)]
    if browser != "없음":
        return ["--cookies-from-browser", browser.lower()]
    return []


def build_check_command(handle: str, browser: str, cookie_file: str = "") -> list[str]:
    """Check one media entry without downloading files."""
    if not HANDLE.fullmatch(handle):
        raise ValueError("잘못된 계정 아이디입니다.")
    return [
        *gallery_dl_command(), "--config-ignore", "--simulate",
        "--range", "1-1", "--option", "extractor.twitter.ratelimit=abort",
        *_auth_args(browser, cookie_file),
        f"https://x.com/{handle}/media",
    ]
