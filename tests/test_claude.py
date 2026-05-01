from datetime import timedelta

from aigauge.models import SnapshotStatus
from aigauge.providers.claude import CLAUDE_USAGE_URL, _build_snapshot


def test_claude_cloudflare_payload_is_auth_required():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": None,
            "weekly_all": None,
            "weekly_design": None,
            "title": "Just a moment...",
            "body_text": "Verify you are human Cloudflare",
        }
    )

    assert snapshot.status == SnapshotStatus.AUTH_REQUIRED
    assert "security verification" in (snapshot.error or "")


def test_claude_logout_payload_is_auth_required():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": None,
            "weekly_all": None,
            "weekly_design": None,
            "title": "Claude",
            "url": "https://claude.ai/logout",
            "body_text": "Loading...",
        }
    )

    assert snapshot.status == SnapshotStatus.AUTH_REQUIRED
    assert "Not signed in" in (snapshot.error or "")


def test_claude_signed_in_empty_usage_payload_is_idle_zero():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": None,
            "weekly_all": None,
            "weekly_design": None,
            "title": "Claude",
            "url": CLAUDE_USAGE_URL,
            "body_text": (
                "New chat Search Chats Projects Recents Plan usage limits "
                "Current session Resets when you next use this limit "
                "All models Resets when you next use this limit"
            ),
        }
    )

    assert snapshot.status == SnapshotStatus.OK
    assert [(metric.label, metric.percent_used, metric.reset_label) for metric in snapshot.metrics] == [
        ("Session", 0.0, "idle"),
        ("Weekly", 0.0, "idle"),
    ]
    assert all(metric.window is None for metric in snapshot.metrics)


def test_claude_partial_render_payload_is_layout_error():
    # Sidebar-only body (main usage pane hasn't populated yet) must NOT be
    # classified as idle — it should surface as an error so the provider
    # retries instead of showing a confident 0/0.
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": None,
            "weekly_all": None,
            "weekly_design": None,
            "title": "Claude",
            "url": CLAUDE_USAGE_URL,
            "body_text": "New chat Search Chats Projects Recents",
        }
    )

    assert snapshot.status == SnapshotStatus.ERROR
    assert "layout may have changed" in (snapshot.error or "")


def test_claude_unparsed_usage_payload_still_reports_layout_error():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": None,
            "weekly_all": None,
            "weekly_design": None,
            "title": "Claude",
            "url": CLAUDE_USAGE_URL,
            "body_text": "Plan usage limits Current session 15% used",
        }
    )

    assert snapshot.status == SnapshotStatus.ERROR
    assert "layout may have changed" in (snapshot.error or "")


def test_claude_cant_reach_page_is_load_failure():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": None,
            "weekly_all": None,
            "weekly_design": None,
            "title": "Claude",
            "url": CLAUDE_USAGE_URL,
            "body_text": "Can't reach Claude Check your connection. Try again",
        }
    )

    assert snapshot.status == SnapshotStatus.ERROR
    assert "load failed" in (snapshot.error or "")


def test_claude_design_limit_is_hidden_by_default():
    payload = {
        "logged_out": False,
        "session": {"percent": 10, "kind": "used", "reset_text": None},
        "weekly_all": {"percent": 20, "kind": "used", "reset_text": None},
        "weekly_design": {"percent": 30, "kind": "used", "reset_text": None},
        "title": "Claude",
        "url": CLAUDE_USAGE_URL,
        "body_text": "Plan usage limits Current session 10% All models 20% Claude Design 30%",
    }

    snapshot = _build_snapshot(payload)

    assert snapshot.status == SnapshotStatus.OK
    assert [metric.label for metric in snapshot.metrics] == ["Session", "Weekly"]
    assert [metric.window for metric in snapshot.metrics] == [
        timedelta(hours=5),
        timedelta(days=7),
    ]


def test_claude_zero_weekly_usage_keeps_weekday_reset():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": {"percent": 2, "kind": "used", "reset_text": "4 hr 58 min"},
            "weekly_all": {"percent": 0, "kind": "used", "reset_text": "Mon 6:00 PM"},
            "weekly_design": None,
            "title": "Claude",
            "url": CLAUDE_USAGE_URL,
            "body_text": "Plan usage limits Current session 2% All models 0%",
        }
    )

    weekly = next(metric for metric in snapshot.metrics if metric.label == "Weekly")
    assert weekly.percent_used == 0
    assert weekly.resets_at is not None
    assert weekly.reset_label is None


def test_claude_design_limit_can_be_shown():
    payload = {
        "logged_out": False,
        "session": {"percent": 10, "kind": "used", "reset_text": None},
        "weekly_all": {"percent": 20, "kind": "used", "reset_text": None},
        "weekly_design": {"percent": 30, "kind": "used", "reset_text": None},
        "title": "Claude",
        "url": CLAUDE_USAGE_URL,
        "body_text": "Plan usage limits Current session 10% All models 20% Claude Design 30%",
    }

    snapshot = _build_snapshot(payload, show_design=True)

    assert snapshot.status == SnapshotStatus.OK
    assert [metric.label for metric in snapshot.metrics] == ["Session", "Weekly", "Design"]
    assert [metric.window for metric in snapshot.metrics] == [
        timedelta(hours=5),
        timedelta(days=7),
        timedelta(days=7),
    ]
