from datetime import datetime

from usage_view.models import SnapshotStatus
from usage_view.providers.codex import CODEX_USAGE_URL, _build_snapshot, _parse_reset_text


def test_parse_reset_text_handles_weekday_time():
    parsed = _parse_reset_text("Mon 6:00 PM")

    assert parsed is not None
    assert parsed.weekday() == 0
    assert parsed.hour == 18
    assert parsed.minute == 0
    assert parsed > datetime.now()


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


def test_codex_unparsed_usage_payload_still_reports_layout_error():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": None,
            "weekly": None,
            "title": "Codex",
            "url": CODEX_USAGE_URL,
            "body_text": "5 hour usage limit Weekly usage limit 15%",
        }
    )

    assert snapshot.status == SnapshotStatus.ERROR
    assert "layout may have changed" in (snapshot.error or "")
