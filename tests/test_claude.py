from datetime import timedelta

from aigauge.models import SnapshotStatus
from aigauge.providers.claude import CLAUDE_USAGE_URL, _build_snapshot


def test_claude_cloudflare_payload_is_auth_required():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": None,
            "weekly_all": None,
            "title": "Just a moment...",
            "body_text": "Verify you are human Cloudflare",
        }
    )

    assert snapshot.status == SnapshotStatus.AUTH_REQUIRED
    assert "security verification" in (snapshot.error or "")


def test_claude_usage_rows_ignore_cloudflare_chat_titles():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": {"percent": 5, "kind": "used", "reset_text": "6 min"},
            "weekly_all": {"percent": 26, "kind": "used", "reset_text": "Thu 9:59 AM"},
            "title": "Claude",
            "url": CLAUDE_USAGE_URL,
            "body_text": (
                "New chat Search Chats Projects Recents "
                "Cloudflare push model for local GitHub repos "
                "Plan usage limits Current session 5% used All models 26% used"
            ),
        }
    )

    assert snapshot.status == SnapshotStatus.OK
    assert [metric.label for metric in snapshot.metrics] == ["Session", "Weekly"]


def test_claude_usage_rows_ignore_connectivity_chat_titles():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": {"percent": 5, "kind": "used", "reset_text": "6 min"},
            "weekly_all": {"percent": 26, "kind": "used", "reset_text": "Thu 9:59 AM"},
            "title": "Claude",
            "url": CLAUDE_USAGE_URL,
            "body_text": (
                "New chat Search Chats Projects Recents "
                "Can't reach Claude, check your connection and try again "
                "Plan usage limits Current session 5% used All models 26% used"
            ),
        }
    )

    assert snapshot.status == SnapshotStatus.OK
    assert [metric.label for metric in snapshot.metrics] == ["Session", "Weekly"]


def test_claude_idle_usage_ignores_cloudflare_chat_titles():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": None,
            "weekly_all": None,
            "title": "Claude",
            "url": CLAUDE_USAGE_URL,
            "body_text": (
                "New chat Search Chats Projects Recents "
                "Just a moment debugging Cloudflare "
                "Plan usage limits Current session Resets when you next use this limit "
                "All models Resets when you next use this limit"
            ),
        }
    )

    assert snapshot.status == SnapshotStatus.OK
    assert [(metric.label, metric.percent_used) for metric in snapshot.metrics] == [
        ("Session", 0.0),
        ("Weekly", 0.0),
    ]


def test_claude_logout_payload_is_auth_required():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": None,
            "weekly_all": None,
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
    assert [
        (metric.label, metric.percent_used, metric.reset_label)
        for metric in snapshot.metrics
    ] == [
        ("Session", 0.0, "idle"),
        ("Weekly", 0.0, "idle"),
    ]
    assert all(metric.window is None for metric in snapshot.metrics)


def test_claude_legacy_usage_url_can_still_be_idle_zero():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": None,
            "weekly_all": None,
            "title": "Claude",
            "url": "https://claude.ai/settings/usage",
            "body_text": (
                "Plan usage limits Current session Resets when you next use this limit "
                "All models Resets when you next use this limit"
            ),
        }
    )

    assert snapshot.status == SnapshotStatus.OK
    assert [(metric.label, metric.percent_used) for metric in snapshot.metrics] == [
        ("Session", 0.0),
        ("Weekly", 0.0),
    ]


def test_claude_partial_render_payload_is_layout_error():
    # Sidebar-only body (main usage pane hasn't populated yet) must NOT be
    # classified as idle — it should surface as an error so the provider
    # retries instead of showing a confident 0/0.
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": None,
            "weekly_all": None,
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
            "title": "Claude",
            "url": CLAUDE_USAGE_URL,
            "body_text": "Can't reach Claude Check your connection. Try again",
        }
    )

    assert snapshot.status == SnapshotStatus.ERROR
    assert "load failed" in (snapshot.error or "")


def test_claude_zero_weekly_usage_keeps_weekday_reset():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "session": {"percent": 2, "kind": "used", "reset_text": "4 hr 58 min"},
            "weekly_all": {"percent": 0, "kind": "used", "reset_text": "Mon 6:00 PM"},
            "title": "Claude",
            "url": CLAUDE_USAGE_URL,
            "body_text": "Plan usage limits Current session 2% All models 0%",
        }
    )

    weekly = next(metric for metric in snapshot.metrics if metric.label == "Weekly")
    assert weekly.percent_used == 0
    assert weekly.resets_at is not None
    assert weekly.reset_label is None


def _max_plan_payload() -> dict:
    return {
        "logged_out": False,
        "session": {"percent": 18, "kind": "used", "reset_text": "3 hr 10 min"},
        "weekly_all": {"percent": 2, "kind": "used", "reset_text": "22 hr 0 min"},
        "weekly_fable": {"percent": 4, "kind": "used", "reset_text": "22 hr 0 min"},
        "title": "Claude",
        "url": CLAUDE_USAGE_URL,
        "body_text": (
            "Plan usage limits Max (5x) Current session 18% used "
            "Weekly limits All models 2% used Fable 4% used"
        ),
    }


def test_claude_fable_row_is_read_when_enabled():
    snapshot = _build_snapshot(_max_plan_payload(), show_fable=True)

    assert snapshot.status == SnapshotStatus.OK
    assert [metric.label for metric in snapshot.metrics] == [
        "Session",
        "Weekly",
        "Fable",
    ]
    fable = snapshot.metrics[2]
    assert fable.percent_used == 4
    assert fable.window == timedelta(days=7)


def test_claude_fable_row_is_ignored_when_disabled():
    snapshot = _build_snapshot(_max_plan_payload())

    assert snapshot.status == SnapshotStatus.OK
    assert [metric.label for metric in snapshot.metrics] == ["Session", "Weekly"]


def test_claude_fable_enabled_on_plan_without_the_row_is_not_an_error():
    payload = _max_plan_payload()
    payload["weekly_fable"] = None
    payload["body_text"] = (
        "Plan usage limits Current session 18% used All models 2% used"
    )

    snapshot = _build_snapshot(payload, show_fable=True)

    assert snapshot.status == SnapshotStatus.OK
    assert [metric.label for metric in snapshot.metrics] == ["Session", "Weekly"]
