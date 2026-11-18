from datetime import datetime, timedelta

import pytest

from aigauge.models import SnapshotStatus
from aigauge.providers import codex
from aigauge.providers.codex import CODEX_USAGE_URL, _build_snapshot, _parse_reset_text


SHARED_LIMITS = (
    "Usage Overview Analytics Plan limits Shared across Codex, Work, "
    "Workspace Agents, and ChatGPT for Excel. Chat conversations are not included. "
)
OVERVIEW = (
    SHARED_LIMITS
    + "5-hour limit Resets in 5h 0m 100% left "
    "Weekly limit Resets in 6d 10h 96% left "
    "Credits Shared workspace credits let you keep using Work and Codex beyond included usage. "
    "0 workspace credits Add more Automatic reload Up to 40% off "
    "Usage limit resets Use a reset to restore your 5-hour limit, weekly limit, or both "
    "Available 2 History Full reset (Weekly + 5 hr) Expires October 22 Use reset "
    "Full reset (Weekly + 5 hr) Expires October 29 Use reset Credit usage history"
)


def usage_payload(body_text=OVERVIEW, **kwargs):
    return {
        "logged_out": False,
        "session": None,
        "weekly": None,
        "title": "ChatGPT",
        "url": CODEX_USAGE_URL,
        "body_text": body_text,
        **kwargs,
    }


@pytest.fixture
def fixed_now(monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 10, 5, 18, 0)

    monkeypatch.setattr(codex, "datetime", FixedDateTime)
    return FixedDateTime.now()


@pytest.mark.parametrize(
    ("text", "delta"),
    [
        ("5h 0m", timedelta(hours=5)),
        ("6d 10h", timedelta(days=6, hours=10)),
        ("in 2 hr 59 min", timedelta(hours=2, minutes=59)),
        ("2 days 3 hours 4 minutes", timedelta(days=2, hours=3, minutes=4)),
        ("1d", timedelta(days=1)),
        ("29m", timedelta(minutes=29)),
        ("45s", timedelta(seconds=45)),
        ("0m", timedelta()),
    ],
)
def test_parse_relative_reset(text, delta, fixed_now):
    assert _parse_reset_text(text) == fixed_now + delta


@pytest.mark.parametrize("text", [None, "", "unknown", "6d 10h junk"])
def test_unrecognized_reset_is_not_a_countdown(text):
    assert _parse_reset_text(text) is None


def test_parse_absolute_resets(fixed_now):
    assert _parse_reset_text("Mon 6:00 PM") == fixed_now + timedelta(days=7)
    assert _parse_reset_text("at 4:47 PM") == datetime(2026, 10, 6, 16, 47)
    assert _parse_reset_text("Oct 12, 2026 at 9:36 AM") == datetime(2026, 10, 12, 9, 36)


def test_overview_body_fallback_reads_limits_and_countdowns(fixed_now):
    snapshot = _build_snapshot(usage_payload())

    assert snapshot.status == SnapshotStatus.OK
    assert [(m.label, m.percent_used) for m in snapshot.metrics] == [
        ("Session", 0.0), ("Weekly", 4.0)
    ]
    assert [m.resets_at for m in snapshot.metrics] == [
        fixed_now + timedelta(hours=5), fixed_now + timedelta(days=6, hours=10)
    ]
    assert [m.window for m in snapshot.metrics] == [timedelta(hours=5), timedelta(days=7)]
    assert all(m.reset_label is None for m in snapshot.metrics)


def test_overview_unused_weekly_limit_without_reset_is_idle():
    snapshot = _build_snapshot(usage_payload(
        SHARED_LIMITS + "5-hour limit Resets in 3h 10m 98% left Weekly limit 100% left"
    ))

    assert snapshot.status == SnapshotStatus.OK
    assert [(m.percent_used, m.reset_label) for m in snapshot.metrics] == [
        (2.0, None), (0.0, "idle")
    ]


def test_weekly_only_plan_is_supported():
    snapshot = _build_snapshot(usage_payload(
        SHARED_LIMITS + "Weekly limit Resets in 6d 10h 96% left "
        "Credits 0 workspace credits Usage limit resets Restore your 5-hour limit"
    ))

    assert snapshot.status == SnapshotStatus.OK
    assert [(m.label, m.percent_used) for m in snapshot.metrics] == [("Weekly", 4.0)]


