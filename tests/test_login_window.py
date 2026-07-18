from PyQt6.QtCore import QUrl

from aigauge.webview.login_window import (
    LoginWindow,
    VERIFY_TARGETS,
    _host_allowed,
    _is_google_host,
    _safe_url_for_log,
)


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

def test_opencode_go_has_verify_target():
    url, check_js = VERIFY_TARGETS["opencode_go"]

    assert url.startswith("https://opencode.ai/workspace/")
    assert "location.hostname === 'opencode.ai'" in check_js
    assert "workspacePath" in check_js
    assert "'usage', 'api keys', 'members', 'billing', 'settings'" in check_js
    assert "Rolling Usage" not in check_js
    assert "Weekly Usage" not in check_js
    assert "Monthly Usage" not in check_js


def test_codex_verification_accepts_weekly_only_usage_page():
    _, check_js = VERIFY_TARGETS["codex"]

    success_check = check_js.split("return true", maxsplit=1)[0]
    assert "/Weekly usage limit/i.test(text)" in success_check
    assert "/5 hour usage limit/i.test(text)" not in success_check
    assert (
        """querySelectorAll('button,a,[role="tab"],[role="button"]')""" in check_js
    )
    assert ",div,span,p" not in check_js


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
