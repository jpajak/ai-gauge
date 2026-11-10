"""The sign-in browser's identity for the Claude Console profile."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aigauge.webview import browser_identity, profile as profile_module
from aigauge.webview.browser_identity import (
    BrowserIdentity,
    build_identity,
    detect_browser_identity,
    read_browser_version,
)

CHROME_157_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"
)


def _install(tmp_path: Path, *versions: str, junk: tuple[str, ...] = ()) -> Path:
    app = tmp_path / "Chrome" / "Application"
    app.mkdir(parents=True)
    exe = app / "chrome.exe"
    exe.write_bytes(b"")
    for name in versions:
        (app / name).mkdir()
    for name in junk:
        (app / name).mkdir()
    return exe


def test_windows_version_is_the_highest_dotted_quad_directory(tmp_path):
    exe = _install(
        tmp_path,
        "157.0.8103.38",
        "157.0.8103.39",
        "99.0.1.2",
        "156.0.9999.1",
        junk=("Locales", "157.0.8103", "157.0.8103.39.1", "v157.0.8103.40", "SetupMetrics"),
    )

    assert read_browser_version(exe, "win32") == "157.0.8103.39"


def test_windows_version_compares_numbers_not_text(tmp_path):
    exe = _install(tmp_path, "99.0.1.2", "100.0.1.2")

    assert read_browser_version(exe, "win32") == "100.0.1.2"


def test_windows_version_none_when_dir_missing_or_no_version_directory(tmp_path):
    assert read_browser_version(tmp_path / "gone" / "chrome.exe", "win32") is None
    assert read_browser_version(_install(tmp_path, junk=("Locales",)), "win32") is None


def test_macos_version_comes_from_info_plist(tmp_path):
    import plistlib

    contents = tmp_path / "Google Chrome.app" / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    exe = contents / "MacOS" / "Google Chrome"
    exe.write_bytes(b"")
    (contents / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleShortVersionString": "157.0.8103.39"})
    )

    assert read_browser_version(exe, "darwin") == "157.0.8103.39"
    assert read_browser_version(tmp_path / "Nope.app" / "Contents" / "MacOS" / "x", "darwin") is None


def test_linux_version_comes_from_version_flag(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, "Google Chrome 157.0.8103.39 \n", "")

    monkeypatch.setattr(browser_identity.subprocess, "run", fake_run)
    exe = tmp_path / "google-chrome"

    assert read_browser_version(exe, "linux") == "157.0.8103.39"
    assert calls[0][0] == [str(exe), "--version"]
    assert calls[0][1]["timeout"] == 5


def test_linux_version_failure_returns_none(monkeypatch, tmp_path):
    def boom(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 5)

    monkeypatch.setattr(browser_identity.subprocess, "run", boom)
    assert read_browser_version(tmp_path / "google-chrome", "linux") is None

    monkeypatch.setattr(
        browser_identity.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "no digits here", ""),
    )
    assert read_browser_version(tmp_path / "google-chrome", "linux") is None


def test_chrome_identity_matches_real_chrome_157():
    identity = build_identity("chrome", "157.0.8103.39", "win32")

    assert identity.user_agent == CHROME_157_UA
    assert identity.brand == "Google Chrome"
    assert identity.full_version == "157.0.8103.39"
    assert identity.platform == "Windows"


def test_identity_strings_per_browser_and_os():
    edge = build_identity("edge", "150.0.1.2", "win32")
    assert edge.user_agent == (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0"
    )
    assert edge.brand == "Microsoft Edge"
    assert build_identity("brave", "150.1.2.3", "win32").brand == "Brave"
    assert build_identity("chromium", "150.1.2.3", "linux").brand is None

    mac = build_identity("chrome", "157.0.8103.39", "darwin")
    assert "(Macintosh; Intel Mac OS X 10_15_7)" in mac.user_agent
    assert mac.platform == "macOS"
    linux = build_identity("chrome", "157.0.8103.39", "linux")
    assert "(X11; Linux x86_64)" in linux.user_agent
    assert linux.platform == "Linux"
    assert "Edg/" not in linux.user_agent


def _fake_browsers(monkeypatch, tmp_path, ids):
    found = {}
    for browser_id in ids:
        app = tmp_path / browser_id / "Application"
        app.mkdir(parents=True)
        (app / "browser.exe").write_bytes(b"")
        (app / {"chrome": "157.0.8103.39", "edge": "150.0.1.1"}.get(browser_id, "140.0.0.1")).mkdir()
        found[browser_id] = app / "browser.exe"
    monkeypatch.setattr(browser_identity, "installed_browsers", lambda: found)


def test_detect_uses_the_configured_browser_when_installed(monkeypatch, tmp_path):
    _fake_browsers(monkeypatch, tmp_path, ["chrome", "edge"])

    identity = detect_browser_identity("edge", "win32")

    assert identity is not None
    assert identity.browser_id == "edge"
    assert identity.full_version == "150.0.1.1"


@pytest.mark.parametrize("preference", ["ask", "embedded", "brave"])
def test_detect_falls_back_to_the_first_installed_browser(
    monkeypatch, tmp_path, preference
):
    _fake_browsers(monkeypatch, tmp_path, ["chrome", "edge"])

    identity = detect_browser_identity(preference, "win32")

    assert identity is not None
    assert identity.browser_id == "chrome"
    assert identity.user_agent == CHROME_157_UA


def test_detect_returns_none_when_nothing_is_installed(monkeypatch):
    monkeypatch.setattr(browser_identity, "installed_browsers", lambda: {})

    assert detect_browser_identity("ask", "win32") is None


def test_detect_returns_none_when_installed_browser_version_is_unreadable(
    monkeypatch, tmp_path
):
    exe = tmp_path / "chrome.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(browser_identity, "installed_browsers", lambda: {"chrome": exe})

    assert detect_browser_identity("chrome", "win32") is None


def test_detect_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("registry on fire")

    monkeypatch.setattr(browser_identity, "installed_browsers", boom)

    assert detect_browser_identity("ask") is None


_APPLY_SCRIPT = r"""
import json, sys
from PyQt6.QtWebEngineCore import QWebEngineProfile
from PyQt6.QtWidgets import QApplication

