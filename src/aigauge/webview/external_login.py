from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
import websocket
from PyQt6.QtCore import QThread, pyqtSignal

from ..config import COOKIE_DOMAINS, COOKIE_NAME_ALIASES, app_data_dir

log = logging.getLogger("aigauge.webview.external_login")

_LOOPBACK_NO_PROXY = ["127.0.0.1", "localhost"]
_OPENCODE_WORKSPACE_MARKERS = ("usage", "api keys", "members", "billing", "settings")
_OPENCODE_PAGE_STATE_JS = r"""
(() => ({
  url: location.href,
  body_text: ((document.body && document.body.innerText) || '').slice(0, 10000),
}))()
"""


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
    return bool(names & aliases)


def _opencode_workspace_shell_visible(url: str, body_text: str) -> bool:
    """Whether an OpenCode page has reached its authenticated workspace shell."""
    parsed = urlparse(url)
    path_parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme != "https"
        or parsed.hostname != "opencode.ai"
        or len(path_parts) < 2
        or path_parts[0] != "workspace"
    ):
        return False
    normalized = " ".join(body_text.lower().split())
    return all(marker in normalized for marker in _OPENCODE_WORKSPACE_MARKERS)


def _is_loopback_websocket_url(value: object, port: int) -> bool:
    parsed = urlparse(str(value or ""))
    return (
        parsed.scheme in ("ws", "wss")
        and parsed.hostname in ("127.0.0.1", "localhost")
        and parsed.port == port
    )


def _create_websocket_connection(url: str, *, timeout: float):
    """Connect to loopback CDP without consulting environment proxies."""
    return websocket.create_connection(
        url,
        timeout=timeout,
        suppress_origin=True,
        http_no_proxy=_LOOPBACK_NO_PROXY,
    )


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
        self._stop_event = threading.Event()

    def run(self) -> None:
        browser = find_supported_browser()
        if browser is None:
            self.failed.emit(
                "Chrome, Edge, Brave, or Chromium was not found. "
                "Use the embedded browser or install a supported browser."
            )
            return

        profile_root = app_data_dir() / "browser-signin"
        try:
            profile_root.mkdir(parents=True, exist_ok=True)
            self._profile_dir = Path(
                tempfile.mkdtemp(prefix=f"{self._account_id}-", dir=profile_root)
            )
            self._debug_port = _reserve_local_port()
        except OSError as exc:
            self.failed.emit(f"Could not prepare the temporary browser: {exc}")
            self._cleanup_profile()
            return
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
            self._stop_event.wait(0.25)
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
                self._stop_event.wait(0.75)
                continue
            relevant = _provider_cookies(self._provider, cookies)
            if self._session_is_ready(relevant):
                log.info(
                    "external sign-in captured provider=%s cookie_names=%s",
                    self._account_id,
                    sorted({str(cookie.get("name", "")) for cookie in relevant}),
                )
                self.session_ready.emit(relevant)
                return
            self._stop_event.wait(0.75)

    def _session_is_ready(self, cookies: list[dict]) -> bool:
        if not _has_auth_cookie(self._provider, cookies):
            return False
        if self._provider == "opencode_go":
            return self._opencode_workspace_ready()
        return True

    def _opencode_workspace_ready(self) -> bool:
        """Confirm OpenCode rendered its signed-in shell, not just an OAuth cookie."""
        if self._debug_port is None:
            return False
        try:
            session = requests.Session()
            session.trust_env = False
            response = session.get(
                f"http://127.0.0.1:{self._debug_port}/json/list",
                timeout=0.5,
            )
            response.raise_for_status()
            targets = response.json()
        except (requests.RequestException, ValueError):
            return False
        if not isinstance(targets, list):
            return False

        for target in targets:
            if not isinstance(target, dict) or target.get("type") != "page":
                continue
            target_url = str(target.get("url") or "")
            parsed = urlparse(target_url)
            if (
                parsed.hostname != "opencode.ai"
                or not parsed.path.startswith("/workspace/")
            ):
                continue
            websocket_url = target.get("webSocketDebuggerUrl")
            if not _is_loopback_websocket_url(websocket_url, self._debug_port):
                continue
            try:
                connection = _create_websocket_connection(
                    str(websocket_url),
                    timeout=1.5,
                )
                try:
                    connection.send(
                        json.dumps(
                            {
                                "id": 3,
                                "method": "Runtime.evaluate",
                                "params": {
                                    "expression": _OPENCODE_PAGE_STATE_JS,
                                    "returnByValue": True,
                                },
                            }
                        )
                    )
                    while True:
                        payload = json.loads(connection.recv())
                        if payload.get("id") == 3:
                            break
                finally:
                    connection.close()
            except Exception as exc:  # noqa: BLE001 - transient CDP state
                log.debug("OpenCode workspace readiness check failed: %s", exc)
                continue
            value = (
                payload.get("result", {})
                .get("result", {})
                .get("value", {})
            )
            if isinstance(value, dict) and _opencode_workspace_shell_visible(
                str(value.get("url") or ""),
                str(value.get("body_text") or ""),
            ):
                return True
        return False

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
            if not _is_loopback_websocket_url(value, self._debug_port):
                return False
            self._websocket_url = str(value)
            return True
        except (requests.RequestException, ValueError):
            return False

    def _read_cookies(self) -> list[dict]:
        if not self._websocket_url:
            return []
        connection = _create_websocket_connection(
            self._websocket_url,
            timeout=1.5,
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
                connection = _create_websocket_connection(
                    self._websocket_url,
                    timeout=1,
                )
                connection.send(json.dumps({"id": 2, "method": "Browser.close"}))
                connection.close()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
        if self._process is not None:
            process = self._process
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    process.terminate()
                except OSError:
                    log.warning("external sign-in browser termination failed")
                else:
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        try:
                            process.kill()
                            process.wait(timeout=2)
                        except (OSError, subprocess.TimeoutExpired):
                            log.warning("external sign-in browser kill failed")
            except OSError:
                log.warning("external sign-in browser wait failed")
            self._process = None

    def _cleanup_profile(self) -> None:
        profile = self._profile_dir
        self._profile_dir = None
        if profile is None:
            return
        try:
            profile.resolve().relative_to((app_data_dir() / "browser-signin").resolve())
            for attempt in range(3):
                shutil.rmtree(profile, ignore_errors=True)
                if not profile.exists():
                    break
                if attempt < 2:
                    time.sleep(0.1)
            if profile.exists():
                log.warning(
                    "temporary sign-in profile could not be removed path=%s", profile
                )
        except (OSError, ValueError):
            log.warning("refusing to clean unexpected sign-in profile path=%s", profile)

    def stop(self) -> None:
        self.requestInterruption()
        self._stop_event.set()
