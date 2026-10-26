import json
import subprocess
from pathlib import Path

import pytest

from aigauge.webview import external_login
from aigauge.webview.external_login import (
    ExternalLoginWorker,
    _create_websocket_connection,
    _domain_matches,
    _has_auth_cookie,
    _provider_cookies,
    find_supported_browser,
    installed_browsers,
)


def test_provider_cookie_filter_accepts_only_the_provider_domain():
    values = [
        {"name": "sessionKey", "value": "secret", "domain": ".claude.ai"},
        {"name": "pref", "value": "yes", "domain": "assets.claude.ai"},
        {"name": "sessionKey", "value": "evil", "domain": "evilclaude.ai"},
        {"name": "google", "value": "private", "domain": ".google.com"},
    ]

    filtered = _provider_cookies("claude", values)

    assert [item["value"] for item in filtered] == ["secret", "yes"]
    assert _domain_matches(".chatgpt.com", "codex")
    assert not _domain_matches("notchatgpt.com", "codex")


def test_auth_cookie_detection_handles_codex_split_tokens():
    cookies = [
        {
            "name": "__Secure-next-auth.session-token.0",
            "value": "first",
            "domain": ".chatgpt.com",
        }
    ]

    assert _has_auth_cookie("codex", cookies)
    assert not _has_auth_cookie(
        "claude",
        [{"name": "preference", "value": "x", "domain": ".claude.ai"}],
    )


def test_supported_browser_selection_is_explicit(monkeypatch, tmp_path):
    chrome = tmp_path / "chrome.exe"
    edge = tmp_path / "msedge.exe"
    chrome.write_text("browser", encoding="utf-8")
    edge.write_text("browser", encoding="utf-8")
    monkeypatch.setattr(
        external_login,
        "_browser_candidates",
        lambda: [("chrome", chrome), ("edge", edge)],
    )

    assert installed_browsers() == {"chrome": chrome, "edge": edge}
    assert find_supported_browser("edge") == edge
    assert find_supported_browser("brave") is None


def test_linux_snap_brave_launcher_is_detected(monkeypatch, tmp_path):
    launcher = tmp_path / "brave"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(external_login.sys, "platform", "linux")
    monkeypatch.setattr(
        external_login.shutil,
        "which",
        lambda name: str(launcher) if name == "brave" else None,
    )

    assert installed_browsers()["brave"] == launcher
    assert find_supported_browser("brave") == launcher


def test_snap_brave_profile_uses_snap_writable_directory(monkeypatch):
    monkeypatch.setattr(external_login.sys, "platform", "linux")
    root = external_login._browser_profile_root(Path("/snap/bin/brave"))
    assert root == Path.home() / "snap/brave/common/ai-gauge-signin"
    assert external_login._browser_profile_root(Path("/usr/bin/brave-browser")) == (
        external_login.app_data_dir() / "browser-signin"
    )


def test_snap_brave_profile_cleanup_stays_within_selected_root(tmp_path):
    root = tmp_path / "snap" / "brave" / "common" / "ai-gauge-signin"
    profile = root / "claude-123"
    profile.mkdir(parents=True)
    (profile / "SingletonLock").write_text("probe", encoding="utf-8")
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    worker = ExternalLoginWorker("claude", "https://claude.ai/login", "claude")
    worker._profile_root = root
    worker._profile_dir = profile

    worker._cleanup_profile()

    assert not profile.exists()
    assert unrelated.exists()


def test_websocket_connection_always_bypasses_loopback_proxies(monkeypatch):
    calls = []
    sentinel = object()

    monkeypatch.setattr(
        external_login.websocket,
        "create_connection",
        lambda url, **kwargs: calls.append((url, kwargs)) or sentinel,
    )

    result = _create_websocket_connection(
        "ws://127.0.0.1:43123/devtools/browser/test",
        timeout=1.5,
    )

    assert result is sentinel
    assert calls == [
        (
            "ws://127.0.0.1:43123/devtools/browser/test",
            {
                "timeout": 1.5,
                "suppress_origin": True,
                "http_no_proxy": ["127.0.0.1", "localhost"],
            },
        )
    ]


class _FakeProcess:
    def __init__(self, wait_results):
        self._wait_results = iter(wait_results)
        self.wait_timeouts = []
        self.terminated = False
        self.killed = False

    def wait(self, timeout):
        self.wait_timeouts.append(timeout)
        result = next(self._wait_results)
        if result == "timeout":
            raise subprocess.TimeoutExpired("browser", timeout)
        return result

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