@pytest.mark.parametrize("limits", [
    "5-hour limit Resets in 3h 10m 98% left Weekly limit",
    "5-hour limit Weekly limit Resets in 6d 10h 96% left",
])
def test_partially_loaded_limits_are_retried(limits):
    snapshot = _build_snapshot(usage_payload(SHARED_LIMITS + limits))

    assert snapshot.status == SnapshotStatus.ERROR
    assert "part of the usage cards" in snapshot.error


def test_weekly_only_without_plan_context_is_retried():
    snapshot = _build_snapshot(usage_payload("Weekly limit 96% left"))
    assert snapshot.status == SnapshotStatus.ERROR


@pytest.mark.parametrize("reset", ["12h 15m", "3d"])
def test_weekly_only_plan_does_not_need_the_shared_subtitle(reset):
    snapshot = _build_snapshot(usage_payload(
        f"Usage Overview Analytics Plan limits Weekly limit Resets in {reset} 75% left"
    ))

    assert snapshot.status == SnapshotStatus.OK
    assert [(m.label, m.percent_used) for m in snapshot.metrics] == [("Weekly", 25.0)]
    assert snapshot.metrics[0].resets_at is not None


def weekly_card(percent=100):
    return {
        "percent": percent,
        "kind": "remaining",
        "reset_text": None,
        "raw": f"Weekly limit {percent}% left",
    }


def test_weekly_only_card_is_kept_when_body_text_is_cut_before_the_limits():
    snapshot = _build_snapshot(usage_payload(
        "Usage Overview",
        weekly=weekly_card(),
        has_plan_limits=True,
        has_five_hour_heading=False,
    ))

    assert snapshot.status == SnapshotStatus.OK
    assert [(m.label, m.percent_used, m.reset_label) for m in snapshot.metrics] == [
        ("Weekly", 0.0, "idle")
    ]


@pytest.mark.parametrize("flags", [
    {"has_plan_limits": True, "has_five_hour_heading": True},
    {"has_plan_limits": False, "has_five_hour_heading": False},
])
def test_extractor_flags_decide_weekly_only_over_body_text(flags):
    # The body text alone would pass as a weekly-only layout.
    snapshot = _build_snapshot(usage_payload(
        SHARED_LIMITS + "Weekly limit Resets in 6d 10h 96% left",
        weekly=weekly_card(96),
        **flags,
    ))

    assert snapshot.status == SnapshotStatus.ERROR
    assert "part of the usage cards" in snapshot.error


def test_body_fallback_weekly_heading_cannot_borrow_session_percent():
    snapshot = _build_snapshot(usage_payload(
        SHARED_LIMITS + "Weekly limit 5-hour limit Resets in 4h 0m 80% left"
    ))

    assert snapshot.status == SnapshotStatus.ERROR
    assert "part of the usage cards" in snapshot.error


def bar_card(percent):
    return {
        "raw": f"progress value={percent}",
        "percent": percent,
        "kind": "remaining",
        "reset_text": None,
    }


def test_label_free_cards_show_percent_without_a_reset_time(caplog):
    caplog.set_level("INFO", logger="aigauge.providers.codex")
    snapshot = _build_snapshot(usage_payload(
        "", session=bar_card(100), weekly=bar_card(94), label_free=True,
    ))

    assert snapshot.status == SnapshotStatus.OK
    assert [(m.label, m.percent_used) for m in snapshot.metrics] == [
        ("Session", 0.0), ("Weekly", 6.0)
    ]
    # An unread reset time is not evidence of an idle window, even at 0% used.
    assert [(m.resets_at, m.reset_label) for m in snapshot.metrics] == [(None, None)] * 2
    assert [m.note for m in snapshot.metrics] == [codex._LABEL_FREE_NOTE] * 2
    assert [m.window for m in snapshot.metrics] == [timedelta(hours=5), timedelta(days=7)]
    diagnoses = [r.getMessage() for r in caplog.records if "page diagnosis" in r.getMessage()]
    assert len(diagnoses) == 1
    assert "classification=label_free_fallback" in diagnoses[0]


def test_label_free_lone_weekly_card_needs_no_plan_limits_text():
    payload = usage_payload("", weekly=bar_card(42), label_free=True)
    snapshot = _build_snapshot(payload)

    assert snapshot.status == SnapshotStatus.OK
    assert [(m.label, m.percent_used, m.note) for m in snapshot.metrics] == [
        ("Weekly", 58.0, codex._LABEL_FREE_NOTE)
    ]
    # The same card without the marker is a partly rendered page.
    del payload["label_free"]
    assert _build_snapshot(payload).status == SnapshotStatus.ERROR


