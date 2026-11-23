"""Persistent local jobs and downloaded-file inventory."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

from naming import DEFAULT_FILE_TEMPLATE, DEFAULT_FOLDER_TEMPLATE, folder_name


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".mkv", ".m4v"}
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "XMediaSaver"
JOBS_FILE = DATA_DIR / "jobs.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
DEFAULT_FOLDER = Path.home() / "Downloads" / "X-미디어"
DEFAULT_SETTINGS = {
    "download_folder": str(DEFAULT_FOLDER),
    "folder_template": DEFAULT_FOLDER_TEMPLATE,
    "file_template": DEFAULT_FILE_TEMPLATE,
    "font_size": 14,
}


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return default


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     suffix=".tmp", delete=False) as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
        temporary = Path(file.name)
    temporary.replace(path)


def migrate_store_python_data() -> None:
    """Bring forward records written to the Microsoft Store Python virtual folder."""
    if not getattr(sys, "frozen", False) or read_json(JOBS_FILE, []):
        return
    packages = DATA_DIR.parent / "Packages"
    candidates = sorted(packages.glob("PythonSoftwareFoundation.Python.*/LocalCache/Local/XMediaSaver"),
                        key=lambda path: (path / "jobs.json").stat().st_mtime
                        if (path / "jobs.json").is_file() else 0, reverse=True)
    for source in candidates:
        jobs = read_json(source / "jobs.json", [])
        if not isinstance(jobs, list) or not jobs:
            continue
        write_json(JOBS_FILE, jobs)
        if not SETTINGS_FILE.is_file():
            settings = read_json(source / "settings.json", {})
            if isinstance(settings, dict):
                write_json(SETTINGS_FILE, settings)
        break


class JobStore:
    def __init__(self) -> None:
        migrate_store_python_data()
        self._inventory_cache: dict[str, tuple[float, list[Path]]] = {}
        self.jobs: list[dict] = read_json(JOBS_FILE, [])
        self.settings: dict = {**DEFAULT_SETTINGS, **read_json(SETTINGS_FILE, {})}
        for job in self.jobs:
            if job.get("status") == "downloading":
                job["status"] = "paused"
            if job.get("preview_state") == "loading":
                job["preview_state"] = "idle"
        self.save_jobs()

    def save_jobs(self) -> None:
        write_json(JOBS_FILE, self.jobs)

    def save_settings(self) -> None:
        write_json(SETTINGS_FILE, self.settings)

    def target_folder(self, handle: str, username: str = "") -> Path:
        name = folder_name(self.settings["folder_template"], handle, username)
        return Path(self.settings["download_folder"]).expanduser() / name

    def create(self, handle: str, media_filter: str = "all", username: str = "",
               target_url: str = "") -> dict:
        job = {
            "id": uuid.uuid4().hex,
            "handle": handle,
            "title": handle,
            "created": datetime.now().astimezone().isoformat(timespec="seconds"),
            "status": "waiting",
            "media_filter": media_filter,
            "target_url": target_url or f"https://x.com/{handle}/media",
            "selected_keys": [],
            "preview": [],
            "profile_image": "",
            "download_folder": str(self.target_folder(handle, username)),
            "file_template": self.settings["file_template"],
            "error": "",
        }
        self.jobs.insert(0, job)
        self.save_jobs()
        return job

    def inventory(self, job: dict) -> list[Path]:
        key = job["id"]
        cached = self._inventory_cache.get(key)
        if cached and time.monotonic() - cached[0] < 30:
            return cached[1]
        root = Path(job["download_folder"])
        if not root.is_dir():
            return []
        files = sorted((p for p in root.rglob("*") if p.is_file() and
                       p.suffix.lower() in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS),
                      key=lambda p: p.stat().st_mtime, reverse=True)
        self._inventory_cache[key] = (time.monotonic(), files)
        return files

    def invalidate_inventory(self, job: dict | None = None) -> None:
        if job:
            self._inventory_cache.pop(job["id"], None)
        else:
            self._inventory_cache.clear()

    def counts(self, job: dict) -> tuple[int, int]:
        files = self.inventory(job)
        return (sum(p.suffix.lower() in IMAGE_EXTENSIONS for p in files),
                sum(p.suffix.lower() in VIDEO_EXTENSIONS for p in files))

    def remove_record(self, job_id: str) -> None:
        self._inventory_cache.pop(job_id, None)
        self.jobs = [job for job in self.jobs if job["id"] != job_id]
        self.save_jobs()