@pytest.mark.parametrize(
    ("wait_results", "expected_timeouts", "terminated", "killed"),
    [
        ([0], [3], False, False),
        (["timeout", 0], [3, 2], True, False),
        (["timeout", "timeout", 0], [3, 2, 2], True, True),
    ],
)
def test_close_browser_waits_for_process_exit(
    wait_results,
    expected_timeouts,
    terminated,
    killed,
):
    worker = ExternalLoginWorker("claude", "https://claude.ai/login", "claude")
    process = _FakeProcess(wait_results)
    worker._process = process  # noqa: SLF001 - lifecycle test seam

    worker._close_browser()  # noqa: SLF001 - lifecycle test seam

    assert process.wait_timeouts == expected_timeouts
    assert process.terminated is terminated
    assert process.killed is killed
    assert worker._process is None  # noqa: SLF001


def test_stop_releases_worker_waits_immediately():
    worker = ExternalLoginWorker("claude", "https://claude.ai/login", "claude")

    worker.stop()

    assert worker._stop_event.is_set()  # noqa: SLF001 - lifecycle test seam


def test_launcher_exit_does_not_end_a_live_cdp_session(monkeypatch):
    ready = []
    failures = []
    worker = ExternalLoginWorker(
        "claude",
        "https://claude.ai/login",
        "claude",
        browser_id="edge",
    )

    class HandedOffProcess:
        @staticmethod
        def poll():
            return 0

    worker._process = HandedOffProcess()  # noqa: SLF001 - process handoff seam
    worker._debug_port = 43123  # noqa: SLF001 - CDP test seam
    monkeypatch.setattr(worker, "_discover_websocket", lambda: True)
    monkeypatch.setattr(
        worker,
        "_read_cookies",
        lambda: [
            {"name": "sessionKey", "value": "secret", "domain": ".claude.ai"}
        ],
    )
    worker.session_ready.connect(ready.append)
    worker.failed.connect(failures.append)

    worker._poll_for_session()  # noqa: SLF001 - synchronous polling seam

    assert len(ready) == 1
    assert failures == []


def test_port_reservation_failure_removes_temporary_profile(monkeypatch, tmp_path):
    app_data = tmp_path / "app-data"
    failures = []
    worker = ExternalLoginWorker("claude", "https://claude.ai/login", "claude")
    worker.failed.connect(failures.append)

    monkeypatch.setattr(external_login, "app_data_dir", lambda: app_data)
    monkeypatch.setattr(
        external_login,
        "find_supported_browser",
        lambda browser_id: Path("chrome.exe"),
    )
    monkeypatch.setattr(
        external_login,
        "_reserve_local_port",
        lambda: (_ for _ in ()).throw(OSError("no ports available")),
    )

    worker.run()

    assert failures == ["Could not prepare the temporary browser: no ports available"]
    assert worker._profile_dir is None  # noqa: SLF001
    assert list((app_data / "browser-signin").iterdir()) == []


def test_claude_api_domain_matches_only_the_console_host():
    for domain in ("platform.claude.com", ".platform.claude.com"):
        assert _domain_matches(domain, "claude_api"), domain
    for domain in ("claude.ai", ".claude.ai", ".claude.com", "claude.com"):
        assert not _domain_matches(domain, "claude_api"), domain
    assert not _domain_matches("platform.claude.com.evil.test", "claude_api")
    assert not _domain_matches("notplatform.claude.com", "claude_api")
    # And the Claude kind does not take the Console's cookies.
    assert not _domain_matches("platform.claude.com", "claude")


def _claude_api_worker_ready(cookies: list[dict]) -> bool:
    worker = ExternalLoginWorker(
        "claude_api", "https://platform.claude.com/login", "claude_api"
    )
    return worker._session_is_ready(  # noqa: SLF001
        _provider_cookies("claude_api", cookies)
    )


def test_claude_ai_session_key_does_not_make_a_claude_api_session_ready():
    claude_ai = [{"name": "sessionKey", "value": "v", "domain": ".claude.ai"}]

    assert not _claude_api_worker_ready(claude_ai)
    # The same cookie is exactly what readies a Claude session.
    assert _has_auth_cookie("claude", _provider_cookies("claude", claude_ai))


def test_console_session_cookies_make_a_claude_api_session_ready():
    v3 = [{"name": "sessionKeyV3", "value": "v", "domain": "platform.claude.com"}]
    key = [{"name": "sessionKey", "value": "v", "domain": ".platform.claude.com"}]
    only_light = [
        {"name": "sessionKeyLC", "value": "v", "domain": "platform.claude.com"},
        {"name": "__cf_bm", "value": "v", "domain": ".claude.com"},
    ]

    assert _claude_api_worker_ready(v3)
    assert _claude_api_worker_ready(key)
    assert not _claude_api_worker_ready(only_light)
