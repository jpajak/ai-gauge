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
    _opencode_workspace_shell_visible,
    _provider_cookies,
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


def test_opencode_auth_detection_recognizes_current_session_cookie_only():
    assert not _has_auth_cookie(
        "opencode_go",
        [
            {
                "name": "authorization",
                "value": "oauth-in-progress",
                "domain": "auth.opencode.ai",
            },
            {
                "name": "provider",
                "value": "google",
                "domain": "auth.opencode.ai",
            },
            {"name": "theme", "value": "dark", "domain": ".opencode.ai"},
        ],
    )
    assert _has_auth_cookie(
        "opencode_go",
        [{"name": "auth", "value": "session", "domain": "opencode.ai"}],
    )


def test_opencode_auth_detection_keeps_legacy_cookie_names():
    for name in ("opencode-session", "opencode.sid"):
        assert _has_auth_cookie(
            "opencode_go",
            [{"name": name, "value": "session", "domain": ".opencode.ai"}],
        )


def test_opencode_workspace_requires_authenticated_shell():
    shell = "Default Go Usage API Keys Members Billing Settings"

    assert _opencode_workspace_shell_visible(
        "https://opencode.ai/workspace/wrk_test/go",
        shell,
    )
    assert not _opencode_workspace_shell_visible(
        "https://opencode.ai/workspace/wrk_test/go",
        "",
    )
    assert not _opencode_workspace_shell_visible(
        "https://auth.opencode.ai/login",
        shell,
    )
    assert not _opencode_workspace_shell_visible(
        "https://opencode.ai.evil.example/workspace/wrk_test/go",
        shell,
    )


def test_opencode_session_requires_cookie_and_rendered_workspace(monkeypatch):
    worker = ExternalLoginWorker(
        "opencode_go",
        "https://opencode.ai/workspace/wrk_test/go",
        "opencode_go",
    )
    cookies = [{"name": "auth", "value": "session", "domain": "opencode.ai"}]

    monkeypatch.setattr(worker, "_opencode_workspace_ready", lambda: False)
    assert not worker._session_is_ready(cookies)  # noqa: SLF001

    monkeypatch.setattr(worker, "_opencode_workspace_ready", lambda: True)
    assert worker._session_is_ready(cookies)  # noqa: SLF001


def test_opencode_workspace_readiness_uses_rendered_page_state(monkeypatch):
    target_url = "https://opencode.ai/workspace/wrk_test/go"
    shell = "Default Go Usage API Keys Members Billing Settings"
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [
                {
                    "type": "page",
                    "url": target_url,
                    "webSocketDebuggerUrl": (
                        "ws://127.0.0.1:43123/devtools/page/test"
                    ),
                }
            ]

    class Session:
        trust_env = True

        def get(self, url, timeout):
            calls.append((url, timeout, self.trust_env))
            return Response()

    class Connection:
        def __init__(self):
            self.sent = []
            self.closed = False

        def send(self, message):
            self.sent.append(json.loads(message))

        def recv(self):
            return json.dumps(
                {
                    "id": 3,
                    "result": {
                        "result": {
                            "value": {"url": target_url, "body_text": shell}
                        }
                    },
                }
            )

        def close(self):
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(external_login.requests, "Session", Session)
    monkeypatch.setattr(
        external_login,
        "_create_websocket_connection",
        lambda url, timeout: connection,
    )
    worker = ExternalLoginWorker("opencode_go", target_url, "opencode_go")
    worker._debug_port = 43123  # noqa: SLF001 - CDP test seam

    assert worker._opencode_workspace_ready()  # noqa: SLF001
    assert calls == [("http://127.0.0.1:43123/json/list", 0.5, False)]
    assert connection.sent[0]["method"] == "Runtime.evaluate"
    assert connection.closed


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


def test_port_reservation_failure_removes_temporary_profile(monkeypatch, tmp_path):
    app_data = tmp_path / "app-data"
    failures = []
    worker = ExternalLoginWorker("claude", "https://claude.ai/login", "claude")
    worker.failed.connect(failures.append)

    monkeypatch.setattr(external_login, "app_data_dir", lambda: app_data)
    monkeypatch.setattr(
        external_login,
        "find_supported_browser",
        lambda: Path("chrome.exe"),
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
