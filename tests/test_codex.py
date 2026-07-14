from datetime import datetime, timedelta

from aigauge.models import SnapshotStatus
from aigauge.providers.codex import (
    CODEX_USAGE_URL,
    EXTRACTOR_JS,
    _build_snapshot,
    _parse_reset_text,
)


def test_parse_reset_text_handles_weekday_time():
    parsed = _parse_reset_text("Mon 6:00 PM")

    assert parsed is not None
    assert parsed.weekday() == 0
    assert parsed.hour == 18
    assert parsed.minute == 0
    assert parsed > datetime.now()


def test_parse_reset_text_handles_at_prefix_and_date_at_time():
    time_only = _parse_reset_text("at 4:47 PM")
    dated = _parse_reset_text("May 19, 2026 at 9:36 AM")

    assert time_only is not None
    assert time_only.hour == 16
    assert time_only.minute == 47
    assert dated is not None
    assert dated.month == 5
    assert dated.day == 19
    assert dated.year == 2026
    assert dated.hour == 9
    assert dated.minute == 36


def test_codex_logged_out_payload_is_auth_required():
    snapshot = _build_snapshot(
        {
            "logged_out": True,
            "session": None,
            "weekly": None,
            "title": "Login",
            "body_text": "Sign in to continue",
        }
    )

    assert snapshot.status == SnapshotStatus.AUTH_REQUIRED
    assert "Not signed in" in (snapshot.error or "")


def test_codex_empty_shell_with_login_task_titles_is_transient_error():
    snapshot = _build_snapshot(
        {
            "logged_out": True,
            "session": None,
            "weekly": None,
            "title": "Codex",
            "url": CODEX_USAGE_URL,
            "body_text": "Codex cloud tasks Sign in flow debugging",
        }
    )

    assert snapshot.status == SnapshotStatus.ERROR
    assert "without usage cards" in (snapshot.error or "")


def test_codex_usage_rows_ignore_stale_logged_out_flag():
    snapshot = _build_snapshot(
        {
            "logged_out": True,
            "session": {"percent": 12, "kind": "used", "reset_text": "4 hr 10 min"},
            "weekly": {"percent": 31, "kind": "used", "reset_text": "Mon 6:00 PM"},
            "title": "Codex",
            "url": CODEX_USAGE_URL,
            "body_text": "Codex Tasks Sign in debugging 5 hour usage limit 12% used",
        }
    )

    assert snapshot.status == SnapshotStatus.OK
    assert [metric.label for metric in snapshot.metrics] == ["Session", "Weekly"]


def test_codex_cloudflare_payload_is_auth_required():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": None,
            "weekly": None,
            "title": "Just a moment...",
            "body_text": "Verify you are human Cloudflare",
        }
    )

    assert snapshot.status == SnapshotStatus.AUTH_REQUIRED
    assert "security verification" in (snapshot.error or "")


def test_codex_cloudflare_soft_payload_is_auth_required():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": None,
            "weekly": None,
            "title": "Just a moment...",
            "body_text": "Checking if the site connection is secure. Cloudflare",
        }
    )

    assert snapshot.status == SnapshotStatus.AUTH_REQUIRED
    assert "security verification" in (snapshot.error or "")


def test_codex_usage_rows_ignore_cloudflare_mentions():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": {"percent": 12, "kind": "used", "reset_text": "4 hr 10 min"},
            "weekly": {"percent": 31, "kind": "used", "reset_text": "Mon 6:00 PM"},
            "title": "Codex",
            "url": CODEX_USAGE_URL,
            "body_text": (
                "Codex Tasks Cloudflare tunnel debugging 5 hour usage limit 12% used "
                "Weekly usage limit 31% used"
            ),
        }
    )

    assert snapshot.status == SnapshotStatus.OK
    assert [metric.label for metric in snapshot.metrics] == ["Session", "Weekly"]


def test_codex_body_text_fallback_ignores_cloudflare_task_titles():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": None,
            "weekly": None,
            "title": "Codex",
            "url": CODEX_USAGE_URL,
            "body_text": (
                "Codex Tasks Just a moment Cloudflare tunnel debugging "
                "Personal usage 5 hour usage limit 88% remaining Resets at 4:47 PM "
                "Weekly usage limit 75% remaining Resets Mon 6:00 PM"
            ),
            "has_usage_text": True,
            "has_percent_text": True,
        }
    )

    assert snapshot.status == SnapshotStatus.OK
    assert [metric.percent_used for metric in snapshot.metrics] == [12.0, 25.0]


def test_codex_signed_in_empty_usage_payload_is_transient_error():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": None,
            "weekly": None,
            "title": "Codex",
            "url": CODEX_USAGE_URL,
            "body_text": "Codex cloud tasks",
        }
    )

    assert snapshot.status == SnapshotStatus.ERROR
    assert "without usage cards" in (snapshot.error or "")