def test_label_free_lone_session_card_is_still_partial():
    snapshot = _build_snapshot(usage_payload(
        "", session=bar_card(42), label_free=True,
    ))
    assert snapshot.status == SnapshotStatus.ERROR
    assert "part of the usage cards" in snapshot.error


def test_labelled_page_logs_no_label_free_diagnosis(caplog):
    caplog.set_level("INFO", logger="aigauge.providers.codex")
    snapshot = _build_snapshot(usage_payload())

    assert snapshot.status == SnapshotStatus.OK
    assert not [r for r in caplog.records if "label_free" in r.getMessage()]
    assert all(m.note != codex._LABEL_FREE_NOTE for m in snapshot.metrics)


def test_analytics_history_is_not_a_current_weekly_limit():
    snapshot = _build_snapshot(usage_payload(
        "Usage Overview Analytics Usage history "
        "Plan usage history By feature Weekly limits Period % of limit used "
        "Sep 26 – Oct 3 Usage as of Oct 2, 12:00 AM UTC 58.7%",
        url="https://chatgpt.com/settings/usage?tab=analytics#personal-usage",
        has_percent_text=True,
    ))

    assert snapshot.status == SnapshotStatus.ERROR
    assert snapshot.metrics == []


def test_credits_discount_and_purchased_resets_are_not_plan_limits():
    snapshot = _build_snapshot(usage_payload(
        SHARED_LIMITS + "5-hour limit Weekly limit Credits Up to 40% off "
        "Usage limit resets Restore your 5-hour limit, weekly limit, or both 100% left"
    ))

    assert snapshot.status == SnapshotStatus.ERROR
    assert snapshot.metrics == []


def test_analytics_tab_is_not_usage_limits():
    snapshot = _build_snapshot(usage_payload(
        "Usage Overview Analytics Usage history See how plan usage and credits "
        "were consumed in Work, Codex, and other agentic tasks. Plan usage By product",
        url="https://chatgpt.com/settings/usage?tab=analytics",
    ))

    assert snapshot.status == SnapshotStatus.ERROR
    assert "layout may have changed" in snapshot.error


def test_logged_out_payload_requires_auth():
    snapshot = _build_snapshot(usage_payload(
        "Sign in to continue", logged_out=True, title="Login",
        url="https://chatgpt.com/auth/login",
    ))
    assert snapshot.status == SnapshotStatus.AUTH_REQUIRED
    assert "Not signed in" in snapshot.error


@pytest.mark.parametrize("body_text", [
    "Verify you are human Cloudflare",
    "Checking if the site connection is secure. Cloudflare",
])
def test_security_verification_requires_auth(body_text):
    snapshot = _build_snapshot(usage_payload(body_text, title="Just a moment..."))
    assert snapshot.status == SnapshotStatus.AUTH_REQUIRED
    assert "security verification" in snapshot.error


def test_usage_limits_ignore_stale_login_and_security_signals():
    snapshot = _build_snapshot(usage_payload(
        OVERVIEW + "Cloudflare Sign in", logged_out=True
    ))
    assert snapshot.status == SnapshotStatus.OK


def test_signed_in_empty_usage_page_is_retried():
    snapshot = _build_snapshot(usage_payload("ChatGPT Codex Work"))
    assert snapshot.status == SnapshotStatus.ERROR
    assert "without plan limits" in snapshot.error


def test_provider_refresh_uses_overview_url(monkeypatch):
    calls = []

    class Runner:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def run(self, on_done):
            pass

    monkeypatch.setattr(codex, "ScrapeRunner", Runner)
    codex.CodexProvider().refresh(lambda snapshot: None)

    assert calls[0]["url"].startswith(CODEX_USAGE_URL + "&aigauge_ts=")


# A page that had not rendered its signed-in content: the URL is still the
# usage page, the body starts with an inline script, and the extractor's soft
# sign-out clauses fired (seen on 2026-11-05, about 1 check in 85).
UNRENDERED_BODY = (
    '(() => { const key = "oai-epic-native-launch"; let launch = null; '
    "try { launch = JSON.parse(sessionStorage.getItem(key) "
)


def unrendered_payload(**kwargs):
    return usage_payload(
        UNRENDERED_BODY,
        logged_out=True,
        url=CODEX_USAGE_URL + "&aigauge_ts=1",
        has_percent_text=False,
        has_usage_text=False,
        has_plan_limits=False,
        has_five_hour_heading=False,
        **kwargs,
    )


def test_soft_signout_stands_when_not_asked_to_confirm():
    snapshot = _build_snapshot(unrendered_payload())
    assert snapshot.status == SnapshotStatus.AUTH_REQUIRED


