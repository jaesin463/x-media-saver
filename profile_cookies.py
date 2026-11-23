"""Read persistent cookies belonging to this app's embedded Qt browser profile."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from PySide6.QtCore import QDateTime
from PySide6.QtNetwork import QNetworkCookie


CHROME_EPOCH_OFFSET = 11_644_473_600_000_000


def read_profile_cookies(profile_path: Path) -> list[QNetworkCookie]:
    database = profile_path / "Cookies"
    if not database.is_file():
        return []
    current = int(time.time() * 1_000_000) + CHROME_EPOCH_OFFSET
    cookies = []
    connection = None
    try:
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=2)
        rows = connection.execute(
            "SELECT host_key, name, path, value, expires_utc, is_secure, is_httponly "
            "FROM cookies WHERE value != '' AND (expires_utc = 0 OR expires_utc > ?)",
            (current,),
        )
        for domain, name, path, value, expires, secure, http_only in rows:
            if not isinstance(domain, str) or not (
                domain.lower().lstrip(".") in {"x.com", "twitter.com"}
                or domain.lower().endswith((".x.com", ".twitter.com"))
            ):
                continue
            cookie = QNetworkCookie(name.encode("utf-8"), value.encode("utf-8"))
            cookie.setDomain(domain)
            cookie.setPath(path or "/")
            cookie.setSecure(bool(secure))
            cookie.setHttpOnly(bool(http_only))
            if expires:
                cookie.setExpirationDate(QDateTime.fromSecsSinceEpoch((expires - CHROME_EPOCH_OFFSET) // 1_000_000))
            cookies.append(cookie)
    except sqlite3.Error:
        return []
    finally:
        if connection is not None:
            connection.close()
    return cookies
