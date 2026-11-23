"""Background thumbnail generation for downloaded video files."""

from __future__ import annotations

import hashlib
import queue
import shutil
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from desktop_store import DATA_DIR


THUMB_DIR = DATA_DIR / "thumbnails"


def thumbnail_path(video: Path) -> Path:
    stat = video.stat()
    key = f"{video.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")
    return THUMB_DIR / (hashlib.sha256(key).hexdigest()[:32] + ".jpg")


def ffmpeg_executable() -> str | None:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        return shutil.which("ffmpeg")


class ThumbnailWorker(QThread):
    ready = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.items: queue.Queue[Path | None] = queue.Queue()
        self.queued: set[Path] = set()
        self.cancelled = False
        self.process: subprocess.Popen | None = None

    def enqueue(self, video: Path) -> Path | None:
        try:
            target = thumbnail_path(video)
        except OSError:
            return None
        if not target.is_file() and video not in self.queued:
            self.queued.add(video)
            self.items.put(video)
        return target

    def run(self) -> None:
        ffmpeg = ffmpeg_executable()
        while (video := self.items.get()) is not None:
            if self.cancelled:
                break
            if ffmpeg:
                try:
                    target = thumbnail_path(video)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.TemporaryDirectory(prefix="x-media-thumb-") as work:
                        output = Path(work) / "frame.jpg"
                        self.process = subprocess.Popen([
                            ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", "0.3",
                            "-i", str(video), "-frames:v", "1", "-vf",
                            "scale=640:360:force_original_aspect_ratio=decrease", "-y", str(output),
                        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                        self.process.wait(timeout=25)
                        self.process = None
                        if output.is_file():
                            shutil.copyfile(output, target)
                    if target.is_file():
                        self.ready.emit(str(video))
                except (OSError, subprocess.TimeoutExpired):
                    if self.process and self.process.poll() is None:
                        self.process.kill()
                        self.process.wait()
                finally:
                    self.process = None
            self.queued.discard(video)

    def stop(self) -> None:
        self.cancelled = True
        if self.process and self.process.poll() is None:
            self.process.terminate()
        self.items.put(None)
        self.wait(3000)
