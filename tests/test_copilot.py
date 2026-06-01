from datetime import datetime, timedelta, timezone

import pytest
import responses

from aigauge.config import Config
from aigauge.models import SnapshotStatus
from aigauge.providers.copilot import (
    COPILOT_PRODUCT,
    GITHUB_API,
    GITHUB_API_VERSION,
    _build_snapshot,
    _next_month_start_utc,
    _this_month_start_utc,
)


def test_next_month_start_utc_normal():
    d = _next_month_start_utc(datetime(2026, 4, 27, tzinfo=timezone.utc))
    assert d == datetime(2026, 5, 1, tzinfo=timezone.utc)
    assert d.tzinfo == timezone.utc


def test_next_month_start_utc_december_rolls_year():
    d = _next_month_start_utc(datetime(2026, 12, 15, tzinfo=timezone.utc))
    assert d == datetime(2027, 1, 1, tzinfo=timezone.utc)
    assert d.tzinfo == timezone.utc


def test_this_month_start_utc():
    d = _this_month_start_utc(datetime(2026, 4, 27, tzinfo=timezone.utc))
    assert d == datetime(2026, 4, 1, tzinfo=timezone.utc)
    assert d.tzinfo == timezone.utc


def test_build_snapshot_uses_utc_month_boundary(monkeypatch):
    import aigauge.providers.copilot as copilot

    real_datetime = datetime
    now = real_datetime(2026, 4, 30, 23, 30, tzinfo=timezone.utc)

    class FrozenDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            if tz is timezone.utc:
                return now
            return now.astimezone().replace(tzinfo=None)

    monkeypatch.setattr(copilot, "datetime", FrozenDatetime)

    snap = _build_snapshot({"usageItems": []}, quota=1500)
    metric = snap.metrics[0]
    next_utc = _next_month_start_utc(now)
    assert metric.resets_at == next_utc.astimezone().replace(tzinfo=None)
    assert metric.resets_at - FrozenDatetime.now() < timedelta(hours=1)
    assert metric.window == next_utc - _this_month_start_utc(now)


def test_build_snapshot_sums_ai_credit_quantity_for_included_allowance():
    payload = {
        "usageItems": [
            {
                "product": "copilot",
                "sku": "copilot_ai_credits",
                "unitType": "credits",
                "netQuantity": 0,
                "grossQuantity": 50,
            },
            {
                "product": "copilot",
                "sku": "copilot_ai_credits",
                "unitType": "credits",
                "netQuantity": 0,
                "grossQuantity": 20,
            },
        ]
    }
    snap = _build_snapshot(payload, quota=1500)
    assert snap.status == SnapshotStatus.OK
    assert len(snap.metrics) == 1
    m = snap.metrics[0]
    assert m.percent_used == pytest.approx(70 / 1500 * 100)
    assert "70/1500" in m.label
    assert m.resets_at is not None
    assert m.window is not None and timedelta(days=28) <= m.window <= timedelta(days=31)


def test_build_snapshot_falls_back_to_credit_amount():
    payload = {
        "usageItems": [
            {
                "product": "copilot",
                "sku": "Copilot AI credits",
                "unitType": "AI credits",
                "grossAmount": 0.12,
            }
        ]
    }
    snap = _build_snapshot(payload, quota=1500)
    assert snap.metrics[0].percent_used == pytest.approx(12 / 1500 * 100)


def test_build_snapshot_accepts_github_ai_unit_sku():
    payload = {
        "usageItems": [
            {
                "product": "Copilot",
                "sku": "copilot_ai_unit",
                "unitType": "ai-units",
                "grossQuantity": 68.5548,
                "grossAmount": 0.685548,
                "netQuantity": 0.0,
            }
        ]
    }
    snap = _build_snapshot(payload, quota=1500)
    metric = snap.metrics[0]
    assert metric.percent_used == pytest.approx(68.5548 / 1500 * 100)
    assert "68.6/1500" in metric.label


def test_build_snapshot_handles_empty_usage():
    snap = _build_snapshot({"usageItems": []}, quota=1500)
    assert snap.status == SnapshotStatus.OK
    assert snap.metrics[0].percent_used == 0.0
    assert snap.metrics[0].note is not None


def test_build_snapshot_ignores_non_credit_items():
    snap = _build_snapshot(
        {"usageItems": [{"product": "copilot", "sku": "copilot_standalone"}]},
        quota=1500,
    )
    assert snap.metrics[0].percent_used == 0.0


@responses.activate
def test_fetch_credit_usage_calls_current_endpoint():
    from aigauge.providers.copilot import _fetch_credit_usage

    responses.add(
        responses.GET,
        f"{GITHUB_API}/users/octocat/settings/billing/usage/summary",
        json={"usageItems": [], "user": "octocat"},
        status=200,
    )
    result = _fetch_credit_usage("ghp_test", "octocat")
    assert result["user"] == "octocat"
    call = responses.calls[0]
    assert "Bearer ghp_test" in call.request.headers["Authorization"]
    assert call.request.headers["X-GitHub-Api-Version"] == GITHUB_API_VERSION
    assert "year=" in call.request.url
    assert "month=" in call.request.url
    assert f"product={COPILOT_PRODUCT}" in call.request.url


@responses.activate
def test_fetch_premium_usage_calls_correct_endpoint():
    from aigauge.providers.copilot import _fetch_premium_usage

    responses.add(
        responses.GET,
        f"{GITHUB_API}/users/octocat/settings/billing/premium_request/usage",
        json={"usageItems": [], "user": "octocat"},
        status=200,
    )
    result = _fetch_premium_usage("ghp_test", "octocat")
    assert result["user"] == "octocat"
    call = responses.calls[0]
    assert "Bearer ghp_test" in call.request.headers["Authorization"]
    assert call.request.headers["X-GitHub-Api-Version"] == GITHUB_API_VERSION
    assert "year=" in call.request.url
    assert "month=" in call.request.url


@responses.activate
def test_fetch_org_premium_usage_calls_correct_endpoint():
    from aigauge.providers.copilot import _fetch_org_premium_usage

    responses.add(
        responses.GET,
        f"{GITHUB_API}/organizations/my-org/settings/billing/premium_request/usage",
        json={"usageItems": [], "organization": "my-org"},
        status=200,
    )
    result = _fetch_org_premium_usage("ghp_test", "my-org", "octocat")
    assert result["organization"] == "my-org"
    call = responses.calls[0]
    assert "Bearer ghp_test" in call.request.headers["Authorization"]
    assert call.request.headers["X-GitHub-Api-Version"] == GITHUB_API_VERSION
    assert "year=" in call.request.url
    assert "month=" in call.request.url
    assert "user=octocat" in call.request.url


@responses.activate
def test_resolve_username_from_pat():
    from aigauge.providers.copilot import _resolve_username

    responses.add(
        responses.GET,
        f"{GITHUB_API}/user",
        json={"login": "octocat"},
        status=200,
    )
    assert _resolve_username("ghp_test", configured=None) == "octocat"


def test_resolve_username_uses_configured_if_set():
    from aigauge.providers.copilot import _resolve_username

    # No HTTP call should happen — configured value short-circuits.
    assert _resolve_username("anything", configured="myname") == "myname"


def test_config_smoke():
    # Ensures provider class can be constructed
    from aigauge.providers.copilot import CopilotProvider

    cfg = Config()
    # Don't actually call refresh — needs Qt event loop.
    provider = CopilotProvider(cfg, pool=None)
    assert provider.name == "copilot"
