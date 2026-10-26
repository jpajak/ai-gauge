import json
import shutil
import subprocess

import pytest
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QPushButton

from aigauge.webview import login_window
from aigauge.webview.login_window import (
    LoginWindow,
    VERIFY_TARGETS,
    _host_allowed,
    _is_google_host,
    _safe_url_for_log,
)


def _run_target_js(tmp_path, provider: str, state: dict) -> bool:
    if shutil.which("node") is None:
        pytest.skip("Node.js not available")
    harness = tmp_path / "verify-harness.js"
    script = tmp_path / "verify-target.js"
    payload = tmp_path / "verify-state.json"
    harness.write_text(
        """
const fs = require('fs');
const state = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
global.document = {
  body: {innerText: state.body}, title: state.title,
  querySelectorAll: () => [],
};
global.location = state.location;
const result = eval(fs.readFileSync(process.argv[3], 'utf8'));
process.stdout.write(JSON.stringify(result));
""",
        encoding="utf-8",
    )
    script.write_text(VERIFY_TARGETS[provider][1], encoding="utf-8")
    payload.write_text(json.dumps(state), encoding="utf-8")
    completed = subprocess.run(
        ["node", str(harness), str(payload), str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return bool(json.loads(completed.stdout))


def test_google_hosts_are_detected_and_allowlisted():
    assert _is_google_host("accounts.google.com")
    assert _is_google_host("google.com")
    assert _host_allowed("accounts.google.com")
    assert _host_allowed("accounts.youtube.com")


def test_logged_blocked_url_drops_query_and_fragment():
    url = QUrl(
        "https://accounts.google.com/o/oauth2/v2/auth?"
        "login_hint=person@example.com#frag"
    )

    assert _safe_url_for_log(url) == "https://accounts.google.com/o/oauth2/v2/auth"


@pytest.mark.parametrize(("body", "expected"), [
    ("Plan limits Weekly limit Resets in 6d 10h 96% left Credits 0", True),
    ("Plan limits 5-hour limit 100% left Weekly limit 96% left", True),
    ("Plan limits Weekly limit Credits Up to 40% off", False),
    ("Usage Overview Analytics Usage history Plan usage By product", False),
    ("Log in to ChatGPT", False),
])
def test_codex_verification_reads_overview_limits(tmp_path, body, expected):
    assert VERIFY_TARGETS["codex"][0] == "https://chatgpt.com/settings/usage?tab=overview"
    state = {
        "body": body, "title": "ChatGPT",
        "location": {"hostname": "chatgpt.com", "pathname": "/settings/usage"},
    }
    assert _run_target_js(tmp_path, "codex", state) is expected


def test_claude_verification_accepts_authenticated_home_shell(tmp_path):
    state = {
        "body": (
            "Home Code New Projects Artifacts Scheduled Customize Chats and tasks "
            "How can I help you today? John Max"
        ),
        "title": "New chat - Claude",
        "location": {
            "hostname": "claude.ai",
            "pathname": "/new",
            "hash": "#settings/usage",
        },
    }

    assert _run_target_js(tmp_path, "claude", state)


def test_claude_verification_rejects_login_ui_on_authenticated_route(tmp_path):
    state = {
        "body": "Log in to Claude Continue with Google Create an account",
        "title": "Claude",
        "location": {
            "hostname": "claude.ai",
            "pathname": "/new",
            "hash": "#settings/usage",
        },
    }

    assert not _run_target_js(tmp_path, "claude", state)


def test_claude_verification_rejects_login_page(tmp_path):
    state = {
        "body": "Log in to Claude",
        "title": "Claude",
        "location": {
            "hostname": "claude.ai",
            "pathname": "/login",
            "hash": "",
        },
    }

    assert not _run_target_js(tmp_path, "claude", state)


def test_claude_verification_rejects_usage_text_on_another_host(tmp_path):
    state = {
        "body": "Plan usage limits Current session All models",
        "title": "Claude",
        "location": {
            "hostname": "example.com",
            "pathname": "/new",
            "hash": "#settings/usage",
        },
    }

    assert not _run_target_js(tmp_path, "claude", state)


def test_external_failure_returns_to_choice_without_opening_embedded():
    calls = []

    class Status:
        def setText(self, value):  # noqa: N802 - Qt-shaped test double
            calls.append(("status", value))

        def setStyleSheet(self, value):  # noqa: N802 - Qt-shaped test double
            calls.append(("style", value))

    class Dialog:
        _closing = False
        _status = Status()

        def _show_browser_choice(self, message):
            calls.append(("choice", message))

    LoginWindow._on_external_failed(Dialog(), "browser closed")

    assert ("choice", "browser closed") in calls
    assert all(name != "embedded" for name, _value in calls)


def test_visible_page_owns_the_dialog_default_action(qtbot):
    continue_btn = QPushButton("Continue")
    verify_btn = QPushButton("I'm signed in")
    qtbot.addWidget(continue_btn)
    qtbot.addWidget(verify_btn)

    class Dialog:
        _continue_btn = continue_btn
        _verify_btn = verify_btn

    dialog = Dialog()

    LoginWindow._set_default_action(dialog, continue_btn)
    assert continue_btn.isDefault()
    assert not verify_btn.isDefault()

    LoginWindow._set_default_action(dialog, verify_btn)
    assert not continue_btn.isDefault()
    assert verify_btn.isDefault()

    LoginWindow._set_default_action(dialog, None)
    assert not continue_btn.isDefault()
    assert not verify_btn.isDefault()


def test_embedded_auth_cookie_schedules_automatic_verification(monkeypatch):
    timers = []

    class Status:
        def setText(self, value):  # noqa: N802 - Qt-shaped test double
            return None

        def setStyleSheet(self, value):  # noqa: N802 - Qt-shaped test double
            return None

    class Cookie:
        @staticmethod
        def name():
            return b"sessionKey"

        @staticmethod
        def domain():
            return ".claude.ai"

    class Dialog:
        _closing = False
        _embedded_active = True
        _provider = "claude"
        _embedded_auth_seen = False
        _session_may_have_changed = False
        _embedded_verify_scheduled = False
        _verifying = False
        _embedded_status = Status()

        @staticmethod
        def _set_label_status(label, text, color):
            label.setText(text)
            label.setStyleSheet(color)

        def _verify_after_embedded_cookie(self):
            return None

    monkeypatch.setattr(
        login_window.QTimer,
        "singleShot",
        lambda delay, callback: timers.append((delay, callback)),
    )
    dialog = Dialog()

    LoginWindow._on_embedded_cookie_added(dialog, Cookie())

    assert dialog._embedded_auth_seen is True
    assert dialog._session_may_have_changed is True
    assert dialog._embedded_verify_scheduled is True
    assert timers[0][0] == 1000


def test_authenticated_embedded_shell_closes_without_manual_verification(monkeypatch):
    timers = []
    accepted = []

    class Status:
        def setText(self, value):  # noqa: N802 - Qt-shaped test double
            return None

        def setStyleSheet(self, value):  # noqa: N802 - Qt-shaped test double
            return None

    class Dialog:
        _closing = False
        _embedded_active = True
        _embedded_auth_seen = False
        _session_may_have_changed = False
        _embedded_verify_scheduled = False
        _verifying = False
        _embedded_status = Status()

        @staticmethod
        def _set_label_status(label, text, color):
            label.setText(text)
            label.setStyleSheet(color)

        def _accept_embedded_session(self):
            accepted.append(True)

    monkeypatch.setattr(
        login_window.QTimer,
        "singleShot",
        lambda delay, callback: timers.append((delay, callback)),
    )
    dialog = Dialog()

    LoginWindow._on_embedded_shell_result(dialog, True)

    assert dialog._embedded_auth_seen is True
    assert dialog._session_may_have_changed is True
    assert dialog._embedded_verify_scheduled is True
    assert timers[0][0] == 250
    timers[0][1]()
    assert accepted == [True]


def test_stopping_external_login_waits_for_worker_cleanup():
    calls = []

    class Worker:
        def stop(self):
            calls.append("stop")

        def isRunning(self):  # noqa: N802 - Qt-shaped test double
            calls.append("is_running")
            return True

        def wait(self):
            calls.append("wait")
            return True

    class Dialog:
        _external_worker = Worker()

    dialog = Dialog()

    LoginWindow._stop_external_login(dialog)

    assert calls == ["stop", "is_running", "wait"]
    assert dialog._external_worker is None


def test_closed_dialog_does_not_start_external_login():
    class Dialog:
        _closing = True
        _external_worker = None

    dialog = Dialog()

    LoginWindow._start_external_login(dialog)

    assert dialog._external_worker is None


_CLAUDE_API_HARNESS = """
const fs = require('fs');
const state = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const fetchCalls = [];
global.window = {};
global.document = {
  body: {innerText: 'Log in to Claude Console'}, title: 'Claude Console',
  querySelectorAll: () => [],
};
global.location = state.location;
global.fetch = (url, init) => {
  fetchCalls.push([url, init]);
  if (state.reject) return Promise.reject(new Error('network down'));
  return Promise.resolve({
    status: state.status,
    json: () => state.html
      ? Promise.reject(new SyntaxError('Unexpected token <'))
      : Promise.resolve(state.body),
  });
};
const script = fs.readFileSync(process.argv[3], 'utf8');
(async () => {
  const calls = [];
  calls.push(eval(script));
  for (let i = 0; i < 5; i++) {
    await new Promise(resolve => setTimeout(resolve, 5));
    calls.push(eval(script));
  }
  process.stdout.write(JSON.stringify({calls, fetchCalls}));
})();
"""


def _run_claude_api_target(tmp_path, **state) -> dict:
    if shutil.which("node") is None:
        pytest.skip("Node.js not available")
    state.setdefault(
        "location", {"hostname": "platform.claude.com", "pathname": "/settings/billing"}
    )
    harness = tmp_path / "claude-api-harness.js"
    script = tmp_path / "claude-api-target.js"
    payload = tmp_path / "claude-api-state.json"
    harness.write_text(_CLAUDE_API_HARNESS, encoding="utf-8")
    script.write_text(VERIFY_TARGETS["claude_api"][1], encoding="utf-8")
    payload.write_text(json.dumps(state), encoding="utf-8")
    completed = subprocess.run(
        ["node", str(harness), str(payload), str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(completed.stdout)


def test_claude_api_verification_targets_the_billing_page():
    assert VERIFY_TARGETS["claude_api"][0] == (
        "https://platform.claude.com/settings/billing"
    )


def test_claude_api_verification_is_false_off_the_console_and_never_fetches(tmp_path):
    result = _run_claude_api_target(
        tmp_path,
        location={"hostname": "claude.ai", "pathname": "/settings/billing"},
        status=200,
        body=[{"uuid": "x"}],
    )

    assert result["calls"] == [False] * 6
    assert result["fetchCalls"] == []


def test_claude_api_verification_first_call_only_starts_the_fetch(tmp_path):
    result = _run_claude_api_target(tmp_path, status=200, body=[{"uuid": "x"}])

    assert result["calls"][0] is False
    assert len(result["fetchCalls"]) == 1
    url, init = result["fetchCalls"][0]
    assert url == "/api/organizations"
    assert init["headers"]["accept"] == "application/json"


def test_claude_api_verification_is_true_on_a_later_call_for_a_200_array(tmp_path):
    result = _run_claude_api_target(tmp_path, status=200, body=[{"uuid": "x"}])

    assert result["calls"][0] is False
    assert result["calls"][-1] is True
    # One fetch only, however often the page is polled.
    assert len(result["fetchCalls"]) == 1


@pytest.mark.parametrize(
    "state",
    [
        {"status": 403, "body": {"error": {"type": "permission_error"}}},
        {"status": 401, "body": {"error": {"type": "authentication_error"}}},
        {"status": 403, "body": [{"uuid": "x"}]},
        {"status": 200, "body": {"organizations": []}},
        {"status": 200, "body": None},
        {"status": 200, "html": True},
        {"status": 403, "html": True},
        {"reject": True},
    ],
    ids=[
        "403-json", "401-json", "403-array", "200-object", "200-null",
        "200-html", "403-html", "network-error",
    ],
)
def test_claude_api_verification_stays_false_unless_signed_in(tmp_path, state):
    result = _run_claude_api_target(tmp_path, **state)

    assert result["calls"] == [False] * 6
    assert len(result["fetchCalls"]) == 1


def test_claude_api_verification_ignores_login_text(tmp_path):
    # The harness page text says "Log in to Claude Console"; only the JSON
    # route decides, so a 200 array still verifies.
    result = _run_claude_api_target(tmp_path, status=200, body=[])

    assert result["calls"][-1] is True
