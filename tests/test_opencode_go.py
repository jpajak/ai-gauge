from datetime import datetime, timedelta

from aigauge.config import BrowserAccount, Config
from aigauge.models import SnapshotStatus
from aigauge.providers.opencode_go import _build_snapshot, _parse_reset_text, usage_url


def test_parse_reset_text_handles_days_hours_minutes():
    parsed = _parse_reset_text("Resets in 30 days 17 hours")

    assert parsed is not None
    assert parsed > datetime.now()
    assert 30 <= (parsed - datetime.now()).days <= 31


def test_opencode_go_builds_three_usage_metrics_from_rows():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "usage": [
                {
                    "label": "Rolling Usage",
                    "percent": 13,
                    "reset_text": "Resets in 4 hours 36 minutes",
                },
                {
                    "label": "Weekly Usage",
                    "percent": 14,
                    "reset_text": "Resets in 3 days 3 hours",
                },
                {
                    "label": "Monthly Usage",
                    "percent": 7,
                    "reset_text": "Resets in 30 days 17 hours",
                },
            ],
            "title": "OpenCode",
            "body_text": "Rolling Usage 13% Weekly Usage 14% Monthly Usage 7%",
        }
    )

    assert snapshot.status == SnapshotStatus.OK
    assert [(m.label, m.percent_used) for m in snapshot.metrics] == [
        ("Rolling", 13.0),
        ("Weekly", 14.0),
        ("Monthly", 7.0),
    ]
    assert [m.window for m in snapshot.metrics] == [
        timedelta(hours=5),
        timedelta(days=7),
        timedelta(days=30),
    ]
    assert all(m.resets_at is not None for m in snapshot.metrics)


def test_opencode_go_body_text_fallback_reads_visible_usage():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "usage": [],
            "title": "OpenCode",
            "body_text": (
                "Rolling Usage 13% Resets in 4 hours 36 minutes "
                "Weekly Usage 14% Resets in 3 days 3 hours "
                "Monthly Usage 7% Resets in 30 days 17 hours"
            ),
        }
    )

    assert snapshot.status == SnapshotStatus.OK
    assert [m.label for m in snapshot.metrics] == ["Rolling", "Weekly", "Monthly"]


def test_opencode_go_logged_out_payload_is_auth_required():
    snapshot = _build_snapshot(
        {
            "logged_out": True,
            "usage": [],
            "title": "Login",
            "body_text": "Sign in to continue",
        }
    )

    assert snapshot.status == SnapshotStatus.AUTH_REQUIRED
    assert "Not signed in" in (snapshot.error or "")


def test_opencode_go_unparsed_payload_reports_layout_error():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "usage": [],
            "title": "OpenCode",
            "body_text": "Usage",
        }
    )

    assert snapshot.status == SnapshotStatus.ERROR
    assert "layout may have changed" in (snapshot.error or "")

def test_opencode_snapshot_uses_account_identity():
    snapshot = _build_snapshot(
        {
            "logged_out": False,
            "usage": [{"label": "Rolling Usage", "percent": 13}],
            "title": "OpenCode",
            "body_text": "Rolling Usage 13%",
        },
        account_id="opencode_go-work",
    )

    assert snapshot.provider == "opencode_go-work"


def test_usage_url_is_account_specific():
    config = Config()
    config.browser_accounts.append(
        BrowserAccount(
            id="opencode_go-work",
            kind="opencode_go",
            name="Work",
            usage_url="https://opencode.ai/workspace/work/go",
        )
    )

    assert usage_url(config, "opencode_go-work") == (
        "https://opencode.ai/workspace/work/go"
    )
    assert usage_url(config, "opencode_go") != usage_url(
        config,
        "opencode_go-work",
    )
