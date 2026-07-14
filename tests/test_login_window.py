from PyQt6.QtCore import QUrl

from aigauge.webview.login_window import (
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
    assert "Rolling Usage" in check_js
    assert "Weekly Usage" in check_js
    assert "Monthly Usage" in check_js


def test_codex_verification_accepts_weekly_only_usage_page():
    _, check_js = VERIFY_TARGETS["codex"]

    success_check = check_js.split("return true", maxsplit=1)[0]
    assert "/Weekly usage limit/i.test(text)" in success_check
    assert "/5 hour usage limit/i.test(text)" not in success_check
    assert (
        """querySelectorAll('button,a,[role="tab"],[role="button"]')""" in check_js
    )
    assert ",div,span,p" not in check_js
