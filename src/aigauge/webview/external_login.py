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

from ..config import COOKIE_DOMAINS, app_data_dir
from ..providers.registry import has_auth_cookie, provider_spec

log = logging.getLogger("aigauge.webview.external_login")

_LOOPBACK_NO_PROXY = ["127.0.0.1", "localhost"]
BROWSER_LABELS = {
    "chrome": "Google Chrome",
    "edge": "Microsoft Edge",
    "brave": "Brave",
    "chromium": "Chromium",
}


def _browser_candidates() -> list[tuple[str, Path]]:
    """Return likely Chrome-family browser executables with stable identifiers."""
    if sys.platform == "win32":
        roots = [
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
        ]
        relative = [
            ("chrome", Path("Google/Chrome/Application/chrome.exe")),
            ("edge", Path("Microsoft/Edge/Application/msedge.exe")),
            ("brave", Path("BraveSoftware/Brave-Browser/Application/brave.exe")),
            ("chromium", Path("Chromium/Application/chrome.exe")),
        ]
        return [
            (browser_id, Path(root) / item)
            for browser_id, item in relative
            for root in roots
            if root
        ]
    if sys.platform == "darwin":
        return [
            (
                "chrome",
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            ),
            (
                "edge",
                Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            ),
            (
                "brave",
                Path("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
            ),
            ("chromium", Path("/Applications/Chromium.app/Contents/MacOS/Chromium")),
        ]
    names = (
        ("chrome", "google-chrome"),
        ("chrome", "google-chrome-stable"),
        ("edge", "microsoft-edge"),
        ("edge", "microsoft-edge-stable"),
        ("brave", "brave-browser"),
        ("brave", "brave"),  # Snap installs its launcher as /snap/bin/brave.
        ("chromium", "chromium"),
        ("chromium", "chromium-browser"),
    )
    return [
        (browser_id, Path(found))
        for browser_id, name in names
        if (found := shutil.which(name))
    ]


def installed_browsers() -> dict[str, Path]:
    """Return one executable for each installed supported browser."""
    found: dict[str, Path] = {}
    for browser_id, path in _browser_candidates():
        if browser_id not in found and path.is_file():
            found[browser_id] = path
    return found


def find_supported_browser(browser_id: str | None = None) -> Path | None:
    browsers = installed_browsers()
    if browser_id is not None:
        return browsers.get(browser_id)
    return next(iter(browsers.values()), None)


def _browser_profile_root(browser: Path) -> Path:
    """Snap Brave cannot write to AI Gauge's hidden config directory."""
    if sys.platform.startswith("linux") and browser == Path("/snap/bin/brave"):
        return Path.home() / "snap/brave/common/ai-gauge-signin"
    return app_data_dir() / "browser-signin"


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
    return has_auth_cookie(provider, (str(cookie.get("name", "")) for cookie in cookies))


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

    def __init__(
        self,
        provider: str,
        login_url: str,
        account_id: str,
        parent=None,
        *,
        browser_id: str | None = None,
    ):
        super().__init__(parent)
        self._provider = provider
        self._login_url = login_url
        self._account_id = account_id
        self._browser_id = browser_id
        self._process: subprocess.Popen | None = None
        self._debug_port: int | None = None
        self._websocket_url: str | None = None
        self._profile_dir: Path | None = None
        self._profile_root: Path | None = None
        self._stop_event = threading.Event()

    def run(self) -> None:
        browser = find_supported_browser(self._browser_id)
        if browser is None:
            selected = BROWSER_LABELS.get(self._browser_id or "", "A supported browser")
            self.failed.emit(
                f"{selected} was not found. Choose another installed browser "
                "or use the embedded browser."
            )
            return

        profile_root = _browser_profile_root(browser)
        self._profile_root = profile_root
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
            "external sign-in started provider=%s browser=%s",
            self._account_id,
            browser.name,
        )
        self.status_changed.emit(
            f"Finish signing in to {self._provider_name()} in "
            f"{BROWSER_LABELS.get(self._browser_id or '', browser.stem)}. "
            "AI Gauge will connect automatically."
        )

        try:
            self._poll_for_session()
        finally:
            self._close_browser()
            self._cleanup_profile()

    def _provider_name(self) -> str:
        spec = provider_spec(self._provider)
        if spec is None or spec.login is None:
            return self._provider
        return spec.login.prompt_name

    def _poll_for_session(self) -> None:
        startup_deadline = time.monotonic() + 20
        process_exit_seen_at: float | None = None
        while not self.isInterruptionRequested() and time.monotonic() < startup_deadline:
            if self._discover_websocket():
                break
            if self._process is not None and self._process.poll() is not None:
                # Edge and other Chromium launchers can hand the real browser
                # process off and exit. Give the loopback DevTools endpoint a
                # grace period before concluding that the window really closed.
                process_exit_seen_at = process_exit_seen_at or time.monotonic()
                if time.monotonic() - process_exit_seen_at >= 2:
                    self.failed.emit("The browser closed before sign-in completed.")
                    return
            self._stop_event.wait(0.25)
        else:
            if not self.isInterruptionRequested():
                self.failed.emit("The browser did not start in time.")
            return

        connection_failures = 0
        while not self.isInterruptionRequested():
            try:
                cookies = self._read_cookies()
            except Exception as exc:  # noqa: BLE001 - transient CDP failures retry
                log.debug("external sign-in cookie poll failed: %s", exc)
                self._websocket_url = None
                if self._discover_websocket():
                    connection_failures = 0
                else:
                    connection_failures += 1
                    if connection_failures >= 3:
                        self.failed.emit("The browser closed before sign-in completed.")
                        return
                self._stop_event.wait(0.75)
                continue
            connection_failures = 0
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
        return _has_auth_cookie(self._provider, cookies)

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
        root = self._profile_root
        self._profile_dir = None
        self._profile_root = None
        if profile is None:
            return
        try:
            if root is None:
                raise ValueError("missing profile root")
            profile.resolve().relative_to(root.resolve())
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
