"""Keep-alive (linger_ms) behaviour of HeadlessScraper against a fake page and view."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QUrl

from aigauge.webview import scraper as scraper_module

BILLING = "https://platform.claude.com/settings/billing"


class _Signal:
    def connect(self, slot):
        pass

    def disconnect(self, slot):
        pass


class _FakePage:
    LifecycleState = SimpleNamespace(Active="Active", Discarded="Discarded")
    WebAction = SimpleNamespace(Stop="Stop")

    def __init__(self, calls):
        self.calls = calls
        self.js: list = []
        self.loadFinished = _Signal()
        self.loadProgress = _Signal()
        self.urlChanged = _Signal()
        self.renderProcessTerminated = _Signal()

    def load(self, url):
        self.calls.append(("page.load", url.toString()))

    def setVisible(self, visible):
        self.calls.append(("page.setVisible", visible))

    def setLifecycleState(self, state):
        self.calls.append(("page.setLifecycleState", state))

    def triggerAction(self, action):
        self.calls.append(("page.triggerAction", action))

    def runJavaScript(self, script, callback=None):
        self.calls.append(("page.runJavaScript", script))
        self.js.append((script, callback))

    def url(self):
        return QUrl(BILLING)

    def title(self):
        return "Billing"

    def deleteLater(self):
        self.calls.append(("page.deleteLater",))


class _FakeView:
    def __init__(self, calls):
        self.calls = calls

    def setPage(self, page):
        self.calls.append(("view.setPage", None if page is None else "page"))

    def resize(self, w, h):
        pass

    def width(self):
        return 1280

    def height(self):
        return 900

    def stop(self):
        self.calls.append(("view.stop",))

    def show(self):
        self.calls.append(("view.show",))

    def setVisible(self, visible):
        self.calls.append(("view.setVisible", visible))

    def deleteLater(self):
        self.calls.append(("view.deleteLater",))


class _FakeTimer:
    pending: list = []

    def __init__(self, parent=None):
        self.timeout = _Signal()

    def setSingleShot(self, flag):
        pass

    def start(self, ms):
        pass

    def stop(self):
        pass

    @staticmethod
    def singleShot(ms, fn):
        _FakeTimer.pending.append((ms, fn))


class _Harness:
    def __init__(self, monkeypatch, linger_ms):
        self.calls: list = []
        self.now = [100.0]
        _FakeTimer.pending = []
        calls = self.calls
        profile = SimpleNamespace(
            persistentStoragePath=lambda: "profile", httpUserAgent=lambda: "ua"
        )
        monkeypatch.setattr(scraper_module, "get_profile", lambda provider: profile)
        monkeypatch.setattr(
            scraper_module,
            "QuietWebEnginePage",
            lambda profile, parent, provider="": _FakePage(calls),
        )
        monkeypatch.setattr(scraper_module, "QWebEngineView", lambda: _FakeView(calls))
        monkeypatch.setattr(scraper_module, "QTimer", _FakeTimer)
        monkeypatch.setattr(
            scraper_module, "time", SimpleNamespace(monotonic=lambda: self.now[0])
        )
        self.scraper = scraper_module.HeadlessScraper(
            "claude_api", BILLING, "extract()", wait_ms=3000, linger_ms=linger_ms
        )
        self.scraper.done.connect(lambda result, error: calls.append(("done", error)))
        self.page = self.scraper._page

    def call_names(self):
        return [c[0] for c in self.calls]

    def pending_names(self):
        return [getattr(fn, "__name__", "") for _, fn in _FakeTimer.pending]

    def run_pending(self, name):
        """Run the earliest pending single-shot whose callable has this name."""
        for index, (ms, fn) in enumerate(_FakeTimer.pending):
            if getattr(fn, "__name__", "") == name:
                del _FakeTimer.pending[index]
                fn()
                return ms
        raise AssertionError(f"{name} is not pending: {self.pending_names()}")

    def pending_ms(self, name):
        for ms, fn in _FakeTimer.pending:
            if getattr(fn, "__name__", "") == name:
                return ms
        raise AssertionError(f"{name} is not pending")

    def extract_ok(self):
        self.scraper._on_load_finished(True)
        self.run_pending("_run_extractor")
        script, callback = self.page.js[-1]
        assert script == "extract()"
        callback({"balance": 1})

    def answer_js(self, script_contains, value):
        for script, callback in self.page.js:
            if script_contains in script and callback is not None:
                callback(value)
                return
        raise AssertionError(script_contains)


@pytest.fixture(autouse=True)
def _clear_lingering():
    scraper_module._LINGERING.clear()
    yield
    scraper_module._LINGERING.clear()


def test_linger_detaches_the_view_before_the_page_is_made_visible_or_loaded(monkeypatch):
    h = _Harness(monkeypatch, linger_ms=60_000)

    names = h.call_names()
    detach = names.index("view.setPage", names.index("view.setPage") + 1)
    assert h.calls[detach] == ("view.setPage", None)
    assert detach < names.index("page.load")
    assert detach < names.index("page.setVisible")
    assert ("page.setVisible", True) in h.calls
    assert ("page.setLifecycleState", "Active") in h.calls
    # The view widget itself is never shown or made visible.
    assert "view.show" not in names
    assert "view.setVisible" not in names


def test_linger_holds_a_strong_reference_until_cleanup(monkeypatch):
    h = _Harness(monkeypatch, linger_ms=60_000)
    assert h.scraper in scraper_module._LINGERING

    h.extract_ok()
    h.run_pending("_begin_hold")
    assert h.scraper in scraper_module._LINGERING

    h.scraper._cleanup()
    assert h.scraper not in scraper_module._LINGERING


def test_linger_delivers_done_before_the_hold_and_defers_cleanup(monkeypatch, caplog):
    h = _Harness(monkeypatch, linger_ms=60_000)
    h.now[0] = 108.0  # the check took 8 seconds
    with caplog.at_level(logging.INFO, logger="aigauge.scraper"):
        h.extract_ok()
        assert ("done", "") in h.calls
        assert "_cleanup" not in h.pending_names()
        assert "page.deleteLater" not in h.call_names()

        h.run_pending("_begin_hold")
        # Hold until linger_ms has passed since scrape start.
        assert h.pending_ms("_end_hold") == 52_000
        h.answer_js("visibilityState", "visible")

    assert (
        "keep-alive start provider=claude_api visibility=visible linger_s=60.0"
        in caplog.text
    )
    assert "page.deleteLater" not in h.call_names()
    assert ("page.setVisible", False) not in h.calls


def test_linger_does_not_wait_when_the_scrape_already_took_longer(monkeypatch):
    h = _Harness(monkeypatch, linger_ms=60_000)
    h.now[0] = 400.0
    h.extract_ok()
    h.run_pending("_begin_hold")

    assert h.pending_ms("_end_hold") == 0


def test_linger_navigates_to_blank_then_hides_then_discards(monkeypatch, caplog):
    h = _Harness(monkeypatch, linger_ms=60_000)
    h.extract_ok()
    h.run_pending("_begin_hold")
    h.now[0] = 160.0
    h.run_pending("_end_hold")
    with caplog.at_level(logging.INFO, logger="aigauge.scraper"):
        h.answer_js(
            "event_logging", {"visibility": "visible", "event_logging_requests": 3}
        )

    assert (
        "keep-alive end provider=claude_api visibility=visible "
        "event_logging_requests=3 elapsed=60.0s"
    ) in caplog.text
    assert "/api/event_logging" in scraper_module._END_PROBE_JS
    assert h.calls[-1] == ("page.load", "about:blank")
    # Cleanup waits a few seconds so pagehide work and beacons can go out.
    assert h.pending_ms("_cleanup") == 3000
    assert "page.deleteLater" not in h.call_names()

    h.run_pending("_cleanup")
    names = h.call_names()
    blank = h.calls.index(("page.load", "about:blank"))
    hide = h.calls.index(("page.setVisible", False))
    discard = h.calls.index(("page.setLifecycleState", "Discarded"))
    assert blank < hide < discard
    assert "page.deleteLater" in names
    assert "view.show" not in names and "view.setVisible" not in names
    # Stopping goes to the page: the view no longer holds it.
    assert ("page.triggerAction", "Stop") in h.calls
    assert "view.stop" not in names


def test_linger_end_proceeds_when_the_probe_never_answers(monkeypatch):
    h = _Harness(monkeypatch, linger_ms=60_000)
    h.extract_ok()
    h.run_pending("_begin_hold")
    h.run_pending("_end_hold")

    # The renderer never answers; the fallback timer moves on once.
    h.run_pending("<lambda>")
    assert h.calls[-1] == ("page.load", "about:blank")
    # A late answer does not navigate a second time.
    h.answer_js("event_logging", {"visibility": "hidden", "event_logging_requests": 0})
    assert h.calls.count(("page.load", "about:blank")) == 1


def test_linger_error_path_does_not_hold(monkeypatch):
    h = _Harness(monkeypatch, linger_ms=60_000)
    h.scraper._on_load_finished(False)

    assert ("done", "page failed to load") in h.calls
    assert "_begin_hold" not in h.pending_names()
    assert "_cleanup" in h.pending_names()

    h.run_pending("_cleanup")
    hide = h.calls.index(("page.setVisible", False))
    discard = h.calls.index(("page.setLifecycleState", "Discarded"))
    assert hide < discard
    assert ("page.load", "about:blank") not in h.calls


def test_linger_retry_stops_the_page_not_the_view(monkeypatch):
    h = _Harness(monkeypatch, linger_ms=60_000)
    h.scraper._max_attempts = 2
    h.scraper._on_load_finished(False)

    assert ("page.triggerAction", "Stop") in h.calls
    assert "view.stop" not in h.call_names()
    assert h.calls.count(("page.load", BILLING)) == 2
    # Visibility is set once, not per attempt.
    assert h.calls.count(("page.setVisible", True)) == 1


def test_zero_linger_keeps_todays_call_sequence(monkeypatch):
    h = _Harness(monkeypatch, linger_ms=0)

    assert h.calls == [("view.setPage", "page"), ("page.load", BILLING)]
    assert h.scraper not in scraper_module._LINGERING

    h.extract_ok()
    assert h.pending_names() == ["_cleanup"]
    h.run_pending("_cleanup")

    assert h.calls[2:] == [
        ("page.runJavaScript", "extract()"),
        ("done", ""),
        ("view.stop",),
        ("view.setPage", None),
        ("page.setLifecycleState", "Discarded"),
        ("view.deleteLater",),
        ("page.deleteLater",),
    ]


def test_scrape_start_drops_constants_and_profile_is_logged_once(monkeypatch, caplog):
    monkeypatch.setattr(scraper_module, "_PROFILE_LOGGED", set())
    with caplog.at_level(logging.INFO, logger="aigauge.scraper"):
        _Harness(monkeypatch, linger_ms=0)
        _Harness(monkeypatch, linger_ms=60_000)

    lines = [r.getMessage() for r in caplog.records]
    profiles = [m for m in lines if m.startswith("scrape profile ")]
    starts = [m for m in lines if m.startswith("scrape start ")]
    assert len(profiles) == 1
    assert "provider=claude_api profile=profile" in profiles[0]
    assert "user_agent='ua'" in profiles[0]
    assert lines.index(profiles[0]) < lines.index(starts[0])
    assert len(starts) == 2
    for start in starts:
        assert "profile=" not in start
        assert "viewport=" not in start
        assert "user_agent=" not in start
    assert starts[0].endswith("max_attempts=1")
    assert starts[1].endswith("max_attempts=1 linger_ms=60000")
