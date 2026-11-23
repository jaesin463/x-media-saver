"""X media downloader with an embedded login browser and local dashboard."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_WEBVIEW_PLUGIN", "native")

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWebView import QtWebView, QWebView
from shiboken6 import isValid
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QPushButton, QScrollArea, QStackedWidget, QTabBar, QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout, QWidget,
)

from core import build_check_command, build_command, gallery_dl_command, profile_handle_from_url
from desktop_store import DATA_DIR, JobStore, VIDEO_EXTENSIONS
from media_catalog import downloaded_keys, parse_catalog_line, parse_target, selection_filters
from naming import (DEFAULT_FILE_TEMPLATE, DEFAULT_FOLDER_TEMPLATE,
                    FILE_TOKENS, FOLDER_TOKENS, filename_format, folder_name,
                    validate_template)
from theme import apply_theme, load_ui_font, set_button_icon, recolor_button, system_is_dark
from thumbnails import ThumbnailWorker
from webview_session import WebViewSession, configure_webview_debugging


def label(text: str, name: str = "") -> QLabel:
    item = QLabel(text)
    if name:
        item.setObjectName(name)
    item.setWordWrap(True)
    return item


def button(text: str, callback, primary: bool = False, icon_name: str = "") -> QPushButton:
    item = QPushButton(text)
    if primary:
        item.setObjectName("primary")
    if icon_name:
        set_button_icon(item, icon_name)
    item.clicked.connect(lambda _checked=False: callback())
    return item


def panel() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("panel")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(10)
    return frame, layout


class SelectableMediaCard(QFrame):
    toggled = Signal()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.toggled.emit()
        super().mouseReleaseEvent(event)


def clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            clear(item.layout())


class AvatarLabel(QLabel):
    """Crop profile photos to the same circular frame everywhere in the app."""

    def __init__(self, initials: str, size: int) -> None:
        super().__init__()
        self.initials = initials[:1].upper()
        self.photo = QPixmap()
        self.setFixedSize(size, size)

    def setPixmap(self, pixmap: QPixmap) -> None:
        self.photo = pixmap
        self.update()

    def paintEvent(self, event) -> None:
        size = min(self.width(), self.height())
        dark = bool(QApplication.instance().property("darkTheme"))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addEllipse(0, 0, size, size)
        painter.setClipPath(path)
        painter.fillRect(self.rect(), QColor("#23252a" if dark else "#e9ece8"))
        if not self.photo.isNull():
            scaled = self.photo.scaled(size, size, Qt.KeepAspectRatioByExpanding,
                                       Qt.SmoothTransformation)
            painter.drawPixmap((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
        else:
            painter.setPen(QColor("#ffffff" if dark else "#30343a"))
            font = painter.font()
            font.setPixelSize(max(12, int(size * 0.42)))
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignCenter, self.initials)
        painter.setClipping(False)
        painter.setPen(QPen(QColor("#383b3f" if dark else "#dfe2dd"), 1))
        painter.drawEllipse(0, 0, size - 1, size - 1)
        painter.end()


class CommandWorker(QThread):
    output = Signal(str)
    finished_result = Signal(int, str)

    def __init__(self, command: list[str] | list[list[str]], cookie_file: Path, probe: bool = False) -> None:
        super().__init__()
        self.command = command
        self.cookie_file = cookie_file
        self.probe = probe
        self.process: subprocess.Popen[str] | None = None
        self.cancelled = False

    def run(self) -> None:
        result = ""
        code = -1
        try:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            if self.probe:
                self.process = subprocess.Popen(
                    self.command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace", creationflags=flags)
                if self.cancelled:
                    self.process.terminate()
                stdout, stderr = self.process.communicate(timeout=50)
                code = self.process.returncode
                result = (stdout + "\n" + stderr).strip()
            else:
                commands = self.command if self.command and isinstance(self.command[0], list) else [self.command]
                for command in commands:
                    if self.cancelled:
                        break
                    self.process = subprocess.Popen(
                        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace", bufsize=1,
                        creationflags=flags,
                    )
                    assert self.process.stdout is not None
                    for line in self.process.stdout:
                        result = line.strip()
                        self.output.emit(line)
                    code = self.process.wait()
                    if code != 0:
                        break
        except subprocess.TimeoutExpired:
            result = "X의 응답이 50초 안에 오지 않았습니다."
            if self.process and self.process.poll() is None:
                self.process.kill()
                self.process.wait()
        except Exception as exc:
            result = str(exc)
        finally:
            self.cookie_file.unlink(missing_ok=True)
            self.finished_result.emit(code, result)

    def stop(self) -> None:
        self.cancelled = True
        if self.process and self.process.poll() is None:
            self.process.terminate()


class PreviewWorker(QThread):
    batch = Signal(list)
    finished_result = Signal(int, str)

    def __init__(self, target_url: str, cookie_file: Path, folders: list[Path]) -> None:
        super().__init__()
        self.target_url = target_url
        self.cookie_file = cookie_file
        self.folders = folders
        self.process: subprocess.Popen[str] | None = None
        self.cancelled = False

    def run(self) -> None:
        batch: list[dict] = []
        pending_video: dict | None = None
        current_tweet = ""
        media_number = 0
        error = ""
        code = -1
        try:
            downloaded = downloaded_keys(self.folders)
            command = [*gallery_dl_command(), "--config-ignore", "--dump-json",
                       "--option", "output.jsonl=true",
                       "--option", "extractor.twitter.retweets=false",
                       "--option", "extractor.twitter.videos=true",
                       "--option", "extractor.twitter.previews=true",
                       "--option", "extractor.twitter.ratelimit=wait",
                       "--cookies", str(self.cookie_file), self.target_url]
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            self.process = subprocess.Popen(command, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, text=True,
                                            encoding="utf-8", errors="replace", bufsize=1,
                                            creationflags=flags)
            assert self.process.stdout is not None
            for line in self.process.stdout:
                if self.cancelled:
                    self.process.terminate()
                    break
                item = parse_catalog_line(line)
                if item:
                    if pending_video:
                        if item["media_type"] == "preview" and item["tweet_id"] == pending_video["tweet_id"]:
                            pending_video["poster_url"] = item["url"]
                            item = None
                        batch.append(pending_video)
                        pending_video = None
                    if not item or item["media_type"] == "preview":
                        continue
                    if item["tweet_id"] != current_tweet:
                        current_tweet = item["tweet_id"]
                        media_number = 0
                    media_number += 1
                    item["num"] = media_number
                    item["key"] = f"{current_tweet}:{media_number}"
                    item["downloaded"] = item["key"] in downloaded
                    if item["kind"] == "video":
                        pending_video = item
                    else:
                        batch.append(item)
                    if len(batch) >= 200:
                        self.batch.emit(batch)
                        batch = []
                elif line.strip() and not line.lstrip().startswith(("[", "{")):
                    error = line.strip()[-500:]
            if pending_video:
                batch.append(pending_video)
            if batch:
                self.batch.emit(batch)
            code = self.process.wait()
        except Exception as exc:
            error = str(exc)
        finally:
            self.cookie_file.unlink(missing_ok=True)
            self.finished_result.emit(code, error)

    def stop(self) -> None:
        self.cancelled = True
        if self.process and self.process.poll() is None:
            self.process.terminate()


class DesktopApp(QMainWindow):
    def __init__(self, debug_port: int) -> None:
        super().__init__()
        self.store = JobStore()
        self.worker: CommandWorker | None = None
        self.preview_worker: PreviewWorker | None = None
        self.preview_job_id: str | None = None
        self.preview_seen: set[str] = set()
        self.preview_selected: set[str] = set()
        self.pending_preview_job: dict | None = None
        self.preview_cancel_requested = False
        self.active_job: dict | None = None
        self.pending_job: dict | None = None
        self.browser_ready = False
        self.session = WebViewSession(debug_port)
        self.cookies: list[dict] = []
        self.legacy_import_attempted = False
        self.current_page = "dashboard"
        self.current_job_id: str | None = None
        self.filter_value = "전체"
        self.search_value = ""
        self.gallery_filter = "전체"
        self.gallery_view = "card"
        self.gallery_limit = 120
        self.preview_progress_label: QLabel | None = None
        self.preview_summary_label: QLabel | None = None
        self.profile_names: dict[str, str] = {}
        self.image_cache: dict[str, bytes] = {}
        self.network = QNetworkAccessManager(self)
        self.thumbnail_worker = ThumbnailWorker()
        self.thumbnail_worker.ready.connect(lambda _path: self._refresh()
                                            if self.current_page in {"dashboard", "gallery"} else None)
        self.thumbnail_worker.start()
        self.detected_profile: str | None = None
        self.last_detected_url = ""
        self.setWindowTitle("X 미디어 저장기")
        self.resize(1400, 900)
        self.setMinimumSize(960, 660)
        icon = Path(__file__).parent / "web" / "assets" / "icons" / "app-icon.png"
        if icon.is_file():
            self.setWindowIcon(QIcon(str(icon)))
        self._make_window()
        self._navigate("dashboard")
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._periodic_refresh)
        self.timer.start(2500)
        self.theme_timer = QTimer(self)
        self.theme_timer.timeout.connect(self._sync_system_theme)
        self.theme_timer.start(2500)
        self.selection_save_timer = QTimer(self)
        self.selection_save_timer.setSingleShot(True)
        self.selection_save_timer.timeout.connect(self.store.save_jobs)
        self.browser_location_timer = QTimer(self)
        self.browser_location_timer.timeout.connect(self._poll_browser_location)
        self.browser_location_timer.start(800)

    def _sync_system_theme(self) -> None:
        dark = system_is_dark()
        app = QApplication.instance()
        if dark != bool(app.property("darkTheme")):
            apply_theme(app, dark, self, self.store.settings.get("font_size", 14))

    @staticmethod
    def _is_x_domain(domain: str) -> bool:
        host = domain.lower().lstrip(".")
        return host in {"x.com", "twitter.com"} or host.endswith((".x.com", ".twitter.com"))

    def _has_x_login(self) -> bool:
        names = {cookie["name"] for cookie in self.cookies
                 if cookie.get("domain", "").lstrip(".") == "x.com"}
        return {"auth_token", "ct0"}.issubset(names)

    def _refresh_cookies(self, bootstrap: bool = False) -> bool:
        self.cookies = self.session.get_cookies()
        if not self.legacy_import_attempted and bootstrap:
            self.legacy_import_attempted = True
            if not self._has_x_login():
                imported = self.session.import_legacy_cookies(DATA_DIR / "EmbeddedBrowser")
                if imported:
                    self.cookies = self.session.get_cookies()
                    if self._has_x_login():
                        self.web.reload()
        return self._has_x_login()

    def _cookie_file(self) -> Path:
        try:
            authenticated = self._refresh_cookies()
        except Exception as exc:
            raise ValueError("X 브라우저 로그인 세션을 읽지 못했습니다: " + str(exc)) from exc
        if not authenticated:
            raise ValueError("앱 안의 X 브라우저에서 로그인한 뒤 '로그인 확인'을 누르세요.")
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt",
                                         prefix="x-media-", delete=False) as file:
            file.write("# Netscape HTTP Cookie File\n")
            for cookie in self.cookies:
                domain = cookie.get("domain", "").lower()
                if not self._is_x_domain(domain):
                    continue
                if not domain.startswith("."):
                    domain = "." + domain
                expires = int(cookie.get("expires", 0))
                if cookie.get("session") or expires <= 0:
                    expires = int(datetime.now().timestamp()) + 3600
                value = cookie.get("value", "").replace("\t", "").replace("\n", "")
                name = cookie.get("name", "")
                secure = "TRUE" if cookie.get("secure") else "FALSE"
                file.write(f"{domain}\tTRUE\t{cookie.get('path') or '/'}\t{secure}\t{expires}\t{name}\t{value}\n")
            return Path(file.name)

    def _make_window(self) -> None:
        base = QWidget()
        base.setObjectName("workspace")
        self.setCentralWidget(base)
        outer = QVBoxLayout(base)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        top = QFrame()
        top.setObjectName("topbar")
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(24, 9, 24, 9)
        top_layout.setSpacing(8)
        brand_icon = QLabel()
        logo = QPixmap(str(Path(__file__).parent / "web" / "assets" / "icons" / "app-icon.png"))
        brand_icon.setPixmap(logo.scaled(30, 30, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        top_layout.addWidget(brand_icon)
        top_layout.addWidget(label("X 미디어 저장기", "section"))
        top_layout.addSpacing(26)
        self.nav_buttons: dict[str, QPushButton] = {}
        for key, title in (("dashboard", "대시보드"), ("tasks", "로컬 작업"),
                           ("browser", "X 브라우저"), ("settings", "설정")):
            item = QPushButton(title)
            item.setObjectName("nav")
            item.setProperty("active", "false")
            set_button_icon(item, key)
            item.clicked.connect(lambda _=False, page=key: self._navigate(page))
            top_layout.addWidget(item)
            self.nav_buttons[key] = item
        top_layout.addStretch()
        self.auth_badge = label("● 로그인 필요", "authBadge")
        self.auth_badge.setProperty("authenticated", "false")
        self.sidebar_status = self.auth_badge
        top_layout.addWidget(self.auth_badge)
        top_layout.addSpacing(12)
        top_layout.addWidget(button("새 작업", self._new_job_dialog, icon_name="plus"))
        outer.addWidget(top)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        quick = QFrame()
        quick.setObjectName("topbar")
        quick_layout = QHBoxLayout(quick)
        quick_layout.setContentsMargins(24, 9, 24, 9)
        self.account_input = QLineEdit()
        self.account_input.setPlaceholderText("X 계정 URL · 게시물 링크 · @아이디")
        self.account_input.returnPressed.connect(self._create_and_preview)
        quick_layout.addWidget(self.account_input, 1)
        quick_layout.addWidget(button("링크 붙여넣기", self._paste_url, icon_name="paste"))
        self.direct_button = button("다운로드", self._download_post_immediately, icon_name="download")
        self.direct_button.setEnabled(False)
        self.account_input.textChanged.connect(self._sync_direct_button)
        quick_layout.addWidget(self.direct_button)
        quick_layout.addWidget(button("미디어 확인", self._create_and_preview, True, "image"))
        right_layout.addWidget(quick)
        heading = QWidget()
        heading_row = QHBoxLayout(heading)
        heading_row.setContentsMargins(26, 18, 26, 2)
        self.title_label = label("대시보드", "pageTitle")
        heading_row.addWidget(self.title_label)
        heading_row.addStretch()
        right_layout.addWidget(heading)

        self.pages = QStackedWidget()
        self.dashboard_page, self.dashboard_layout = self._scroll_page()
        self.tasks_page, self.tasks_layout = self._scroll_page()
        self.settings_page, self.settings_layout = self._scroll_page()
        self.gallery_page, self.gallery_layout = self._scroll_page()
        self.browser_page = self._make_browser_page()
        for page in (self.dashboard_page, self.tasks_page, self.browser_page,
                     self.settings_page, self.gallery_page):
            self.pages.addWidget(page)
        right_layout.addWidget(self.pages, 1)
        outer.addWidget(right, 1)
        self.statusBar().showMessage("앱 내부 X 브라우저에서 로그인한 뒤 계정 URL을 입력하세요.")
        if self._has_x_login():
            self._set_auth_state(True)
            self.browser_hint.setText("저장된 X 로그인 쿠키를 찾았습니다. 프로필을 연 뒤 로그인 확인을 누르면 접근을 시험합니다.")

    def _scroll_page(self) -> tuple[QScrollArea, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("page")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(26, 26, 26, 26)
        layout.setSpacing(18)
        layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(content)
        return scroll, layout

    def _set_auth_state(self, authenticated: bool) -> None:
        self.auth_badge.setText("● 로그인" if authenticated else "● 로그인 필요")
        self.auth_badge.setProperty("authenticated", "true" if authenticated else "false")
        self.auth_badge.style().unpolish(self.auth_badge)
        self.auth_badge.style().polish(self.auth_badge)

    def _sync_direct_button(self, _text: str = "") -> None:
        try:
            _handle, target_url = parse_target(self.account_input.text())
            enabled = "/status/" in target_url
        except ValueError:
            enabled = False
        self.direct_button.setEnabled(enabled)

    def _download_post_immediately(self) -> None:
        try:
            handle, target_url = parse_target(self.account_input.text())
            if "/status/" not in target_url:
                return
            if self.worker:
                QMessageBox.information(self, "진행 중", "현재 다운로드가 끝난 뒤 다시 시작하세요.")
                return
            if not self.browser_ready:
                QMessageBox.information(self, "로그인 필요", "X 브라우저에서 로그인을 완료한 뒤 다시 눌러주세요.")
                return
            cookie_file = self._cookie_file()
            username = self.profile_names.get(handle.lower(), "")
            folder = self.store.target_folder(handle, username)
            folder.mkdir(parents=True, exist_ok=True)
            current_template = self.store.settings["file_template"]
            job = next((item for item in self.store.jobs
                        if item["handle"].lower() == handle.lower()
                        and item.get("target_url") == target_url
                        and item["download_folder"] == str(folder)
                        and item.get("file_template", DEFAULT_FILE_TEMPLATE) == current_template), None)
            job = job or self.store.create(handle, "all", username, target_url)
            command = build_command(handle, folder, "없음", str(cookie_file),
                                    filename_format(current_template), target_url)
        except (ValueError, OSError) as exc:
            if "cookie_file" in locals():
                cookie_file.unlink(missing_ok=True)
            QMessageBox.warning(self, "다운로드 시작 실패", str(exc))
            return
        job["status"] = "downloading"
        job["error"] = ""
        self.store.save_jobs()
        self.active_job = job
        self.recent_log = []
        self.worker = CommandWorker(command, cookie_file)
        self.worker.output.connect(self._download_output)
        self.worker.finished_result.connect(self._download_finished)
        self.worker.start()
        self.statusBar().showMessage(f"게시물 다운로드 중 · @{handle} · X 브라우저를 계속 이용할 수 있습니다.")
        self._refresh()

    def _make_browser_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 18, 22, 18)
        toolbar = QHBoxLayout()
        toolbar.addWidget(button("", self._browser_back, icon_name="back"))
        toolbar.addWidget(button("", self._browser_forward, icon_name="forward"))
        toolbar.addWidget(button("", self._browser_reload, icon_name="refresh"))
        self.address = QLineEdit("https://x.com/home")
        self.address.returnPressed.connect(self._go_address)
        toolbar.addWidget(self.address, 1)
        toolbar.addWidget(button("이동", self._go_address))
        toolbar.addWidget(button("X 로그인", self._open_login, True, "login"))
        toolbar.addWidget(button("로그인 확인", self._check_login, icon_name="check"))
        layout.addLayout(toolbar)
        self.browser_hint = label("이 브라우저에서 X에 로그인하고 영상을 재생할 수 있습니다. 비밀번호는 앱에 저장하지 않습니다.", "muted")
        layout.addWidget(self.browser_hint)
        self.browser_layout = layout
        self.web: QWebView | None = None
        return page

    def _create_browser_view(self) -> None:
        if self.web is not None or self.current_page != "browser":
            return
        self.web = QWebView()
        self.web.urlChanged.connect(self._browser_url_changed)
        self.web.loadingChanged.connect(self._browser_loaded)
        self.browser_layout.addWidget(QWidget.createWindowContainer(self.web), 1)
        QTimer.singleShot(250, lambda: self.web.setUrl(QUrl("https://x.com/home"))
                          if self.web is not None and self.web.url().isEmpty() else None)

    def _browser_back(self) -> None:
        self.web.goBack()

    def _browser_forward(self) -> None:
        self.web.goForward()

    def _browser_reload(self) -> None:
        self.web.reload()

    def _open_login(self) -> None:
        self.web.setUrl(QUrl("https://x.com/i/flow/login"))

    def _go_address(self) -> None:
        address = self.address.text().strip()
        if not address.startswith(("https://", "http://")):
            address = "https://" + address
        self.web.setUrl(QUrl(address))

    def _browser_url_changed(self, url: QUrl) -> None:
        self._apply_browser_url(url.toString())

    def _poll_browser_location(self) -> None:
        if self.current_page == "browser" and self.web is not None:
            self.web.runJavaScript("window.location.href", self._apply_browser_url)

    def _apply_browser_url(self, value) -> None:
        if not isinstance(value, str) or not value.startswith(("https://", "http://")):
            return
        if not self.address.hasFocus() and self.address.text() != value:
            self.address.setText(value)
        handle = profile_handle_from_url(value)
        post = re.match(r"^https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/status/(\d+)", value)
        if post:
            handle = post.group(1)
            target = f"https://x.com/{handle}/status/{post.group(2)}"
        elif handle:
            target = "@" + handle
        else:
            self.detected_profile = None
            return
        self.detected_profile = handle
        if target != self.account_input.text() or value != self.last_detected_url:
            self.account_input.setText(target)
            self.last_detected_url = value
            self.statusBar().showMessage(f"열린 X 주소를 다운로드 대상에 입력했습니다: {target}", 5000)
            QTimer.singleShot(1200, lambda h=handle: self._capture_profile_name(h))

    def _browser_loaded(self, info) -> None:
        if info.status().name == "Failed":
            self.browser_hint.setText("페이지를 열지 못했습니다: " + info.errorString())
        if info.status().name != "Succeeded":
            return
        self.browser_ready = True
        try:
            if self._refresh_cookies(bootstrap=True):
                self._set_auth_state(True)
        except Exception as exc:
            self.browser_hint.setText("로그인 상태 확인 실패: " + str(exc))
        if self.detected_profile:
            handle = self.detected_profile
            QTimer.singleShot(1200, lambda: self._capture_profile_name(handle))
        if self.pending_job:
            job = self.pending_job
            self.pending_job = None
            QTimer.singleShot(0, lambda: self._start_job(job))
        if self.pending_preview_job:
            job = self.pending_preview_job
            self.pending_preview_job = None
            QTimer.singleShot(0, lambda: self._load_preview(job))

    def _capture_profile_name(self, handle: str) -> None:
        if self.detected_profile != handle:
            return
        script = "document.querySelector('[data-testid=\"UserName\"]')?.innerText?.split('\\n')[0] || ''"
        self.web.runJavaScript(script, lambda value: self._store_profile_name(handle, value))

    def _store_profile_name(self, handle: str, value) -> None:
        if self.detected_profile == handle and isinstance(value, str) and value.strip():
            self.profile_names[handle.lower()] = value.strip()[:80]

    def _check_login(self) -> None:
        self.browser_hint.setText("X 로그인 세션 확인 중…")
        self._finish_cookie_check()

    def _finish_cookie_check(self) -> None:
        try:
            authenticated = self._refresh_cookies()
        except Exception as exc:
            self.browser_hint.setText("로그인 상태를 읽지 못했습니다: " + str(exc))
            return
        if authenticated:
            self._set_auth_state(True)
            self.browser_hint.setText("X 로그인 쿠키를 찾았습니다. 계정 URL을 입력한 뒤 다시 누르면 실제 미디어 접근을 확인합니다.")
            self.statusBar().showMessage("X 쿠키 발견. 계정 접근 검사가 남았습니다.")
            account = self.account_input.text().strip()
            if account and not self.worker:
                try:
                    handle, _target_url = parse_target(account)
                    cookie_file = self._cookie_file()
                    command = build_check_command(handle, "없음", str(cookie_file))
                    self.worker = CommandWorker(command, cookie_file, probe=True)
                    self.worker.finished_result.connect(self._probe_finished)
                    self.worker.start()
                    self._set_auth_state(True)
                    self.browser_hint.setText(f"@{handle} 미디어 접근 확인 중…")
                except ValueError as exc:
                    self.browser_hint.setText(str(exc))
        else:
            self._set_auth_state(False)
            self.browser_hint.setText("X 로그인 쿠키가 없습니다. 앱 내부 브라우저에서 로그인을 완료하세요.")

    def _probe_finished(self, code: int, output: str) -> None:
        if self.worker:
            self.worker.wait(1000)
        self.worker = None
        if code == 0:
            self._set_auth_state(True)
            self.browser_hint.setText("X 계정 미디어 접근 확인됨. 이제 저장을 시작할 수 있습니다.")
        elif "AuthRequired" in output:
            self._set_auth_state(False)
            self.browser_hint.setText("X가 접근을 거부했습니다. 브라우저에서 로그인을 다시 확인하세요.")
        else:
            self._set_auth_state(False)
            self.browser_hint.setText("접근 확인 실패: " + output[-240:])

    def _navigate(self, page: str, job_id: str | None = None) -> None:
        self.current_page = page
        if job_id:
            self.current_job_id = job_id
        indexes = {"dashboard": 0, "tasks": 1, "browser": 2, "settings": 3, "gallery": 4}
        titles = {"dashboard": "대시보드", "tasks": "로컬 작업", "browser": "X 브라우저",
                  "settings": "설정", "gallery": "작업 상세"}
        self.pages.setCurrentIndex(indexes[page])
        if page == "browser" and self.web is None:
            QTimer.singleShot(200, self._create_browser_view)
        self.title_label.setText(titles[page])
        for key, nav in self.nav_buttons.items():
            nav.setProperty("active", "true" if key == page else "false")
            nav.style().unpolish(nav)
            nav.style().polish(nav)
            recolor_button(nav)
        self._refresh()

    def _periodic_refresh(self) -> None:
        if self.active_job and self.current_page in {"dashboard", "tasks", "gallery"}:
            self._refresh()

    def _remote_image(self, url: str, target: QLabel, width: int, height: int) -> None:
        if not url.startswith("https://"):
            return
        if url in self.image_cache:
            pixmap = QPixmap()
            if pixmap.loadFromData(self.image_cache[url]):
                target.setPixmap(pixmap if isinstance(target, AvatarLabel) else
                                 pixmap.scaled(width, height, Qt.KeepAspectRatioByExpanding,
                                               Qt.SmoothTransformation))
            return
        request = QNetworkRequest(QUrl(url))
        reply = self.network.get(request)
        def finished() -> None:
            data = bytes(reply.readAll())
            reply.deleteLater()
            if not data:
                return
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                self.image_cache[url] = data
                if isValid(target):
                    target.setPixmap(pixmap if isinstance(target, AvatarLabel) else
                                     pixmap.scaled(width, height, Qt.KeepAspectRatioByExpanding,
                                                   Qt.SmoothTransformation))
        reply.finished.connect(finished)

    def _avatar(self, job: dict, size: int) -> AvatarLabel:
        avatar = AvatarLabel(job["handle"], size)
        self._remote_image(job.get("profile_image", ""), avatar, size, size)
        return avatar

    def _media_thumbnail(self, file: Path | None, entry: dict | None,
                         width: int, height: int) -> QLabel:
        thumb = QLabel("▶  동영상" if (file and file.suffix.lower() in VIDEO_EXTENSIONS)
                       or (entry and entry["kind"] == "video") else "▧  이미지")
        thumb.setObjectName("mediaThumb")
        thumb.setAlignment(Qt.AlignCenter)
        thumb.setFixedSize(width, height)
        thumb.setScaledContents(False)
        if file:
            source = file
            if file.suffix.lower() in VIDEO_EXTENSIONS:
                source = self.thumbnail_worker.enqueue(file)
            if source and source.is_file():
                pixmap = QPixmap(str(source))
                if not pixmap.isNull():
                    thumb.setPixmap(pixmap.scaled(width, height, Qt.KeepAspectRatioByExpanding,
                                                  Qt.SmoothTransformation))
            elif entry and entry.get("poster_url"):
                self._remote_image(entry["poster_url"].replace("name=orig", "name=small"),
                                   thumb, width, height)
        elif entry and (entry["kind"] == "image" or entry.get("poster_url")):
            url = (entry.get("poster_url") or entry["url"]).replace("name=orig", "name=small")
            self._remote_image(url, thumb, width, height)
        return thumb

    def _refresh(self) -> None:
        if self.current_page == "dashboard":
            self._render_dashboard()
        elif self.current_page == "tasks":
            self._render_tasks()
        elif self.current_page == "settings":
            self._render_settings()
        elif self.current_page == "gallery":
            self._render_gallery()

    def _render_dashboard(self) -> None:
        clear(self.dashboard_layout)
        jobs = self.store.jobs
        counts = [self.store.counts(job) for job in jobs]
        images = sum(item[0] for item in counts)
        videos = sum(item[1] for item in counts)
        metrics = QHBoxLayout()
        for title, value, note in (("총 다운로드", images + videos, "로컬 파일"),
                                   ("작업 수", len(jobs), f"{sum(j['status']=='downloading' for j in jobs)} 진행 중"),
                                   ("저장한 이미지", images, "전체 이미지"),
                                   ("저장한 동영상", videos, "전체 동영상")):
            card, box = panel()
            box.addWidget(label(title, "muted"))
            box.addWidget(label(str(value), "metric"))
            box.addWidget(label(note, "muted"))
            metrics.addWidget(card, 1)
        self.dashboard_layout.addLayout(metrics)
        columns = QHBoxLayout()
        columns.setAlignment(Qt.AlignTop)
        left = QVBoxLayout()
        left.setAlignment(Qt.AlignTop)
        left.addWidget(label("최근 작업", "section"))
        if not jobs:
            empty, box = panel()
            box.addWidget(label("아직 작업이 없습니다. 위에 X 계정 URL을 입력해 저장을 시작하세요.", "muted"))
            left.addWidget(empty)
        for job in jobs[:5]:
            item, box = panel()
            row = QHBoxLayout()
            row.addWidget(self._avatar(job, 38))
            identity = QVBoxLayout()
            identity.setSpacing(0)
            identity.addWidget(label(job.get("title") or job["handle"], "section"))
            identity.addWidget(label("@" + job["handle"], "muted"))
            row.addLayout(identity)
            row.addStretch()
            row.addWidget(label(self._status_text(job), "muted"))
            box.addLayout(row)
            pics, clips = self.store.counts(job)
            box.addWidget(label(f"사진 {pics} · 영상 {clips}  |  {job['created'][:10]}", "muted"))
            box.addWidget(button("작업 보기", lambda _=False, j=job: self._navigate("gallery", j["id"]), icon_name="image"))
            left.addWidget(item)
        columns.addLayout(left, 3)
        right = QVBoxLayout()
        right.setAlignment(Qt.AlignTop)
        right.addWidget(label("다운로드 현황", "section"))
        stats, box = panel()
        box.addWidget(label(f"로컬 작업 {len(jobs)}개", "section"))
        box.addWidget(label(f"저장 파일 {images + videos}개 · 다운로드 중 {sum(j['status']=='downloading' for j in jobs)}개", "muted"))
        right.addWidget(stats)
        right.addWidget(label("X 계정 상태", "section"))
        auth, box = panel()
        box.addWidget(label("X 쿠키 발견" if self._has_x_login() else "로그인 확인 필요", "section"))
        box.addWidget(label("앱 내부 X 브라우저의 로그인 세션을 사용합니다.", "muted"))
        box.addWidget(button("X 브라우저 열기", lambda: self._navigate("browser"), icon_name="browser"))
        right.addWidget(auth)
        columns.addLayout(right, 2)
        self.dashboard_layout.addLayout(columns)
        recent = []
        for job in jobs:
            recent.extend(self.store.inventory(job)[:8])
        recent.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        if recent:
            self.dashboard_layout.addWidget(label("최근 저장한 미디어", "section"))
            strip = QHBoxLayout()
            for file in recent[:5]:
                card, box = panel()
                card.setFixedWidth(170)
                box.setContentsMargins(7, 7, 7, 7)
                box.addWidget(self._media_thumbnail(file, None, 152, 95))
                box.addWidget(label(file.name[:22], "muted"))
                strip.addWidget(card)
            strip.addStretch()
            self.dashboard_layout.addLayout(strip)

    def _status_text(self, job: dict) -> str:
        return {"waiting": "대기 중", "downloading": "다운로드 중", "completed": "다운로드 완료",
                "paused": "중단됨", "failed": "다운로드 실패"}.get(job["status"], job["status"])

    def _render_tasks(self) -> None:
        clear(self.tasks_layout)
        toolbar = QHBoxLayout()
        task_filters = ("전체", "대기", "다운로드 완료", "다운로드 실패")
        tabs = QTabBar()
        tabs.setObjectName("filterTabs")
        tabs.setDrawBase(False)
        tabs.setExpanding(False)
        for title in task_filters:
            tabs.addTab(title)
        tabs.setCurrentIndex(task_filters.index(self.filter_value))
        tabs.currentChanged.connect(lambda index: self._set_task_filter(task_filters[index]))
        toolbar.addWidget(tabs)
        toolbar.addStretch()
        search = QLineEdit(self.search_value)
        self.search_widget = search
        search.setPlaceholderText("작업 이름으로 검색...")
        search.setFixedWidth(240)
        search.textChanged.connect(self._set_search)
        toolbar.addWidget(search)
        self.tasks_layout.addLayout(toolbar)
        jobs = [job for job in self.store.jobs if self.search_value.lower() in job["handle"].lower()]
        if self.filter_value == "대기":
            jobs = [job for job in jobs if job["status"] in {"waiting", "paused"}]
        elif self.filter_value == "다운로드 완료":
            jobs = [job for job in jobs if job["status"] == "completed"]
        elif self.filter_value == "다운로드 실패":
            jobs = [job for job in jobs if job["status"] == "failed"]
        if not jobs:
            empty, box = panel()
            box.addWidget(label("아직 작업이 없습니다. X 계정 URL을 입력해 다운로드 작업을 시작하세요.", "muted"))
            self.tasks_layout.addWidget(empty)
            return
        table = QTableWidget(len(jobs), 5)
        table.setHorizontalHeaderLabels(["작업 이름", "미디어", "상태", "생성됨", "동작"])
        table.verticalHeader().hide()
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setShowGrid(False)
        table.horizontalHeader().setStretchLastSection(True)
        widths = (300, 120, 140, 145, 260)
        for i, width in enumerate(widths):
            table.setColumnWidth(i, width)
        for row, job in enumerate(jobs):
            pics, clips = self.store.counts(job)
            account = QWidget()
            account_row = QHBoxLayout(account)
            account_row.setContentsMargins(10, 4, 4, 4)
            account_row.setSpacing(9)
            account_row.addWidget(self._avatar(job, 36))
            account_text = QVBoxLayout()
            account_text.setSpacing(0)
            account_text.addWidget(label(job.get("title") or job["handle"], "mediaTitle"))
            account_text.addWidget(label("@" + job["handle"], "muted"))
            account_row.addLayout(account_text)
            account_row.addStretch()
            table.setCellWidget(row, 0, account)
            for col, text in enumerate((f"{pics + clips} 미디어", self._status_text(job),
                                        job["created"][:16].replace("T", " ")), 1):
                table.setItem(row, col, QTableWidgetItem(text))
            action = QWidget()
            layout = QHBoxLayout(action)
            layout.setContentsMargins(3, 3, 3, 3)
            layout.addStretch()
            layout.addWidget(button("미디어 보기", lambda _=False, j=job: self._navigate("gallery", j["id"]), icon_name="image"))
            if job["status"] == "downloading":
                layout.addWidget(button("다운로드 중단", self._stop_job, icon_name="pause"))
            more = QPushButton("작업 메뉴")
            menu = QMenu(more)
            menu.addAction("미디어 목록 다시 확인", lambda _checked=False, j=job: self._load_preview(j))
            if job["status"] != "downloading" and job.get("preview_state") == "ready" and job.get("selected_keys"):
                menu.addAction("선택한 미디어 다운로드", lambda _checked=False, j=job: self._start_job(j))
            menu.addSeparator()
            menu.addAction("작업 기록 삭제", lambda _checked=False, j=job: self._delete_job(j))
            more.setMenu(menu)
            layout.addWidget(more)
            table.setCellWidget(row, 4, action)
            table.setRowHeight(row, 64)
        table.setFixedHeight(min(640, 45 + len(jobs) * 64))
        self.tasks_layout.addWidget(table)
        self.tasks_layout.addWidget(label("작업 삭제는 목록 기록만 지우며 이미 받은 파일은 유지합니다.", "muted"))

    def _set_task_filter(self, value: str) -> None:
        self.filter_value = value
        self._render_tasks()

    def _set_search(self, value: str) -> None:
        self.search_value = value
        self._render_tasks()
        self.search_widget.setFocus()
        self.search_widget.setCursorPosition(len(value))

    def _render_settings(self) -> None:
        clear(self.settings_layout)
        appearance, appearance_box = panel()
        appearance_box.addWidget(label("화면 설정", "section"))
        font_row = QHBoxLayout()
        font_row.addWidget(label("텍스트 크기"))
        self.font_size_input = QComboBox()
        for size in range(12, 17):
            self.font_size_input.addItem(f"{size}px" + (" · 기본" if size == 14 else ""), size)
        saved_size = self.store.settings.get("font_size", 14)
        self.font_size_input.setCurrentIndex(max(0, min(4, saved_size - 12))
                                            if isinstance(saved_size, int) else 2)
        font_row.addStretch()
        font_row.addWidget(self.font_size_input)
        appearance_box.addLayout(font_row)
        appearance_box.addWidget(label("12px부터 16px까지 5단계로 조절합니다. 변경 저장을 누르면 앱 전체에 적용됩니다.", "muted"))
        self.settings_layout.addWidget(appearance)
        frame, box = panel()
        header = QHBoxLayout()
        header.addWidget(label("다운로드 설정", "section"))
        header.addStretch()
        header.addWidget(button("기본값으로 재설정", self._reset_settings, icon_name="refresh"))
        header.addWidget(button("변경 저장", self._save_settings, True, "check"))
        box.addLayout(header)
        form = QFormLayout()
        self.folder_input = QLineEdit(self.store.settings.get("download_folder", ""))
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder_input, 1)
        folder_row.addWidget(button("폴더 선택", self._select_folder, icon_name="folder"))
        form.addRow("기본 저장 폴더", folder_row)
        box.addLayout(form)
        box.addWidget(label("다운로드 파일은 아래 템플릿으로 만든 계정 폴더에 저장됩니다.", "muted"))
        box.addSpacing(8)
        box.addWidget(label("폴더 이름 템플릿", "section"))
        self.folder_template_input = QLineEdit(self.store.settings.get("folder_template", DEFAULT_FOLDER_TEMPLATE))
        self.folder_template_input.textChanged.connect(self._update_template_preview)
        box.addWidget(self.folder_template_input)
        box.addLayout(self._template_chips(self.folder_template_input, FOLDER_TOKENS))
        self.folder_preview = label("", "muted")
        box.addWidget(self.folder_preview)
        box.addSpacing(8)
        box.addWidget(label("파일 이름 템플릿", "section"))
        self.file_template_input = QLineEdit(self.store.settings.get("file_template", DEFAULT_FILE_TEMPLATE))
        self.file_template_input.textChanged.connect(self._update_template_preview)
        box.addWidget(self.file_template_input)
        box.addLayout(self._template_chips(self.file_template_input, FILE_TOKENS))
        self.file_preview = label("", "muted")
        box.addWidget(self.file_preview)
        box.addWidget(label("확장자는 자동으로 붙습니다. 같은 게시물의 여러 사진이 덮어써지지 않도록 게시물·미디어 번호가 없는 경우 자동 추가됩니다.", "muted"))
        self.settings_layout.addWidget(frame)
        auth, box = panel()
        box.addWidget(label("X 로그인", "section"))
        box.addWidget(label("X 로그인은 이 프로그램의 내장 브라우저에만 저장됩니다. 다운로드 때 임시 쿠키 파일을 만들고 작업이 끝나면 삭제합니다.", "muted"))
        box.addWidget(button("X 브라우저 열기", lambda: self._navigate("browser"), icon_name="browser"))
        self.settings_layout.addWidget(auth)
        self._update_template_preview()

    def _template_chips(self, target: QLineEdit, names: tuple[str, ...]) -> QGridLayout:
        grid = QGridLayout()
        grid.addWidget(label("삽입:", "muted"), 0, 0)
        for index, name in enumerate(names):
            row, column = divmod(index, 4)
            grid.addWidget(button("{{" + name + "}}", lambda n=name: target.insert("{{" + n + "}}")), row, column + 1)
        return grid

    def _update_template_preview(self) -> None:
        if not hasattr(self, "folder_preview") or not hasattr(self, "file_preview"):
            return
        try:
            folder = folder_name(self.folder_template_input.text(), "example_user", "예시 사용자")
            self.folder_preview.setText("폴더 예시: " + folder)
        except ValueError as exc:
            self.folder_preview.setText("폴더 템플릿 오류: " + str(exc))
        try:
            fmt = filename_format(self.file_template_input.text())
            from gallery_dl import formatter
            sample = {"content": "예시 게시물", "date": datetime(2026, 11, 20, 12, 30),
                      "user": {"name": "example_user", "nick": "예시 사용자"},
                      "tweet_id": 123456789, "num": 1, "extension": "jpg"}
            self.file_preview.setText("파일 예시: " + formatter.parse(fmt).format_map(sample))
        except (ValueError, KeyError, TypeError) as exc:
            self.file_preview.setText("파일 템플릿 오류: " + str(exc))

    def _reset_settings(self) -> None:
        self.folder_template_input.setText(DEFAULT_FOLDER_TEMPLATE)
        self.file_template_input.setText(DEFAULT_FILE_TEMPLATE)
        self.font_size_input.setCurrentIndex(2)
        self._save_settings()

    def _select_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "저장 폴더 선택", self.folder_input.text())
        if folder:
            self.folder_input.setText(folder)

    def _save_settings(self) -> None:
        folder = self.folder_input.text().strip()
        if not folder:
            QMessageBox.warning(self, "설정", "저장 폴더를 선택하세요.")
            return
        try:
            folder_template = validate_template(self.folder_template_input.text(), FOLDER_TOKENS)
            file_template = validate_template(self.file_template_input.text(), FILE_TOKENS)
        except ValueError as exc:
            QMessageBox.warning(self, "템플릿 확인", str(exc))
            return
        self.store.settings["download_folder"] = folder
        self.store.settings["folder_template"] = folder_template
        self.store.settings["file_template"] = file_template
        self.store.settings["font_size"] = self.font_size_input.currentData()
        self.store.save_settings()
        apply_theme(QApplication.instance(), system_is_dark(), self,
                    self.store.settings["font_size"])
        self.statusBar().showMessage("다운로드 설정을 저장했습니다.", 5000)

    def _render_gallery(self) -> None:
        position = self.gallery_page.verticalScrollBar().value()
        clear(self.gallery_layout)
        job = next((item for item in self.store.jobs if item["id"] == self.current_job_id), None)
        if not job:
            self.gallery_layout.addWidget(label("작업을 찾을 수 없습니다."))
            return
        top, box = panel()
        row = QHBoxLayout()
        row.addWidget(self._avatar(job, 42))
        identity = QVBoxLayout()
        identity.addWidget(label(job.get("title") or job["handle"], "pageTitle"))
        identity.addWidget(label("@" + job["handle"], "muted"))
        row.addLayout(identity)
        row.addStretch()
        row.addWidget(label(self._status_text(job), "muted"))
        if job["status"] == "downloading":
            row.addWidget(button("중단", self._stop_job, icon_name="pause"))
        else:
            start = button("선택한 미디어 다운로드", lambda: self._start_job(job), True, "download")
            start.setEnabled(job.get("preview_state") == "ready" and bool(job.get("selected_keys")))
            row.addWidget(start)
        row.addWidget(button("폴더 열기", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(job["download_folder"])), icon_name="folder"))
        box.addLayout(row)
        pics, clips = self.store.counts(job)
        preview = job.get("preview", [])
        done = sum(item.get("downloaded") for item in preview)
        state = job.get("preview_state")
        note = ("목록 불러오는 중…" if state == "loading" else
                "목록 확인 실패" if state == "failed" else
                "목록 확인 필요" if state == "idle" else "목록 확인 완료")
        self.preview_summary_label = label(f"{note}  ·  발견 {len(preview)}개  ·  저장 완료 {done}개  ·  미저장 {len(preview)-done}개  ·  선택 {len(job.get('selected_keys', []))}개", "muted")
        box.addWidget(self.preview_summary_label)
        box.addWidget(label(f"로컬 파일 {pics + clips}개 · 사진 {pics}개 · 동영상 {clips}개", "muted"))
        options = QHBoxLayout()
        new_only = QCheckBox("새 미디어 자동 선택")
        new_only.setChecked(job.get("new_only", False))
        new_only.toggled.connect(lambda checked, j=job: self._toggle_new_only(j, checked))
        options.addWidget(new_only)
        options.addStretch()
        if state == "loading":
            options.addWidget(button("목록 확인 중단", self._cancel_preview, icon_name="pause"))
        else:
            options.addWidget(button("목록 다시 확인", lambda: self._load_preview(job), icon_name="refresh"))
        box.addLayout(options)
        if job.get("error"):
            box.addWidget(label("최근 오류: " + job["error"][-400:], "muted"))
        self.gallery_layout.addWidget(top)
        if state == "loading":
            loading, loading_box = panel()
            loading_box.addWidget(label("미디어 목록을 백그라운드에서 확인하고 있습니다", "section"))
            self.preview_progress_label = label(f"현재 {len(preview):,}개 확인 · 목록 확인이 끝나면 선택할 수 있습니다.", "muted")
            loading_box.addWidget(self.preview_progress_label)
            self.gallery_layout.addWidget(loading)
            QTimer.singleShot(0, lambda: self.gallery_page.verticalScrollBar().setValue(position))
            return
        self.preview_progress_label = None
        filters = QHBoxLayout()
        filters.addWidget(label("미디어 목록", "section"))
        filters.addStretch()
        switch = QFrame()
        switch.setObjectName("viewSwitch")
        switch_row = QHBoxLayout(switch)
        switch_row.setContentsMargins(2, 2, 2, 2)
        switch_row.setSpacing(2)
        for kind, title in (("card", "카드"), ("list", "목록")):
            item = button(title, lambda _=False, value=kind: self._set_gallery_view(value),
                          icon_name="dashboard" if kind == "card" else "tasks")
            if kind == self.gallery_view:
                item.setProperty("selected", "true")
            switch_row.addWidget(item)
        filters.addWidget(switch)
        self.gallery_layout.addLayout(filters)
        gallery_filters = ("전체", "이미지", "동영상", "저장 완료", "미저장")
        tabs = QTabBar()
        tabs.setObjectName("filterTabs")
        tabs.setDrawBase(False)
        tabs.setExpanding(False)
        for title in gallery_filters:
            tabs.addTab(title)
        tabs.setCurrentIndex(gallery_filters.index(self.gallery_filter))
        tabs.currentChanged.connect(lambda index: self._set_gallery_filter(gallery_filters[index]))
        self.gallery_layout.addWidget(tabs)
        files = self.store.inventory(job)
        file_by_key = {}
        for file in files:
            match = re.search(r"(?:^|\D)(\d{15,20})_(\d+)(?:\D|$)", file.stem)
            if match:
                file_by_key[f"{match.group(1)}:{int(match.group(2))}"] = file
        entries = preview
        selected_keys = set(job.get("selected_keys", []))
        if self.gallery_filter == "이미지":
            entries = [item for item in entries if item["kind"] == "image"]
        elif self.gallery_filter == "동영상":
            entries = [item for item in entries if item["kind"] == "video"]
        elif self.gallery_filter == "저장 완료":
            entries = [item for item in entries if item.get("downloaded")]
        elif self.gallery_filter == "미저장":
            entries = [item for item in entries if not item.get("downloaded")]
        if not preview and not files:
            empty, box = panel()
            box.addWidget(label("미디어 목록을 확인하면 사진과 영상이 여기에 표시됩니다.", "muted"))
            self.gallery_layout.addWidget(empty)
        elif preview:
            if not entries:
                empty, box = panel()
                box.addWidget(label("이 조건에 맞는 미디어가 없습니다.", "muted"))
                self.gallery_layout.addWidget(empty)
            grid = QGridLayout()
            grid.setSpacing(10)
            columns = 1 if self.gallery_view == "list" else max(1, min(5, (self.width() - 80) // 240))
            if self.gallery_view == "card":
                grid.setColumnStretch(columns, 1)
            for i, entry in enumerate(entries[:self.gallery_limit]):
                card = SelectableMediaCard()
                card.setObjectName("mediaCard")
                card.toggled.connect(lambda j=job, key=entry["key"]:
                                     self._toggle_media(j, key, key not in set(j.get("selected_keys", []))))
                box = QVBoxLayout(card)
                box.setSpacing(10)
                card.setProperty("selected", "true" if entry["key"] in selected_keys else "false")
                if self.gallery_view == "card":
                    card.setFixedWidth(230)
                    box.setContentsMargins(9, 9, 9, 9)
                else:
                    box.setContentsMargins(10, 8, 10, 8)
                body = QHBoxLayout() if self.gallery_view == "list" else QVBoxLayout()
                body.setContentsMargins(0, 0, 0, 0)
                body.setSpacing(8)
                file = file_by_key.get(entry["key"])
                thumbnail = self._media_thumbnail(file, entry,
                                                   138 if self.gallery_view == "list" else 210,
                                                   86 if self.gallery_view == "list" else 142)
                thumbnail.setAttribute(Qt.WA_TransparentForMouseEvents)
                body.addWidget(thumbnail)
                info = QVBoxLayout()
                status = "저장 완료" if entry.get("downloaded") else "미저장"
                for item in (label(("동영상" if entry["kind"] == "video" else "사진") + "  ·  " + status,
                                   "statusDone" if entry.get("downloaded") else "statusPending"),
                             label(entry["text"][:64] or entry["tweet_id"], "mediaTitle"),
                             label(entry["date"][:16].replace("T", " "), "muted")):
                    item.setAttribute(Qt.WA_TransparentForMouseEvents)
                    info.addWidget(item)
                select = QCheckBox("선택")
                select.setChecked(entry["key"] in selected_keys)
                select.toggled.connect(lambda checked, j=job, key=entry["key"]: self._toggle_media(j, key, checked))
                info.addWidget(select)
                if file:
                    info.addWidget(button("파일 열기", lambda _=False, path=file: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))))
                body.addLayout(info)
                box.addLayout(body)
                grid.addWidget(card, i // columns, i % columns)
            self.gallery_layout.addLayout(grid)
            if len(entries) > self.gallery_limit:
                self.gallery_layout.addWidget(button(f"더 보기 · {len(entries) - self.gallery_limit}개 남음", self._more_gallery))
        elif files:
            self.gallery_layout.addWidget(label("저장된 파일 · 목록 확인을 누르면 새 미디어와 비교합니다", "section"))
            grid = QGridLayout()
            grid.setSpacing(10)
            columns = 1 if self.gallery_view == "list" else max(1, min(5, (self.width() - 100) // 240))
            for i, file in enumerate(files[:self.gallery_limit]):
                card, box = panel()
                box.setContentsMargins(9, 9, 9, 9)
                if self.gallery_view == "card":
                    card.setFixedWidth(220)
                content = QHBoxLayout() if self.gallery_view == "list" else QVBoxLayout()
                content.setContentsMargins(0, 0, 0, 0)
                content.addWidget(self._media_thumbnail(file, None,
                                                        138 if self.gallery_view == "list" else 200,
                                                        86 if self.gallery_view == "list" else 130))
                detail = QVBoxLayout()
                detail.addWidget(label("동영상 · 저장 완료" if file.suffix.lower() in VIDEO_EXTENSIONS
                                       else "사진 · 저장 완료", "statusDone"))
                detail.addWidget(label(file.name[:55], "mediaTitle"))
                detail.addWidget(button("파일 열기", lambda _=False, path=file:
                                        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))))
                content.addLayout(detail)
                box.addLayout(content)
                grid.addWidget(card, i // columns, i % columns)
            self.gallery_layout.addLayout(grid)
            if len(files) > self.gallery_limit:
                self.gallery_layout.addWidget(button(f"더 보기 · {len(files)-self.gallery_limit}개 남음", self._more_gallery))
        if self.active_job and self.active_job["id"] == job["id"]:
            log, box = panel()
            box.addWidget(label("진행 로그", "section"))
            self.log_view = QTextEdit()
            self.log_view.setReadOnly(True)
            self.log_view.setMaximumHeight(180)
            self.log_view.setPlainText("\n".join(self.recent_log[-80:]))
            box.addWidget(self.log_view)
            self.gallery_layout.addWidget(log)
        QTimer.singleShot(0, lambda: self.gallery_page.verticalScrollBar().setValue(position))

    def _toggle_media(self, job: dict, key: str, checked: bool) -> None:
        selected = set(job.get("selected_keys", []))
        if checked:
            selected.add(key)
        else:
            selected.discard(key)
        job["selected_keys"] = sorted(selected)
        self.selection_save_timer.start(600)
        self._render_gallery()

    def _toggle_new_only(self, job: dict, checked: bool) -> None:
        job["new_only"] = checked
        job["selected_keys"] = [item["key"] for item in job.get("preview", [])
                                if not checked or not item.get("downloaded")]
        self.selection_save_timer.start(600)
        self._render_gallery()

    def _set_gallery_view(self, view: str) -> None:
        self.gallery_view = view
        self._render_gallery()

    def _more_gallery(self) -> None:
        self.gallery_limit += 120
        self._render_gallery()

    def _set_gallery_filter(self, value: str) -> None:
        self.gallery_filter = value
        self._render_gallery()

    def _new_job_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("새 다운로드 작업")
        dialog.setMinimumWidth(420)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label("X 계정 URL 또는 @아이디", "section"))
        account = QLineEdit(self.account_input.text())
        account.setPlaceholderText("https://x.com/username")
        layout.addWidget(account)
        layout.addWidget(label("저장할 미디어", "section"))
        media = QComboBox()
        media.addItems(["이미지와 동영상", "이미지", "동영상"])
        layout.addWidget(media)
        controls = QHBoxLayout()
        controls.addStretch()
        controls.addWidget(button("취소", dialog.reject))
        controls.addWidget(button("미디어 확인", dialog.accept, True))
        layout.addLayout(controls)
        if dialog.exec() == QDialog.Accepted:
            self.account_input.setText(account.text())
            selected = {"이미지와 동영상": "all", "이미지": "images", "동영상": "videos"}[media.currentText()]
            self._create_and_preview(selected)

    def _paste_url(self) -> None:
        value = QApplication.clipboard().text().strip()
        if value:
            self.account_input.setText(value)
            self._create_and_preview()

    def _create_and_preview(self, media_filter: str = "all") -> None:
        try:
            handle, target_url = parse_target(self.account_input.text())
        except ValueError as exc:
            QMessageBox.warning(self, "계정 입력", str(exc))
            return
        username = self.profile_names.get(handle.lower(), "")
        target = self.store.target_folder(handle, username)
        current_template = self.store.settings["file_template"]
        existing = next((job for job in self.store.jobs if job["handle"].lower() == handle.lower()
                         and job["media_filter"] == media_filter
                         and job.get("target_url", f"https://x.com/{handle}/media") == target_url
                         and job["download_folder"] == str(target)
                         and job.get("file_template", DEFAULT_FILE_TEMPLATE) == current_template), None)
        job = existing or self.store.create(handle, media_filter, username, target_url)
        if existing:
            job["target_url"] = target_url
        self._navigate("gallery", job["id"])
        self._load_preview(job)

    def _load_preview(self, job: dict) -> None:
        if self.preview_worker:
            QMessageBox.information(self, "목록 확인 중", "현재 미디어 목록을 모두 불러온 뒤 다시 확인하세요.")
            return
        if not self.browser_ready:
            self.pending_preview_job = job
            self._navigate("browser")
            self.browser_hint.setText("로그인 세션을 준비한 뒤 미디어 목록을 불러옵니다.")
            return
        try:
            cookie_file = self._cookie_file()
        except ValueError as exc:
            QMessageBox.warning(self, "로그인 필요", str(exc))
            self._navigate("browser")
            return
        folders = [Path(item["download_folder"]) for item in self.store.jobs
                   if item["handle"].lower() == job["handle"].lower()]
        self.preview_seen.clear()
        self.preview_selected.clear()
        self.preview_cancel_requested = False
        job["preview"] = []
        job["selected_keys"] = []
        job["preview_state"] = "loading"
        job["new_only"] = job.get("new_only", True)
        self.preview_job_id = job["id"]
        self.preview_worker = PreviewWorker(job["target_url"], cookie_file, folders)
        self.preview_worker.batch.connect(self._preview_batch)
        self.preview_worker.finished_result.connect(self._preview_finished)
        self.preview_worker.start()
        self._navigate("gallery", job["id"])

    def _preview_batch(self, entries: list[dict]) -> None:
        job = next((item for item in self.store.jobs if item["id"] == self.preview_job_id), None)
        if not job:
            return
        for entry in entries:
            if entry["key"] in self.preview_seen:
                continue
            self.preview_seen.add(entry["key"])
            if job["media_filter"] == "images" and entry["kind"] != "image":
                continue
            if job["media_filter"] == "videos" and entry["kind"] != "video":
                continue
            job["preview"].append(entry)
            if not job["new_only"] or not entry["downloaded"]:
                self.preview_selected.add(entry["key"])
            if entry["profile_name"]:
                job["title"] = entry["profile_name"]
            if entry["profile_image"]:
                job["profile_image"] = entry["profile_image"]
        if self.current_page == "gallery" and self.current_job_id == job["id"]:
            if self.preview_progress_label and isValid(self.preview_progress_label):
                self.preview_progress_label.setText(
                    f"현재 {len(job['preview']):,}개 확인 · 목록 확인이 끝나면 선택할 수 있습니다.")
            if self.preview_summary_label and isValid(self.preview_summary_label):
                self.preview_summary_label.setText(f"목록 불러오는 중…  ·  발견 {len(job['preview']):,}개")

    def _preview_finished(self, code: int, error: str) -> None:
        job = next((item for item in self.store.jobs if item["id"] == self.preview_job_id), None)
        if job:
            job["selected_keys"] = sorted(self.preview_selected)
            job["preview_state"] = "idle" if self.preview_cancel_requested else "ready" if code == 0 else "failed"
            if self.preview_cancel_requested:
                job["error"] = "사용자가 목록 확인을 중단했습니다."
            elif code != 0:
                job["error"] = error or "미디어 목록을 불러오지 못했습니다."
            self.store.save_jobs()
        if self.preview_worker:
            self.preview_worker.wait(1000)
        self.preview_worker = None
        self.preview_job_id = None
        self.preview_seen.clear()
        self.preview_selected.clear()
        self._refresh()

    def _cancel_preview(self) -> None:
        if self.preview_worker:
            self.preview_cancel_requested = True
            self.preview_worker.stop()

    def _start_job(self, job: dict) -> None:
        if self.worker:
            QMessageBox.information(self, "진행 중", "현재 작업이 끝난 뒤 다음 작업을 시작하세요.")
            return
        if job.get("preview_state") != "ready":
            self._load_preview(job)
            return
        selected = set(job.get("selected_keys", []))
        if not selected:
            QMessageBox.information(self, "선택한 미디어 없음", "목록에서 저장할 미디어를 선택하세요.")
            return
        if not self.browser_ready:
            self.pending_job = job
            self._navigate("browser")
            self.browser_hint.setText("X 브라우저의 로그인 상태를 확인한 뒤 저장을 시작합니다.")
            return
        try:
            cookie_file = self._cookie_file()
            folder = Path(job["download_folder"])
            folder.mkdir(parents=True, exist_ok=True)
            name_format = filename_format(job.get("file_template", DEFAULT_FILE_TEMPLATE))
            filters = selection_filters(selected)
            commands = []
            for expression in filters:
                command = build_command(job["handle"], folder, "없음", str(cookie_file),
                                        name_format, job.get("target_url", ""))
                command[-1:-1] = ["--filter", expression]
                commands.append(command)
        except (ValueError, OSError) as exc:
            if "cookie_file" in locals():
                cookie_file.unlink(missing_ok=True)
            QMessageBox.warning(self, "로그인 필요", str(exc))
            self._navigate("browser")
            return
        job["status"] = "downloading"
        job["error"] = ""
        self.store.save_jobs()
        self.active_job = job
        self.recent_log: list[str] = []
        self.worker = CommandWorker(commands, cookie_file)
        self.worker.output.connect(self._download_output)
        self.worker.finished_result.connect(self._download_finished)
        self.worker.start()
        self.statusBar().showMessage(f"@{job['handle']} 다운로드 중…")
        self._refresh()

    def _download_output(self, line: str) -> None:
        self.recent_log.append(line.rstrip())
        self.recent_log = self.recent_log[-200:]
        if self.current_page == "gallery" and hasattr(self, "log_view"):
            self.log_view.append(line.rstrip())

    def _download_finished(self, code: int, last_line: str) -> None:
        if not self.active_job:
            return
        job = self.active_job
        if job["status"] == "paused":
            status = "paused"
        elif code == 0:
            status = "completed"
        else:
            status = "failed"
            job["error"] = last_line
        job["status"] = status
        self.store.save_jobs()
        self.active_job = None
        if self.worker:
            self.worker.wait(1000)
        self.worker = None
        self.store.invalidate_inventory(job)
        if job.get("preview"):
            keys = downloaded_keys([Path(job["download_folder"])])
            for entry in job["preview"]:
                entry["downloaded"] = entry["key"] in keys
            if job.get("new_only"):
                job["selected_keys"] = [key for key in job.get("selected_keys", []) if key not in keys]
            self.store.save_jobs()
        self.statusBar().showMessage(f"@{job['handle']}: {self._status_text(job)}", 10000)
        self._refresh()

    def _stop_job(self) -> None:
        if self.worker and self.active_job:
            self.active_job["status"] = "paused"
            self.store.save_jobs()
            self.worker.stop()
            self.statusBar().showMessage("다운로드를 중단하는 중…")

    def _delete_job(self, job: dict) -> None:
        if job["status"] == "downloading":
            QMessageBox.information(self, "작업 삭제", "진행 중인 작업은 먼저 중단하세요.")
            return
        answer = QMessageBox.question(self, "작업 기록 삭제", "목록에서 작업 기록을 지울까요? 다운로드된 파일은 그대로 유지됩니다.")
        if answer == QMessageBox.Yes:
            self.store.remove_record(job["id"])
            self._refresh()

    def closeEvent(self, event) -> None:
        if self.selection_save_timer.isActive():
            self.selection_save_timer.stop()
            self.store.save_jobs()
        if self.preview_worker:
            self.preview_worker.stop()
        if self.worker:
            self.worker.stop()
            self.worker.wait(4000)
        self.thumbnail_worker.stop()
        super().closeEvent(event)


def main() -> None:
    if "--smoke-test" in sys.argv:
        QtWebView.initialize()
        app = QApplication([sys.argv[0]])
        view = QWebView()
        view.loadingChanged.connect(
            lambda info: app.exit(0 if info.status().name == "Succeeded" else 1)
            if info.status().name in {"Succeeded", "Failed"} else None)
        QTimer.singleShot(10000, lambda: app.exit(2))
        view.setUrl(QUrl("about:blank"))
        view.show()
        sys.exit(app.exec())
    debug_port = configure_webview_debugging()
    QtWebView.initialize()
    app = QApplication(sys.argv)
    app.setOrganizationName("XMediaSaver")
    app.setApplicationName("XMediaSaver")
    app.setStyle("Fusion")
    load_ui_font(app)
    apply_theme(app, system_is_dark())
    window = DesktopApp(debug_port)
    apply_theme(app, system_is_dark(), window, window.store.settings.get("font_size", 14))
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
