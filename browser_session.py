"""Read X cookies from a Chrome window opened by this application.

The separate Chrome profile avoids Windows DPAPI failures when reading the
user's regular Chrome cookie database. Cookie values are never displayed.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket


def chrome_executable() -> Path:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    found = shutil.which("chrome")
    if found:
        candidates.insert(0, Path(found))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("Chrome을 찾을 수 없습니다. Chrome을 설치하거나 다른 로그인 방식을 선택하세요.")


class ManagedChromeSession:
    def __init__(self) -> None:
        local = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
        self.profile = local / "XMediaSaver" / "ChromeProfile"
        self.port: int | None = None
        self.process: subprocess.Popen[bytes] | None = None

    def _version(self, port: int) -> dict:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as response:
            return json.load(response)

    def _active_port(self) -> int | None:
        for file in (self.profile / "XMediaSaverPort", self.profile / "DevToolsActivePort"):
            try:
                port = int(file.read_text(encoding="utf-8").splitlines()[0])
                self._version(port)
                return port
            except (OSError, ValueError, IndexError, TimeoutError):
                pass
        return None

    def open_login(self) -> None:
        chrome = chrome_executable()
        port = self._active_port()
        if port:
            subprocess.Popen([str(chrome), f"--user-data-dir={self.profile}",
                              "https://x.com/i/flow/login"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            self.port = port
            return
        self.profile.mkdir(parents=True, exist_ok=True)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.process = subprocess.Popen([
            str(chrome), f"--user-data-dir={self.profile}",
            f"--remote-debugging-port={port}", "--remote-debugging-address=127.0.0.1",
            f"--remote-allow-origins=http://127.0.0.1:{port}",
            "--no-first-run", "https://x.com/i/flow/login",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            if self.process.poll() is not None:
                break
            try:
                self._version(port)
                self.port = port
                (self.profile / "XMediaSaverPort").write_text(str(port), encoding="utf-8")
                return
            except (OSError, TimeoutError):
                time.sleep(0.1)
        raise RuntimeError("프로그램 전용 Chrome 창을 열 수 없습니다.")

    def export_cookie_file(self) -> Path:
        port = self._active_port() or self.port
        if not port:
            raise RuntimeError("먼저 'X 로그인 열기'로 프로그램 전용 Chrome 창을 여세요.")
        version = self._version(port)
        address = version["webSocketDebuggerUrl"]
        connection = websocket.create_connection(
            address, timeout=5, origin=f"http://127.0.0.1:{port}")
        try:
            connection.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
            while True:
                reply = json.loads(connection.recv())
                if reply.get("id") == 1:
                    break
        finally:
            connection.close()
        if "error" in reply:
            raise RuntimeError("Chrome 로그인 쿠키를 읽지 못했습니다.")
        cookies = [c for c in reply.get("result", {}).get("cookies", [])
                   if c.get("domain", "").lstrip(".") in {"x.com", "twitter.com"}
                   or c.get("domain", "").endswith((".x.com", ".twitter.com"))]
        names = {c.get("name") for c in cookies if c.get("domain", "").lstrip(".").endswith("x.com")}
        if not {"auth_token", "ct0"}.issubset(names):
            raise RuntimeError("프로그램 전용 Chrome 창에서 X 로그인을 완료한 뒤 다시 확인하세요.")
        file = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".txt",
                                           prefix="x-media-cookies-", delete=False)
        try:
            file.write("# Netscape HTTP Cookie File\n")
            for cookie in cookies:
                domain = cookie["domain"]
                if not domain.startswith("."):
                    domain = "." + domain
                secure = "TRUE" if cookie.get("secure") else "FALSE"
                expires = int(cookie.get("expires", 0))
                if expires <= 0:  # Netscape format needs a future value for session cookies.
                    expires = int(time.time()) + 3600
                value = str(cookie.get("value", "")).replace("\n", "").replace("\t", "")
                file.write(f"{domain}\tTRUE\t{cookie.get('path', '/')}\t{secure}\t{expires}\t{cookie['name']}\t{value}\n")
            file.close()
            return Path(file.name)
        except Exception:
            file.close()
            Path(file.name).unlink(missing_ok=True)
            raise

    def close(self) -> None:
        port = self._active_port()
        if port:
            try:
                address = self._version(port)["webSocketDebuggerUrl"]
                connection = websocket.create_connection(
                    address, timeout=2, origin=f"http://127.0.0.1:{port}")
                connection.send(json.dumps({"id": 1, "method": "Browser.close"}))
                connection.close()
            except (OSError, TimeoutError, websocket.WebSocketException):
                pass
