"""The identity of the browser external sign-in uses, for a WebEngine profile.

The Claude Console is signed in through the user's own Chrome and the cookies
are then replayed into a QtWebEngine profile. Left alone, that profile sends a
different user agent and different client hints (``Sec-CH-UA``,
``navigator.userAgentData``) from the browser that signed in. This module
reads the installed browser's version and makes the profile send the same
identity. Detection degrades to ``None`` with a log line; it never raises.
"""
from __future__ import annotations

import logging
import plistlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .external_login import installed_browsers

log = logging.getLogger("aigauge.webview.browser_identity")

_VERSION_DIR = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
_VERSION_TEXT = re.compile(r"\d+(?:\.\d+){1,3}")
_VERSION_TIMEOUT_SECONDS = 5

_OS_TOKENS = {
    "windows": "Windows NT 10.0; Win64; x64",
    "macos": "Macintosh; Intel Mac OS X 10_15_7",
    "linux": "X11; Linux x86_64",
}
_HINT_PLATFORMS = {"windows": "Windows", "macos": "macOS", "linux": "Linux"}
_BRANDS = {
    "chrome": "Google Chrome",
    "edge": "Microsoft Edge",
    "brave": "Brave",
}


@dataclass(frozen=True)
class BrowserIdentity:
    user_agent: str
    brand: str | None
    full_version: str
    platform: str  # the Sec-CH-UA-Platform name: Windows, macOS or Linux
    browser_id: str = ""  # only for the log line


def _os_key(platform: str | None = None) -> str:
    platform = sys.platform if platform is None else platform
    if platform == "win32":
        return "windows"
    if platform == "darwin":
        return "macos"
    return "linux"


def build_identity(
    browser_id: str, full_version: str, platform: str | None = None
) -> BrowserIdentity:
    """Build the reduced-form identity a Chrome-family browser reports."""
    os_key = _os_key(platform)
    major = full_version.split(".")[0]
    user_agent = (
        f"Mozilla/5.0 ({_OS_TOKENS[os_key]}) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{major}.0.0.0 Safari/537.36"
    )
    if browser_id == "edge":
        user_agent += f" Edg/{major}.0.0.0"
    return BrowserIdentity(
        user_agent=user_agent,
        brand=_BRANDS.get(browser_id),
        full_version=full_version,
        platform=_HINT_PLATFORMS[os_key],
        browser_id=browser_id,
    )


def _version_from_directories(exe: Path) -> str | None:
    """Windows keeps each version in a dotted-quad directory beside the exe."""
    best: tuple[int, ...] | None = None
    best_name: str | None = None
    for child in exe.parent.iterdir():
        if _VERSION_DIR.match(child.name) and child.is_dir():
            key = tuple(int(part) for part in child.name.split("."))
            if best is None or key > best:
                best, best_name = key, child.name
    return best_name


def _version_from_plist(exe: Path) -> str | None:
    # exe is <X>.app/Contents/MacOS/<name>.
    plist = exe.parent.parent / "Info.plist"
    with plist.open("rb") as handle:
        value = plistlib.load(handle).get("CFBundleShortVersionString")
    return str(value) if value else None


def _version_from_command(exe: Path) -> str | None:
    result = subprocess.run(
        [str(exe), "--version"],
        capture_output=True,
        text=True,
        timeout=_VERSION_TIMEOUT_SECONDS,
        check=False,
    )
    match = _VERSION_TEXT.search(result.stdout or "")
    return match.group(0) if match else None


def read_browser_version(exe: Path, platform: str | None = None) -> str | None:
    """Return the installed browser's full version, or None."""
    os_key = _os_key(platform)
    try:
        if os_key == "windows":
            version = _version_from_directories(exe)
        elif os_key == "macos":
            version = _version_from_plist(exe)
        else:
            version = _version_from_command(exe)
    except Exception as exc:  # noqa: BLE001 - identity is best effort
        log.info("browser version unreadable exe=%s error=%s", exe, exc)
        return None
    if not version or not version.split(".")[0].isdigit():
        log.info("browser version not found exe=%s", exe)
        return None
    return version


def detect_browser_identity(
    preference: str = "ask", platform: str | None = None
) -> BrowserIdentity | None:
    """Return the identity of the browser external sign-in would use.

    ``preference`` is the configured ``sign_in_browser``. When it is "ask",
    "embedded" or not installed, the first installed supported browser is
    used, the same fallback ``find_supported_browser`` applies.
    """
    try:
        browsers = installed_browsers()
        if not browsers:
            log.info("no supported browser installed for the sign-in identity")
            return None
        browser_id = preference if preference in browsers else next(iter(browsers))
        version = read_browser_version(browsers[browser_id], platform)
        if version is None:
            return None
        return build_identity(browser_id, version, platform)
    except Exception as exc:  # noqa: BLE001 - never break the caller
        log.info("browser identity detection failed: %s", exc)
        return None


def apply_browser_identity(profile, identity: BrowserIdentity) -> None:
    """Make ``profile`` send ``identity`` as its user agent and client hints."""
    profile.setHttpUserAgent(identity.user_agent)
    hints = profile.clientHints()
    versions = {"Not-A.Brand": "99.0.0.0"}
    if identity.brand:
        versions[identity.brand] = identity.full_version
    versions["Chromium"] = identity.full_version
    hints.setFullVersionList(versions)
    hints.setFullVersion(identity.full_version)
    hints.setPlatform(identity.platform)
