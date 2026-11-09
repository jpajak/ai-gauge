import logging
from typing import Any, cast

from aigauge.webview.scraper import HeadlessScraper
from PyQt6.QtCore import QUrl


class _LoginPage:
    def url(self):
        return QUrl("https://claude.ai/login")

    def title(self):
        return "Claude"


class _FailedLoginScrape:
    _provider = "claude"
    _finished = False
    _max_progress = 100
    _last_load_status = "LoadStartedStatus"
    _last_load_is_error_page = False
    _page: Any = _LoginPage()

    def __init__(self):
        self.result = None

    def _finish(self, payload, error):
        self.result = (payload, error)


def test_failed_load_on_claude_login_reports_auth_instead_of_transport_failure():
    scrape = _FailedLoginScrape()
    HeadlessScraper._on_load_finished(cast(Any, scrape), False)
    assert scrape.result == ({"logged_out": True, "url": "https://claude.ai/login"}, "")


def test_failed_load_on_sign_in_page_at_new_route_reports_auth():
    scrape = _FailedLoginScrape()
    scrape._page = type("Page", (), {
        "url": lambda self: QUrl("https://claude.ai/new#settings/usage"),
        "title": lambda self: "Sign in - Claude",
    })()
    HeadlessScraper._on_load_finished(cast(Any, scrape), False)
    assert scrape.result == ({"logged_out": True, "url": "https://claude.ai/new"}, "")


def test_failed_load_on_other_page_remains_transport_failure():
    scrape = _FailedLoginScrape()
    scrape._page = type("Page", (), {
        "url": lambda self: QUrl("https://claude.ai/new"),
        "title": lambda self: "Claude",
    })()
    HeadlessScraper._on_load_finished(cast(Any, scrape), False)
    assert scrape.result == (None, "page failed to load")


def test_extractor_retry_limit_is_retryable_transport_error():
    assert "extractor retry limit exceeded" in HeadlessScraper._RETRYABLE_ERRORS


def test_claude_retry_exhaustion_logs_only_safe_page_flags(caplog):
    class UsagePage:
        def url(self):
            return QUrl("https://claude.ai/settings/usage")

        def title(self):
            return "Claude"

    class Scrape:
        _finished = False
        _provider = "claude"
        _extractor_reruns = 5
        _page = UsagePage()
        result = None

        def _finish(self, payload, error):
            self.result = (payload, error)

    scrape = Scrape()
    with caplog.at_level(logging.WARNING, logger="aigauge.scraper"):
        HeadlessScraper._on_js_result(cast(Any, scrape), {
            "__retry_after_ms": 1200,
            "url": "https://claude.ai/settings/usage?secret=do-not-log",
            "title": "Claude",
            "body_text": "Your usage Current session This week 25% used",
            "session": None,
            "weekly_all": None,
        })
    assert scrape.result == (None, "extractor retry limit exceeded")
    assert "extractor_retry_exhausted" in caplog.text
    assert "url_query=True" in caplog.text
    assert "do-not-log" not in caplog.text
    assert "Current session This week" not in caplog.text
