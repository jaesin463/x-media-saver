"""Read the embedded Edge WebView2 session through its local DevTools endpoint."""

from __future__ import annotations

import json
import os
import socket
import urllib.request
from pathlib import Path

import websocket

from profile_cookies import read_profile_cookies


def configure_webview_debugging() -> int:
    """Reserve an unpredictable loopback port for this app instance."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
        f"--remote-debugging-port={port} --remote-debugging-address=127.0.0.1"
    )
    return port


class WebViewSession:
    def __init__(self, port: int) -> None:
        self.port = port

    def _target_url(self) -> str:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json", timeout=4) as response:
            targets = json.load(response)
        pages = [target for target in targets if target.get("type") == "page"]
        x_page = next((target for target in pages if target.get("url", "").startswith(
            ("https://x.com/", "https://twitter.com/"))), None)
        target = x_page or (pages[0] if pages else None)
        if not target or not target.get("webSocketDebuggerUrl"):
            raise ConnectionError("X 브라우저의 세션에 연결하지 못했습니다.")
        return target["webSocketDebuggerUrl"]

    def _call(self, method: str, params: dict) -> dict:
        connection = websocket.create_connection(
            self._target_url(), timeout=5, suppress_origin=True,
            http_proxy_host=None, http_proxy_port=None,
        )
        try:
            connection.send(json.dumps({"id": 1, "method": method, "params": params}))
            while True:
                message = json.loads(connection.recv())
                if message.get("id") == 1:
                    if "error" in message:
                        raise RuntimeError(message["error"].get("message", "브라우저 세션 오류"))
                    return message.get("result", {})
        finally:
            connection.close()

    def get_cookies(self) -> list[dict]:
        result = self._call("Network.getCookies", {
            "urls": ["https://x.com/", "https://twitter.com/"],
        })
        def is_x_domain(cookie: dict) -> bool:
            host = cookie.get("domain", "").lower().lstrip(".")
            return host in {"x.com", "twitter.com"} or host.endswith((".x.com", ".twitter.com"))
        return [cookie for cookie in result.get("cookies", []) if is_x_domain(cookie)]

    def import_legacy_cookies(self, profile_path: Path) -> int:
        cookies = []
        for cookie in read_profile_cookies(profile_path):
            item = {
                "name": bytes(cookie.name()).decode("utf-8", "replace"),
                "value": bytes(cookie.value()).decode("utf-8", "replace"),
                "domain": cookie.domain(),
                "path": cookie.path() or "/",
                "secure": cookie.isSecure(),
                "httpOnly": cookie.isHttpOnly(),
            }
            if not cookie.isSessionCookie():
                item["expires"] = cookie.expirationDate().toSecsSinceEpoch()
            cookies.append(item)
        if cookies:
            self._call("Network.setCookies", {"cookies": cookies})
        return len(cookies)
