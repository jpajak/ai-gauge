from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
import websocket
from PyQt6.QtCore import QThread, pyqtSignal

from ..config import COOKIE_DOMAINS, COOKIE_NAME_ALIASES, app_data_dir

log = logging.getLogger("aigauge.webview.external_login")


def _browser_candidates() -> list[Path]:
    """Return likely Chrome-family browser executables in preference order."""
    if sys.platform == "win32":
        roots = [
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
        ]
        relative = [
            Path("Google/Chrome/Application/chrome.exe"),
            Path("Microsoft/Edge/Application/msedge.exe"),
            Path("Chromium/Application/chrome.exe"),
            Path("BraveSoftware/Brave-Browser/Application/brave.exe"),
        ]
        return [Path(root) / item for item in relative for root in roots if root]
    if sys.platform == "darwin":
        return [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            Path("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        ]
    names = (
        "google-chrome",
        "google-chrome-stable",
        "microsoft-edge",
        "microsoft-edge-stable",
        "chromium",
        "chromium-browser",
        "brave-browser",
    )
    return [Path(found) for name in names if (found := shutil.which(name))]


def find_supported_browser() -> Path | None:
    return next((path for path in _browser_candidates() if path.is_file()), None)


def _reserve_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _domain_matches(cookie_domain: str, provider: str) -> bool:
    wanted = COOKIE_DOMAINS[provider].lstrip(".").lower()
    actual = cookie_domain.lstrip(".").lower()
    return actual == wanted or actual.endswith("." + wanted)


def _provider_cookies(provider: str, cookies: list[dict]) -> list[dict]:
    return [
        cookie
        for cookie in cookies
        if isinstance(cookie, dict)
        and _domain_matches(str(cookie.get("domain", "")), provider)
        and cookie.get("name")
        and cookie.get("value") is not None
    ]


def _has_auth_cookie(provider: str, cookies: list[dict]) -> bool:
    names = {str(cookie.get("name", "")) for cookie in cookies}
    aliases = set(COOKIE_NAME_ALIASES.get(provider, ()))
    if provider == "codex":
        return bool(names & aliases) or "__Secure-oai-is" in names
    if provider == "opencode_go":
        return bool(cookies)
    return bool(names & aliases)


class ExternalLoginWorker(QThread):
    """Run a genuine Chrome-family browser and retrieve its cookies over CDP."""

    status_changed = pyqtSignal(str)
    session_ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, provider: str, login_url: str, account_id: str, parent=None):
        super().__init__(parent)
        self._provider = provider
        self._login_url = login_url
        self._account_id = account_id
        self._process: subprocess.Popen | None = None
        self._debug_port: int | None = None
        self._websocket_url: str | None = None
        self._profile_dir: Path | None = None

    def run(self) -> None:
        browser = find_supported_browser()
        if browser is None:
            self.failed.emit(
                "Chrome, Edge, Brave, or Chromium was not found. "
                "Use the embedded browser or install a supported browser."
            )
            return

        profile_root = app_data_dir() / "browser-signin"
        profile_root.mkdir(parents=True, exist_ok=True)
        self._profile_dir = Path(
            tempfile.mkdtemp(prefix=f"{self._account_id}-", dir=profile_root)
        )
        self._debug_port = _reserve_local_port()
        command = [
            str(browser),
            f"--user-data-dir={self._profile_dir}",
            f"--remote-debugging-port={self._debug_port}",
            "--remote-debugging-address=127.0.0.1",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            self._login_url,
        ]
        try:
            self._process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            self.failed.emit(f"Could not open {browser.name}: {exc}")
            self._cleanup_profile()
            return

        log.info(
            "external sign-in started provider=%s browser=%s port=%s",
            self._account_id,
            browser.name,
            self._debug_port,
        )
        self.status_changed.emit(
            f"Finish signing in to {self._provider_name()} in {browser.stem}. "
            "AI Gauge will connect automatically."
        )

        try:
            self._poll_for_session()
        finally:
            self._close_browser()
            self._cleanup_profile()

    def _provider_name(self) -> str:
        return {
            "claude": "Claude",
            "codex": "ChatGPT",
            "opencode_go": "OpenCode",
        }.get(self._provider, self._provider)

    def _poll_for_session(self) -> None:
        startup_deadline = time.monotonic() + 20
        while not self.isInterruptionRequested() and time.monotonic() < startup_deadline:
            if self._discover_websocket():
                break
            if self._process is not None and self._process.poll() is not None:
                self.failed.emit("The browser closed before sign-in completed.")
                return
            time.sleep(0.25)
        else:
            if not self.isInterruptionRequested():
                self.failed.emit("The browser did not start in time.")
            return

        while not self.isInterruptionRequested():
            if self._process is not None and self._process.poll() is not None:
                self.failed.emit("The browser closed before sign-in completed.")
                return
            try:
                cookies = self._read_cookies()
            except Exception as exc:  # noqa: BLE001 - transient CDP failures retry
                log.debug("external sign-in cookie poll failed: %s", exc)
                time.sleep(0.75)
                continue
            relevant = _provider_cookies(self._provider, cookies)
            if _has_auth_cookie(self._provider, relevant):
                log.info(
                    "external sign-in captured provider=%s cookie_names=%s",
                    self._account_id,
                    sorted({str(cookie.get("name", "")) for cookie in relevant}),
                )
                self.session_ready.emit(relevant)
                return
            time.sleep(0.75)

    def _discover_websocket(self) -> bool:
        assert self._debug_port is not None
        try:
            session = requests.Session()
            session.trust_env = False  # never send loopback CDP traffic to a proxy
            response = session.get(
                f"http://127.0.0.1:{self._debug_port}/json/version",
                timeout=0.5,
            )
            response.raise_for_status()
            value = response.json().get("webSocketDebuggerUrl")
            parsed = urlparse(str(value or ""))
            if (
                parsed.scheme not in ("ws", "wss")
                or parsed.hostname not in ("127.0.0.1", "localhost")
                or parsed.port != self._debug_port
            ):
                return False
            self._websocket_url = str(value)
            return True
        except (requests.RequestException, ValueError):
            return False

    def _read_cookies(self) -> list[dict]:
        if not self._websocket_url:
            return []
        connection = websocket.create_connection(
            self._websocket_url,
            timeout=1.5,
            suppress_origin=True,
        )
        try:
            connection.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
            while True:
                payload = json.loads(connection.recv())
                if payload.get("id") == 1:
                    return list(payload.get("result", {}).get("cookies", []))
        finally:
            connection.close()

    def _close_browser(self) -> None:
        if self._websocket_url:
            try:
                connection = websocket.create_connection(
                    self._websocket_url,
                    timeout=1,
                    suppress_origin=True,
                )
                connection.send(json.dumps({"id": 2, "method": "Browser.close"}))
                connection.close()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
        if self._process is not None:
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.terminate()
            self._process = None

    def _cleanup_profile(self) -> None:
        profile = self._profile_dir
        self._profile_dir = None
        if profile is None:
            return
        try:
            profile.resolve().relative_to((app_data_dir() / "browser-signin").resolve())
            shutil.rmtree(profile, ignore_errors=True)
        except (OSError, ValueError):
            log.warning("refusing to clean unexpected sign-in profile path=%s", profile)

    def stop(self) -> None:
        self.requestInterruption()
