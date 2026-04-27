from datetime import datetime

import pytest
import responses

from usage_view.config import Config
from usage_view.models import SnapshotStatus
from usage_view.providers.copilot import (
    GITHUB_API,
    GITHUB_API_VERSION,
    _build_snapshot,
    _next_month_start,
)


def test_next_month_start_normal():
    d = _next_month_start(datetime(2026, 4, 27))
    assert d == datetime(2026, 5, 1)


def test_next_month_start_december_rolls_year():
    d = _next_month_start(datetime(2026, 12, 15))
    assert d == datetime(2027, 1, 1)


def test_build_snapshot_sums_gross_quantity_for_included_allowance():
    payload = {
        "usageItems": [
            {"unitType": "Premium Request", "netQuantity": 0, "grossQuantity": 50},
            {"unitType": "Premium Request", "netQuantity": 0, "grossQuantity": 20},
        ]
    }
    snap = _build_snapshot(payload, quota=300)
    assert snap.status == SnapshotStatus.OK
    assert len(snap.metrics) == 1
    m = snap.metrics[0]
    assert m.percent_used == pytest.approx(70 / 300 * 100)
    assert "70/300" in m.label
    assert m.resets_at is not None and m.resets_at.day == 1


def test_build_snapshot_falls_back_to_net_quantity():
    snap = _build_snapshot({"usageItems": [{"unitType": "x", "netQuantity": 12}]}, quota=300)
    assert snap.metrics[0].percent_used == pytest.approx(12 / 300 * 100)


def test_build_snapshot_handles_empty_usage():
    snap = _build_snapshot({"usageItems": []}, quota=300)
    assert snap.status == SnapshotStatus.OK
    assert snap.metrics[0].percent_used == 0.0
    assert snap.metrics[0].note is not None


def test_build_snapshot_treats_missing_net_quantity_as_zero():
    snap = _build_snapshot({"usageItems": [{"unitType": "x"}]}, quota=300)
    assert snap.metrics[0].percent_used == 0.0


@responses.activate
def test_fetch_premium_usage_calls_correct_endpoint():
    from usage_view.providers.copilot import _fetch_premium_usage

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
    from usage_view.providers.copilot import _fetch_org_premium_usage

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
    from usage_view.providers.copilot import _resolve_username

    responses.add(
        responses.GET,
        f"{GITHUB_API}/user",
        json={"login": "octocat"},
        status=200,
    )
    assert _resolve_username("ghp_test", configured=None) == "octocat"


def test_resolve_username_uses_configured_if_set():
    from usage_view.providers.copilot import _resolve_username

    # No HTTP call should happen — configured value short-circuits.
    assert _resolve_username("anything", configured="myname") == "myname"


def test_config_smoke():
    # Ensures provider class can be constructed
    from usage_view.providers.copilot import CopilotProvider

    cfg = Config()
    # Don't actually call refresh — needs Qt event loop.
    provider = CopilotProvider(cfg, pool=None)
    assert provider.name == "copilot"
