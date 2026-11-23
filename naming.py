"""Validated user-facing folder and filename templates."""

from __future__ import annotations

import re
from datetime import datetime


DEFAULT_FOLDER_TEMPLATE = "{{@UserId}}"
DEFAULT_FILE_TEMPLATE = "{{Tweet Id}}_{{Media Number}}"
FOLDER_TOKENS = ("UserName", "@UserId", "DownloadDate")
FILE_TOKENS = (
    "Text Summary", "Published Time", "UserName", "@UserId",
    "DownloadTime", "Tweet Id", "Media Number",
)
TOKEN = re.compile(r"\{\{([^{}]+)\}\}")
INVALID_WINDOWS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED_WINDOWS = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)


def validate_template(value: str, allowed: tuple[str, ...]) -> str:
    value = value.strip()
    if not value or len(value) > 160:
        raise ValueError("템플릿은 1~160자로 입력하세요.")
    names = TOKEN.findall(value)
    unknown = [name for name in names if name not in allowed]
    if unknown:
        raise ValueError("지원하지 않는 템플릿 항목: " + ", ".join(unknown))
    literal = TOKEN.sub("X", value)
    if "{" in literal or "}" in literal or INVALID_WINDOWS.search(literal):
        raise ValueError("템플릿에는 경로 구분자와 Windows 파일명 금지 문자를 넣을 수 없습니다.")
    if literal.strip(" .") == "" or RESERVED_WINDOWS.match(literal):
        raise ValueError("Windows에서 사용할 수 없는 이름입니다.")
    return value


def safe_component(value: str) -> str:
    value = INVALID_WINDOWS.sub("_", value).strip(" .")
    if not value or RESERVED_WINDOWS.match(value):
        return "미디어"
    return value[:120].rstrip(" .")


def folder_name(template: str, handle: str, username: str = "", date: datetime | None = None) -> str:
    template = validate_template(template, FOLDER_TOKENS)
    now = date or datetime.now().astimezone()
    values = {
        "UserName": username or handle,
        "@UserId": handle,
        "DownloadDate": now.strftime("%Y-%m-%d"),
    }
    return safe_component(TOKEN.sub(lambda m: values[m.group(1)], template))


def filename_format(template: str, date: datetime | None = None) -> str:
    template = validate_template(template, FILE_TOKENS)
    now = date or datetime.now().astimezone()
    values = {
        "Text Summary": "{content:.70}",
        "Published Time": "{date:%Y-%m-%d_%H-%M-%S}",
        "UserName": "{user[nick]}",
        "@UserId": "{user[name]}",
        "DownloadTime": now.strftime("%Y-%m-%d_%H-%M-%S"),
        "Tweet Id": "{tweet_id}",
        "Media Number": "{num}",
    }
    result = TOKEN.sub(lambda m: values[m.group(1)], template)
    if "{{Tweet Id}}" not in template:
        result += "_{tweet_id}"
    if "{{Media Number}}" not in template:
        result += "_{num}"
    return result + ".{extension}"