def test_soft_signout_is_a_retryable_error_when_asked_to_confirm(caplog):
    with caplog.at_level("INFO", logger=codex.log.name):
        snapshot = _build_snapshot(unrendered_payload(), confirm_soft_signout=True)
    assert snapshot.status == SnapshotStatus.ERROR
    assert "not finished loading" in snapshot.error
    assert "codex sign-out unconfirmed, reloading account=codex title='ChatGPT'" in caplog.text


@pytest.mark.parametrize("url", [
    "https://chatgpt.com/auth/login",
    "https://chatgpt.com/logout",
])
def test_login_url_is_conclusive_even_when_asked_to_confirm(url):
    snapshot = _build_snapshot(
        usage_payload("Sign in", logged_out=True, url=url),
        confirm_soft_signout=True,
    )
    assert snapshot.status == SnapshotStatus.AUTH_REQUIRED


class _FakeRunner:
    """Replays queued payloads through `build` the way ScrapeRunner does."""

    payloads: list = []
    scrapes = 0
    max_attempts = 2

    def __init__(self, *, build, build_max_attempts, **kwargs):
        self._build = build
        self._max = build_max_attempts

    def run(self, on_done):
        attempt = 0
        while True:
            attempt += 1
            _FakeRunner.scrapes += 1
            snapshot = self._build(_FakeRunner.payloads.pop(0))
            if snapshot.status == SnapshotStatus.ERROR and attempt < self._max:
                continue
            on_done(snapshot)
            return


def refresh_with(monkeypatch, payloads):
    _FakeRunner.payloads = list(payloads)
    _FakeRunner.scrapes = 0
    monkeypatch.setattr(codex, "ScrapeRunner", _FakeRunner)
    results = []
    provider = codex.CodexProvider()
    provider.refresh(results.append)
    return results, provider


def test_refresh_reloads_after_one_soft_signout_and_reads_normally(monkeypatch, fixed_now):
    results, _ = refresh_with(monkeypatch, [unrendered_payload(), usage_payload()])
    assert [r.status for r in results] == [SnapshotStatus.OK]
    assert _FakeRunner.scrapes == 2


def test_refresh_reports_signed_out_when_both_loads_say_so(monkeypatch):
    results, _ = refresh_with(monkeypatch, [unrendered_payload(), unrendered_payload()])
    assert [r.status for r in results] == [SnapshotStatus.AUTH_REQUIRED]
    assert _FakeRunner.scrapes == 2


def test_refresh_does_not_reload_for_a_login_url(monkeypatch):
    login = usage_payload("Sign in", logged_out=True, url="https://chatgpt.com/auth/login")
    results, _ = refresh_with(monkeypatch, [login])
    assert [r.status for r in results] == [SnapshotStatus.AUTH_REQUIRED]
    assert _FakeRunner.scrapes == 1


def test_refresh_reads_a_normal_payload_with_one_scrape(monkeypatch, fixed_now):
    results, _ = refresh_with(monkeypatch, [usage_payload()])
    assert [r.status for r in results] == [SnapshotStatus.OK]
    assert _FakeRunner.scrapes == 1


def test_attempt_count_restarts_on_each_refresh(monkeypatch):
    results, provider = refresh_with(
        monkeypatch, [unrendered_payload(), unrendered_payload()]
    )
    assert [r.status for r in results] == [SnapshotStatus.AUTH_REQUIRED]
    # A second refresh must defer its first soft sign-out again, not stand on it.
    _FakeRunner.payloads = [unrendered_payload(), usage_payload()]
    _FakeRunner.scrapes = 0
    provider.refresh(results.append)
    assert results[-1].status != SnapshotStatus.AUTH_REQUIRED
    assert _FakeRunner.scrapes == 2


def test_real_runner_makes_two_scrapes_for_one_soft_signout(monkeypatch, fixed_now):
    from aigauge.providers import _scrape_runner as runner_module

    scrapers = []

    class Signal:
        def connect(self, slot):
            self.slot = slot

    class Scraper:
        def __init__(self, **kwargs):
            self.done = Signal()
            scrapers.append(self)

    monkeypatch.setattr(runner_module, "HeadlessScraper", Scraper)
    monkeypatch.setattr(runner_module.webengine, "is_available", lambda: True)
    results = []
    codex.CodexProvider().refresh(results.append)

    scrapers[0].done.slot(unrendered_payload(), "")
    assert results == [] and len(scrapers) == 2
    scrapers[1].done.slot(usage_payload(), "")
    assert [r.status for r in results] == [SnapshotStatus.OK]
