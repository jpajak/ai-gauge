from datetime import datetime, timedelta

from aigauge.models import SnapshotStatus
from aigauge.providers.codex import (
    CODEX_USAGE_URL,
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


def test_codex_idle_usage_ignores_login_task_titles():
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

    assert snapshot.status == SnapshotStatus.OK
    assert [(metric.label, metric.percent_used) for metric in snapshot.metrics] == [
        ("Session", 0.0),
        ("Weekly", 0.0),
    ]


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


def test_codex_signed_in_empty_usage_payload_is_idle_zero():
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

    assert snapshot.status == SnapshotStatus.OK
    assert [(metric.label, metric.percent_used, metric.reset_label) for metric in snapshot.metrics] == [
        ("Session", 0.0, "idle"),
        ("Weekly", 0.0, "idle"),
    ]
    assert all(metric.window is None for metric in snapshot.metrics)


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