def test_codex_session_without_weekly_is_transient_error():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": {
                "percent": 2,
                "kind": "remaining",
                "reset_text": "Jul 10, 2026 12:14 AM",
            },
            "weekly": None,
            "title": "Codex",
            "url": CODEX_USAGE_URL,
            "body_text": "5 hour usage limit 2% remaining Weekly usage limit",
        }
    )

    assert snapshot.status == SnapshotStatus.ERROR
    assert "part of the usage cards" in (snapshot.error or "")


def test_codex_weekly_only_payload_is_valid_while_session_limit_is_absent():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": None,
            "weekly": {
                "percent": 93,
                "kind": "remaining",
                "reset_text": "Jul 19, 2026 4:29 PM",
            },
            "title": "Codex Analytics",
            "url": CODEX_USAGE_URL,
            "body_text": (
                "Personal usage Balance Weekly usage limit 93% remaining "
                "Resets Jul 19, 2026 4:29 PM"
            ),
        }
    )

    assert snapshot.status == SnapshotStatus.OK
    assert [metric.label for metric in snapshot.metrics] == ["Weekly"]
    assert snapshot.metrics[0].percent_used == 7.0
    assert snapshot.metrics[0].window == timedelta(days=7)


def test_codex_extractor_stops_waiting_when_weekly_card_is_ready():
    personal_usage_check = EXTRACTOR_JS.split(
        "function maybeSelectPersonalUsageTab",
        maxsplit=1,
    )[1].split("const labels", maxsplit=1)[0]

    assert "/Weekly usage limit/i.test(bodyText)" in personal_usage_check
    assert "/5 hour usage limit/i.test(bodyText)" not in personal_usage_check


def test_codex_active_session_with_idle_weekly_is_transient_error():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": {
                "percent": 99,
                "kind": "remaining",
                "reset_text": "4 hr 59 min",
            },
            "weekly": {
                "percent": 100,
                "kind": "remaining",
                "reset_text": None,
            },
            "title": "Codex",
            "url": CODEX_USAGE_URL,
            "body_text": "5 hour usage limit 99% remaining Weekly usage limit 100% remaining",
        }
    )

    assert snapshot.status == SnapshotStatus.ERROR
    assert "active session with an idle weekly card" in (snapshot.error or "")

def test_codex_usage_signal_prevents_false_idle_fallback():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": None,
            "weekly": None,
            "title": "Codex",
            "url": CODEX_USAGE_URL,
            "body_text": "Codex cloud tasks",
            "has_usage_text": True,
        }
    )

    assert snapshot.status == SnapshotStatus.ERROR
    assert "layout may have changed" in (snapshot.error or "")


def test_codex_generic_usage_text_prevents_false_idle_fallback():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": None,
            "weekly": None,
            "title": "Codex",
            "url": CODEX_USAGE_URL,
            "body_text": "Codex cloud Usage Settings",
        }
    )

    assert snapshot.status == SnapshotStatus.ERROR
    assert "layout may have changed" in (snapshot.error or "")


def test_codex_unparsed_usage_payload_still_reports_layout_error():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": None,
            "weekly": None,
            "title": "Codex",
            "url": CODEX_USAGE_URL,
            "body_text": "5 hour usage limit Weekly usage limit",
        }
    )

    assert snapshot.status == SnapshotStatus.ERROR
    assert "layout may have changed" in (snapshot.error or "")


def test_codex_metrics_carry_windows():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": {"percent": 10, "kind": "used", "reset_text": "4 hr 30 min"},
            "weekly": {"percent": 20, "kind": "used", "reset_text": "Mon 6:00 PM"},
            "title": "Codex",
            "url": CODEX_USAGE_URL,
            "body_text": "5 hour usage limit 10% Weekly usage limit 20%",
        }
    )

    assert snapshot.status == SnapshotStatus.OK
    assert [metric.window for metric in snapshot.metrics] == [
        timedelta(hours=5),
        timedelta(days=7),
    ]


def test_codex_body_text_fallback_reads_new_visible_cards():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": None,
            "weekly": None,
            "title": "Codex",
            "url": CODEX_USAGE_URL,
            "body_text": (
                "Personal usage 5 hour usage limit 99% remaining "
                "Resets at 4:47 PM Weekly usage limit 94% remaining "
                "Resets May 19, 2026 at 9:36 AM"
            ),
            "has_usage_text": True,
            "has_percent_text": True,
        }
    )

    assert snapshot.status == SnapshotStatus.OK
    assert [metric.percent_used for metric in snapshot.metrics] == [1.0, 6.0]
    assert all(metric.resets_at is not None for metric in snapshot.metrics)