from aigauge.webview.browser_identity import apply_browser_identity, build_identity

app = QApplication(sys.argv)
out = {}
for browser_id in ("chrome", "chromium"):
    profile = QWebEngineProfile(app)  # off the record: nothing on disk
    apply_browser_identity(profile, build_identity(browser_id, "157.0.8103.39", "win32"))
    hints = profile.clientHints()
    out[browser_id] = {
        "ua": profile.httpUserAgent(),
        "versions": hints.fullVersionList(),
        "full_version": hints.fullVersion(),
        "platform": hints.platform(),
    }
print("RESULT " + json.dumps(out))
"""


def test_apply_browser_identity_on_a_real_offscreen_profile():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    src = str(Path(browser_identity.__file__).resolve().parents[2])
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _APPLY_SCRIPT],
            capture_output=True,
            text=True,
            timeout=90,
            env=env,
        )
    except subprocess.TimeoutExpired:
        pytest.skip("Qt WebEngine did not start")
    line = next(
        (item for item in proc.stdout.splitlines() if item.startswith("RESULT ")), None
    )
    if line is None:
        pytest.skip(f"Qt WebEngine unavailable here: {proc.stderr[-300:]}")
    result = json.loads(line[len("RESULT "):])

    chrome = result["chrome"]
    assert chrome["ua"] == CHROME_157_UA
    assert chrome["versions"] == {
        "Not-A.Brand": "99.0.0.0",
        "Google Chrome": "157.0.8103.39",
        "Chromium": "157.0.8103.39",
    }
    assert chrome["full_version"] == "157.0.8103.39"
    assert chrome["platform"] == "Windows"
    assert result["chromium"]["versions"] == {
        "Not-A.Brand": "99.0.0.0",
        "Chromium": "157.0.8103.39",
    }


class _FakeSignal:
    def connect(self, slot):
        pass


class _FakeCookieStore:
    cookieAdded = _FakeSignal()
    cookieRemoved = _FakeSignal()

    def loadAllCookies(self):
        pass


class _FakeProfile:
    PersistentCookiesPolicy = profile_module.QWebEngineProfile.PersistentCookiesPolicy

    def __init__(self, name):
        self.name = name
        self.calls = []

    def cookieStore(self):
        return _FakeCookieStore()

    def __getattr__(self, attr):
        if attr.startswith("set"):
            return lambda *args: self.calls.append((attr, args))
        raise AttributeError(attr)


@pytest.fixture
def fake_profiles(monkeypatch):
    monkeypatch.setattr(profile_module, "QWebEngineProfile", _FakeProfile)
    monkeypatch.setattr(profile_module, "_profiles", {})
    monkeypatch.setattr(profile_module, "_cookie_watchers", {})
    # The conftest redirects APPDATA, so the profile directory is under tmp.
    applied = []
    monkeypatch.setattr(
        profile_module,
        "apply_browser_identity",
        lambda profile, identity: applied.append((profile.name, identity)),
    )
    return applied


def _user_agents(profile):
    return [args[0] for attr, args in profile.calls if attr == "setHttpUserAgent"]


def test_get_profile_applies_the_identity_for_claude_api_only(
    monkeypatch, fake_profiles, caplog
):
    identity = build_identity("chrome", "157.0.8103.39", "win32")
    seen = []
    monkeypatch.setattr(
        profile_module,
        "detect_browser_identity",
        lambda preference: seen.append(preference) or identity,
    )
    caplog.set_level("INFO", logger="aigauge.webview.profile")

    api = profile_module.get_profile("claude_api")
    claude = profile_module.get_profile("claude")

    assert fake_profiles == [("ai-gauge-claude_api", identity)]
    assert seen == ["ask"]
    assert _user_agents(claude) == [profile_module._REALISTIC_UA]  # noqa: SLF001
    assert _user_agents(api)[0] == profile_module._REALISTIC_UA  # noqa: SLF001
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "browser identity provider=claude_api browser=chrome "
        "version=157.0.8103.39 applied=True" in text
        for text in messages
    )
    created = next(text for text in messages if "provider=claude_api storage=" in text)
    assert repr(CHROME_157_UA) in created


def test_get_profile_keeps_the_fixed_identity_when_detection_finds_nothing(
    monkeypatch, fake_profiles, caplog
):
    monkeypatch.setattr(profile_module, "detect_browser_identity", lambda preference: None)
    caplog.set_level("INFO", logger="aigauge.webview.profile")

    profile = profile_module.get_profile("claude_api")

    assert fake_profiles == []
    assert _user_agents(profile) == [profile_module._REALISTIC_UA]  # noqa: SLF001
    assert any("applied=False" in record.getMessage() for record in caplog.records)


def test_get_profile_survives_a_failing_detection(monkeypatch, fake_profiles):
    def boom(preference):
        raise RuntimeError("detection exploded")

    monkeypatch.setattr(profile_module, "detect_browser_identity", boom)

    profile = profile_module.get_profile("claude_api")

    assert profile_module.get_profile("claude_api") is profile
    assert fake_profiles == []


def test_the_identity_dataclass_is_frozen():
    identity = BrowserIdentity("ua", None, "1.2.3.4", "Linux")
    with pytest.raises(Exception):
        identity.brand = "x"  # type: ignore[misc]
