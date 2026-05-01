from PyQt6.QtCore import QUrl

from aigauge.webview.login_window import (
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
