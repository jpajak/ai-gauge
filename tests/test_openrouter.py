import pytest
import responses

from aigauge.config import Config
from aigauge.models import SnapshotStatus
from aigauge.providers.openrouter import (
    ACTIVITY_LABEL,
    KEY_BREAKDOWN_TAG,
    MAX_KEY_BREAKDOWN_ROWS,
    MAX_MODEL_BREAKDOWN_ROWS,
    MODEL_BREAKDOWN_TAG,
    MODEL_DISPLAY_MAX_LEN,
    OPENROUTER_API,
    _account_usage,
    _activity_model_costs,
    _build_credits_metric,
    _build_daily_metric,
    _build_key_metrics,
    _build_model_metrics,
    _build_snapshot,
    _build_summary_metric,
)


def test_build_credits_metric_shows_remaining_balance_only():
    m = _build_credits_metric({"total_credits": 100.0, "total_usage": 25.5})
    assert m is not None
    assert m.percent_used is None
    assert m.label == "Balance ($74.50 left)"
    assert m.note is not None
    assert "OpenRouter" in m.note


def test_build_credits_metric_returns_none_for_missing_or_invalid():
    assert _build_credits_metric(None) is None
    assert _build_credits_metric({}) is None
    assert _build_credits_metric({"total_credits": "bad"}) is None


def test_build_credits_metric_shows_zero_balance_without_gauge():
    m = _build_credits_metric({"total_credits": 0, "total_usage": 0})
    assert m is not None
    assert m.percent_used is None
    assert m.label == "Balance ($0.00 left)"
    assert m.note is not None and "OpenRouter" in m.note


def test_build_credits_metric_clamps_overage():
    m = _build_credits_metric({"total_credits": 10.0, "total_usage": 25.0})
    assert m is not None
    assert m.percent_used is None
    assert m.label == "Balance ($0.00 left)"


def test_build_daily_metric_with_budget():
    m = _build_daily_metric({"usage_daily": 2.5}, daily_budget=5.0)
    assert m is not None
    assert m.percent_used == pytest.approx(50.0)
    assert "$2.50" in m.label and "$5.00" in m.label
    assert m.label.startswith("Today")
    assert m.resets_at is not None


def test_build_daily_metric_without_budget():
    m = _build_summary_metric(
        {"total_credits": 100.0, "total_usage": 25.5},
        {"usage_daily": 2.5, "usage_monthly": 20.75},
        daily_budget=None,
    )
    assert m is not None
    assert m.percent_used is None
    assert m.label == "Balance $74.50 · Today $2.50 · Month $20.75"
    assert m.note is not None
    assert "current UTC day" in m.note


def test_build_daily_metric_without_budget_returns_none():
    assert _build_daily_metric({"usage_daily": 2.5}, daily_budget=None) is None


def test_build_daily_metric_missing_usage_returns_none():
    assert _build_daily_metric({}, daily_budget=5.0) is None
    assert _build_daily_metric({"usage_daily": "bad"}, daily_budget=5.0) is None


def test_build_model_metrics_top_six_only():
    metrics = _build_model_metrics(
        [
            ("a", 10.0),
            ("b", 5.0),
            ("c", 2.0),
            ("d", 1.0),
            ("e", 0.8),
            ("f", 0.7),
            ("g", 0.5),
        ],
    )
    assert len(metrics) == MAX_MODEL_BREAKDOWN_ROWS + 1
    assert metrics[0].label == "Models: last 30 completed UTC days"
    assert metrics[0].percent_used is None
    assert [m.label for m in metrics[1:]] == ["a", "b", "c", "d", "e", "f"]
    assert all(m.tag == MODEL_BREAKDOWN_TAG for m in metrics)
    # Percent is share of TOTAL activity spend (all models), not share of top rows,
    # so the bars don't add up to 100% if there's a long tail.
    assert metrics[1].percent_used == pytest.approx(10 / 20 * 100)


def test_build_model_metrics_empty():
    assert _build_model_metrics([]) == []


def test_build_model_metrics_strips_provider_prefix_into_tooltip():
    metrics = _build_model_metrics(
        [
            ("anthropic/claude-opus-4.7", 10.0),
            ("openai/gpt-5.5", 5.0),
            ("bare-name", 1.0),
        ],
    )
    rows = metrics[1:]
    assert [m.label for m in rows] == ["claude-opus-4.7", "gpt-5.5", "bare-name"]
    # Full slug goes into the tooltip, with cost on a second line.
    assert rows[0].note == "anthropic/claude-opus-4.7\n$10.00"
    assert rows[1].note == "openai/gpt-5.5\n$5.00"
    # Models without a provider prefix keep the cost-only note.
    assert rows[2].note == "$1.00"


def test_build_model_metrics_truncates_long_names_and_keeps_full_in_tooltip():
    metrics = _build_model_metrics(
        [
            ("google/gemini-3-pro-image-preview", 10.0),
            ("some-very-long-bare-model-name-here", 5.0),
        ],
    )
    rows = metrics[1:]
    # Display labels are capped to MODEL_DISPLAY_MAX_LEN chars (with an ellipsis).
    assert all(len(m.label) <= MODEL_DISPLAY_MAX_LEN for m in rows)
    assert rows[0].label.endswith("…")
    assert rows[1].label.endswith("…")
    # Full slug is preserved in the tooltip for both prefixed and bare names.
    assert "google/gemini-3-pro-image-preview" in (rows[0].note or "")
    assert "some-very-long-bare-model-name-here" in (rows[1].note or "")


def test_activity_model_costs_aggregates_all_activity_rows():
    rows = [
        {"date": "2026-05-05", "model": "gpt-4o", "usage": 1.0},
        {"date": "2026-05-04", "model": "gpt-4o", "usage": 2.5},
        {"date": "2026-05-03", "model": "claude-3", "usage": 4.0},
        {"date": "1999-01-01", "model": "old", "usage": 99.0},
        {"model": "no-date", "usage": 1.0},
    ]
    result = _activity_model_costs(rows)
    assert result == [
        ("old", 99.0),
        ("claude-3", 4.0),
        ("gpt-4o", 3.5),
        ("no-date", 1.0),
    ]


def test_build_snapshot_full_path():
    top_models = [("gpt-4o", 5.0), ("claude-3", 3.0)]
    snap = _build_snapshot(
        credits={"total_credits": 100.0, "total_usage": 25.0},
        key_info={"usage_daily": 2.5},
        top_models=top_models,
        daily_budget=10.0,
        mgmt_key_configured=True,
    )
    assert snap.status == SnapshotStatus.OK
    # 1 summary + 1 daily + 1 model context + 2 model breakdown
    assert len(snap.metrics) == 5
    main = [m for m in snap.metrics if not m.tag]
    breakdown = [m for m in snap.metrics if m.tag == MODEL_BREAKDOWN_TAG]
    assert len(main) == 2
    assert len(breakdown) == 3
    assert breakdown[0].label == "Models: last 30 completed UTC days"
    assert snap.raw["top_models"] == [["gpt-4o", 5.0], ["claude-3", 3.0]]
    assert snap.raw["activity_date"] is None
    assert snap.raw["activity_window"] == "last_30_completed_utc_days"


def test_build_snapshot_no_mgmt_key_shows_visible_row():
    """Without a management key, _build_snapshot inserts a clearly visible
    'Account balance' row pointing at Settings, instead of silently dropping
    the credits info."""
    snap = _build_snapshot(
        credits=None,
        key_info={"usage_daily": 1.0},
        top_models=[],
        daily_budget=None,
        mgmt_key_configured=False,
    )
    assert snap.status == SnapshotStatus.OK
    labels = [m.label for m in snap.metrics]
    assert "Account balance" in labels
    balance_row = next(m for m in snap.metrics if m.label == "Account balance")
    assert balance_row.percent_used is None
    assert balance_row.note is not None
    assert "management key" in balance_row.note
    assert "Settings" in balance_row.note


def test_build_snapshot_activity_error_shows_visible_row():
    """When /activity fails, _build_snapshot adds a top models unavailable
    row instead of silently omitting the model breakdown."""
    snap = _build_snapshot(
        credits={"total_credits": 100.0, "total_usage": 25.0},
        key_info={"usage_daily": 1.0},
        top_models=[],
        daily_budget=None,
        mgmt_key_configured=True,
        activity_error="/activity returned HTTP 500",
    )
    assert snap.status == SnapshotStatus.OK
    labels = [m.label for m in snap.metrics]
    assert f"{ACTIVITY_LABEL}: unavailable" in labels
    err_row = next(
        m for m in snap.metrics if m.label == f"{ACTIVITY_LABEL}: unavailable"
    )
    assert err_row.percent_used is None
    assert err_row.note is not None
    assert "Unavailable" in err_row.note
    assert "500" in err_row.note


def test_build_snapshot_empty_activity_shows_visible_row():
    snap = _build_snapshot(
        credits={"total_credits": 100.0, "total_usage": 25.0},
        key_info={"usage_daily": 1.0},
        top_models=[],
        daily_budget=None,
        mgmt_key_configured=True,
    )
    labels = [m.label for m in snap.metrics]
    assert f"{ACTIVITY_LABEL}: none" in labels
    activity_row = next(
        m for m in snap.metrics if m.label == f"{ACTIVITY_LABEL}: none"
    )
    assert activity_row.percent_used is None
    assert "last 30 completed UTC days" in (activity_row.note or "")


def test_build_snapshot_no_metrics_no_crash():
    """With nothing configured and no data, the snapshot is still OK with the
    'Account balance' opt-out row, never an empty silent metric list."""
    snap = _build_snapshot(
        credits=None,
        key_info={},
        top_models=[],
        daily_budget=None,
        mgmt_key_configured=False,
    )
    assert snap.status == SnapshotStatus.OK
    assert any(m.label == "Account balance" for m in snap.metrics)


@responses.activate
def test_fetch_credits_returns_data():
    from aigauge.providers.openrouter import _fetch_credits

    responses.add(
        responses.GET,
        f"{OPENROUTER_API}/credits",
        json={"data": {"total_credits": 50.0, "total_usage": 10.0}},
        status=200,
    )
    result = _fetch_credits("sk-or-test")
    assert result == {"total_credits": 50.0, "total_usage": 10.0}
    call = responses.calls[0]
    assert "Bearer sk-or-test" in call.request.headers["Authorization"]


@responses.activate
def test_fetch_credits_raises_on_401():
    """A 401 from /credits means the management key is bad, callers must
    surface this rather than silently returning empty credits."""
    import requests
    from aigauge.providers.openrouter import _fetch_credits

    responses.add(
        responses.GET,
        f"{OPENROUTER_API}/credits",
        json={"error": "unauthorized"},
        status=401,
    )
    with pytest.raises(requests.HTTPError):
        _fetch_credits("sk-or-test")


@responses.activate
def test_fetch_key_info_returns_data():
    from aigauge.providers.openrouter import _fetch_key_info

    responses.add(
        responses.GET,
        f"{OPENROUTER_API}/key",
        json={"data": {"usage": 10.0, "usage_daily": 1.0}},
        status=200,
    )
    result = _fetch_key_info("sk-or-test")
    assert result["usage_daily"] == 1.0


@responses.activate
def test_fetch_activity_returns_error_on_404():
    """Activity failures must surface as a string error so the snapshot can
    show a visible 'Top models unavailable' row."""
    from aigauge.providers.openrouter import _fetch_activity

    responses.add(
        responses.GET,
        f"{OPENROUTER_API}/activity",
        status=404,
    )
    rows, err = _fetch_activity("sk-or-test")
    assert rows == []
    assert err is not None and "404" in err


@responses.activate
def test_fetch_activity_returns_error_on_network_failure():
    from aigauge.providers.openrouter import _fetch_activity
    import requests as _requests

    responses.add(
        responses.GET,
        f"{OPENROUTER_API}/activity",
        body=_requests.ConnectionError("connection refused"),
    )
    rows, err = _fetch_activity("sk-or-test")
    assert rows == []
    assert err is not None and "network" in err.lower()


@responses.activate
def test_fetch_activity_success():
    from aigauge.providers.openrouter import _fetch_activity

    responses.add(
        responses.GET,
        f"{OPENROUTER_API}/activity",
        json={"data": [{"date": "2026-05-05", "model": "gpt-4o", "usage": 1.0}]},
        status=200,
    )
    rows, err = _fetch_activity("sk-or-test")
    assert err is None
    assert len(rows) == 1


def test_activity_model_costs_empty_no_log():
    """If /activity returned 0 rows, do NOT log a warning, because that's normal."""
    import logging

    logger = logging.getLogger("aigauge.providers.openrouter")
    handler_records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            handler_records.append(record)

    handler = _Capture(level=logging.WARNING)
    logger.addHandler(handler)
    try:
        result = _activity_model_costs([])
    finally:
        logger.removeHandler(handler)
    assert result == []
    assert handler_records == []


def test_provider_construction_smoke():
    from aigauge.providers.openrouter import OpenRouterProvider

    cfg = Config()
    provider = OpenRouterProvider(cfg, pool=None)
    assert provider.name == "openrouter"
    assert provider.display_name == "OpenRouter"


def test_management_key_helpers_roundtrip(monkeypatch):
    """Storing and retrieving the management key uses a separate keyring slot
    from the inference key, so setting one does not overwrite the other."""
    import aigauge.config as cfg_mod

    store: dict[tuple[str, str], str] = {}

    def fake_get(service, name):
        return store.get((service, name))

    def fake_set(service, name, value):
        store[(service, name)] = value

    def fake_delete(service, name):
        store.pop((service, name), None)

    monkeypatch.setattr(cfg_mod.keyring, "get_password", fake_get)
    monkeypatch.setattr(cfg_mod.keyring, "set_password", fake_set)
    monkeypatch.setattr(cfg_mod.keyring, "delete_password", fake_delete)

    cfg_mod.set_openrouter_key("inference-key")
    cfg_mod.set_openrouter_mgmt_key("mgmt-key")
    assert cfg_mod.get_openrouter_key() == "inference-key"
    assert cfg_mod.get_openrouter_mgmt_key() == "mgmt-key"

    cfg_mod.set_openrouter_mgmt_key(None)
    assert cfg_mod.get_openrouter_mgmt_key() is None
    # Inference key is untouched.
    assert cfg_mod.get_openrouter_key() == "inference-key"


@responses.activate
def test_refresh_uses_mgmt_key_for_credits_when_set(monkeypatch):
    """When a management key is configured, /credits, /keys and /activity are
    called with it (not the inference key), and /key is never called, even
    though an inference key is saved."""
    from aigauge.providers.openrouter import OpenRouterProvider
    import aigauge.providers.openrouter as or_mod

    monkeypatch.setattr(or_mod, "get_openrouter_key", lambda: "inference-key")
    monkeypatch.setattr(or_mod, "get_openrouter_mgmt_key", lambda: "mgmt-key")

    responses.add(
        responses.GET,
        f"{OPENROUTER_API}/credits",
        json={"data": {"total_credits": 100.0, "total_usage": 25.0}},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{OPENROUTER_API}/keys",
        json={"data": [{"name": "a", "usage_daily": 1.5, "usage_monthly": 3.0}]},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{OPENROUTER_API}/activity",
        json={"data": []},
        status=200,
    )

    provider = OpenRouterProvider(Config(), pool=None)
    captured: list = []
    # Run synchronously by stubbing _run_async to call work() inline.
    monkeypatch.setattr(
        provider, "_run_async", lambda work, on_done: on_done(work())
    )
    provider.refresh(captured.append)

    assert len(captured) == 1
    snap = captured[0]
    assert snap.status == SnapshotStatus.OK

    # Confirm management endpoints were called with the management key, not the
    # inference key.
    credits_calls = [
        c for c in responses.calls if c.request.url.endswith("/credits")
    ]
    key_calls = [c for c in responses.calls if c.request.url.endswith("/key")]
    keys_calls = [c for c in responses.calls if "/keys" in c.request.url]
    activity_calls = [c for c in responses.calls if "/activity" in c.request.url]
    assert credits_calls, "/credits should have been called"
    assert "Bearer mgmt-key" in credits_calls[0].request.headers["Authorization"]
    assert activity_calls, "/activity should have been called"
    assert "Bearer mgmt-key" in activity_calls[0].request.headers["Authorization"]
    assert "date=" not in activity_calls[0].request.url
    assert keys_calls, "/keys should have been called"
    assert "Bearer mgmt-key" in keys_calls[0].request.headers["Authorization"]
    assert key_calls == []


@responses.activate
def test_refresh_skips_management_endpoints_when_no_mgmt_key(monkeypatch):
    """Without a management key, management endpoints must NOT be called at all
    (no silent fallback). The snapshot still renders OK with visible opt-in
    rows."""
    from aigauge.providers.openrouter import OpenRouterProvider
    import aigauge.providers.openrouter as or_mod

    monkeypatch.setattr(or_mod, "get_openrouter_key", lambda: "inference-key")
    monkeypatch.setattr(or_mod, "get_openrouter_mgmt_key", lambda: None)

    responses.add(
        responses.GET,
        f"{OPENROUTER_API}/key",
        json={"data": {"usage_daily": 0.5}},
        status=200,
    )
    provider = OpenRouterProvider(Config(), pool=None)
    captured: list = []
    monkeypatch.setattr(
        provider, "_run_async", lambda work, on_done: on_done(work())
    )
    provider.refresh(captured.append)

    snap = captured[0]
    assert snap.status == SnapshotStatus.OK
    assert any(m.label == "Account balance" for m in snap.metrics)
    assert any("Today $" in m.label for m in snap.metrics)

    # Critical: management endpoints must not be called when no management key
    # is configured.
    credits_calls = [
        c for c in responses.calls if c.request.url.endswith("/credits")
    ]
    activity_calls = [c for c in responses.calls if "/activity" in c.request.url]
    assert credits_calls == []
    assert activity_calls == []
    activity_rows = [
        m for m in snap.metrics if m.label == f"{ACTIVITY_LABEL}: unavailable"
    ]
    assert len(activity_rows) == 1
    assert "management key" in (activity_rows[0].note or "")


@responses.activate
def test_refresh_surfaces_mgmt_key_401_as_auth_required(monkeypatch):
    """A 401 from /credits when a management key is configured means the key
    is bad. Surface as AUTH_REQUIRED so the user knows to fix it, never silent."""
    from aigauge.providers.openrouter import OpenRouterProvider
    import aigauge.providers.openrouter as or_mod

    monkeypatch.setattr(or_mod, "get_openrouter_key", lambda: "inference-key")
    monkeypatch.setattr(or_mod, "get_openrouter_mgmt_key", lambda: "bad-mgmt-key")

    responses.add(
        responses.GET,
        f"{OPENROUTER_API}/credits",
        status=401,
    )

    provider = OpenRouterProvider(Config(), pool=None)
    captured: list = []
    monkeypatch.setattr(
        provider, "_run_async", lambda work, on_done: on_done(work())
    )
    provider.refresh(captured.append)

    snap = captured[0]
    assert snap.status == SnapshotStatus.AUTH_REQUIRED
    assert "management key" in (snap.error or "")


@responses.activate
def test_refresh_surfaces_activity_failure_as_visible_row(monkeypatch):
    """When /activity returns a non-200, the snapshot is still OK (other
    metrics are valid) but the model breakdown is replaced by a clearly
    visible top models unavailable row."""
    from aigauge.providers.openrouter import OpenRouterProvider
    import aigauge.providers.openrouter as or_mod

    monkeypatch.setattr(or_mod, "get_openrouter_key", lambda: "inference-key")
    monkeypatch.setattr(or_mod, "get_openrouter_mgmt_key", lambda: "mgmt-key")

    responses.add(
        responses.GET,
        f"{OPENROUTER_API}/credits",
        json={"data": {"total_credits": 100.0, "total_usage": 25.0}},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{OPENROUTER_API}/keys",
        json={"data": [{"name": "a", "usage_daily": 1.0, "usage_monthly": 1.0}]},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{OPENROUTER_API}/activity",
        status=500,
    )

    provider = OpenRouterProvider(Config(), pool=None)
    captured: list = []
    monkeypatch.setattr(
        provider, "_run_async", lambda work, on_done: on_done(work())
    )
    provider.refresh(captured.append)

    snap = captured[0]
    assert snap.status == SnapshotStatus.OK
    err_rows = [
        m for m in snap.metrics if m.label == f"{ACTIVITY_LABEL}: unavailable"
    ]
    assert len(err_rows) == 1
    assert "500" in (err_rows[0].note or "")


# ---------------------------------------------------------------------------
# /keys: account-wide spend across every API key
# ---------------------------------------------------------------------------


def _key(name, monthly, daily=0.0, **extra):
    return {
        "hash": f"hash-{name}",
        "name": name,
        "label": "sk-or-v1-0e6...1c96",
        "disabled": False,
        "usage_daily": daily,
        "usage_monthly": monthly,
        **extra,
    }


def _run_refresh(monkeypatch, *, inference, mgmt, config=None):
    from aigauge.providers.openrouter import OpenRouterProvider
    import aigauge.providers.openrouter as or_mod

    monkeypatch.setattr(or_mod, "get_openrouter_key", lambda: inference)
    monkeypatch.setattr(or_mod, "get_openrouter_mgmt_key", lambda: mgmt)
    provider = OpenRouterProvider(config or Config(), pool=None)
    captured: list = []
    monkeypatch.setattr(provider, "_run_async", lambda work, on_done: on_done(work()))
    provider.refresh(captured.append)
    assert len(captured) == 1
    return captured[0]


def _add_mgmt_endpoints(keys, *, activity=None):
    responses.add(
        responses.GET,
        f"{OPENROUTER_API}/credits",
        json={"data": {"total_credits": 100.0, "total_usage": 25.0}},
        status=200,
    )
    responses.add(responses.GET, f"{OPENROUTER_API}/keys", json={"data": keys}, status=200)
    responses.add(
        responses.GET,
        f"{OPENROUTER_API}/activity",
        json={"data": activity or []},
        status=200,
    )


def _urls(suffix):
    return [c for c in responses.calls if c.request.url.split("?")[0].endswith(suffix)]


@responses.activate
def test_fetch_keys_sends_include_flags_and_auth():
    from aigauge.providers.openrouter import _fetch_keys

    responses.add(
        responses.GET, f"{OPENROUTER_API}/keys", json={"data": [_key("a", 1.0)]}, status=200
    )
    keys = _fetch_keys("mgmt-key")
    assert [k["name"] for k in keys] == ["a"]
    req = responses.calls[0].request
    assert "include_disabled=true" in req.url
    assert "include_expired=true" in req.url
    assert "offset=0" in req.url
    assert req.headers["Authorization"] == "Bearer mgmt-key"


@responses.activate
def test_fetch_keys_paginates_until_short_page():
    from aigauge.providers.openrouter import _fetch_keys

    first = [_key(f"k{i}", 1.0) for i in range(100)]
    second = [_key("last", 1.0)]
    responses.add(responses.GET, f"{OPENROUTER_API}/keys", json={"data": first}, status=200)
    responses.add(responses.GET, f"{OPENROUTER_API}/keys", json={"data": second}, status=200)
    keys = _fetch_keys("mgmt-key")
    assert len(keys) == 101
    assert len(responses.calls) == 2
    assert "offset=0" in responses.calls[0].request.url
    assert "offset=100" in responses.calls[1].request.url


@responses.activate
def test_fetch_keys_stops_after_ten_pages():
    from aigauge.providers.openrouter import _fetch_keys

    page = [_key(f"k{i}", 1.0) for i in range(100)]
    responses.add(responses.GET, f"{OPENROUTER_API}/keys", json={"data": page}, status=200)
    keys = _fetch_keys("mgmt-key")
    assert len(responses.calls) == 10
    assert len(keys) == 1000


@responses.activate
def test_fetch_keys_ignores_bad_items_and_shapes():
    from aigauge.providers.openrouter import _fetch_keys

    responses.add(
        responses.GET,
        f"{OPENROUTER_API}/keys",
        json={"data": [_key("a", 1.0), "junk", None, 3]},
        status=200,
    )
    assert [k["name"] for k in _fetch_keys("m")] == ["a"]
    responses.replace(responses.GET, f"{OPENROUTER_API}/keys", json={"data": "nope"}, status=200)
    assert _fetch_keys("m") == []
    responses.replace(responses.GET, f"{OPENROUTER_API}/keys", json=["x"], status=200)
    assert _fetch_keys("m") == []


@responses.activate
def test_fetch_keys_raises_on_401():
    import requests
    from aigauge.providers.openrouter import _fetch_keys

    responses.add(responses.GET, f"{OPENROUTER_API}/keys", status=401)
    with pytest.raises(requests.HTTPError):
        _fetch_keys("bad")


def test_account_usage_sums_and_skips_bad_values():
    totals = _account_usage(
        [
            {"usage_daily": 1.0, "usage_monthly": 10},
            {"usage_daily": None, "usage_monthly": "2.5"},
            {"usage_daily": "abc", "usage_monthly": [1]},
            {"usage_daily": 0.25},
        ]
    )
    assert totals == {"usage_daily": 1.25, "usage_monthly": 12.5}


@responses.activate
def test_refresh_mgmt_key_only_sums_across_keys_and_never_calls_key(monkeypatch):
    _add_mgmt_endpoints([_key("a", 10.0, daily=1.0), _key("b", 5.5, daily=0.5)])
    snap = _run_refresh(monkeypatch, inference=None, mgmt="mgmt-key")

    assert snap.status == SnapshotStatus.OK
    assert snap.metrics[0].label == "Balance $75.00 \u00b7 Today $1.50 \u00b7 Month $15.50"
    assert "across all 2 API keys" in snap.metrics[0].note
    assert _urls("/key") == []
    assert snap.raw["usage_source"] == "all_keys"
    assert snap.raw["key"] is None
    assert snap.raw["keys"] == [
        {"name": "a", "usage_daily": 1.0, "usage_monthly": 10.0, "disabled": False},
        {"name": "b", "usage_daily": 0.5, "usage_monthly": 5.5, "disabled": False},
    ]
    # Neither the hash nor the (possibly masked-key) label leaks into raw.
    assert "hash" not in repr(snap.raw["keys"])
    assert "sk-or" not in repr(snap.raw["keys"])
    # No "add a management key" row on this path.
    assert not any(m.label == "Account balance" for m in snap.metrics)


@responses.activate
def test_refresh_with_both_keys_never_calls_key(monkeypatch):
    _add_mgmt_endpoints([_key("a", 1.0)])
    snap = _run_refresh(monkeypatch, inference="inference-key", mgmt="mgmt-key")
    assert snap.status == SnapshotStatus.OK
    assert _urls("/key") == []
    assert snap.raw["usage_source"] == "all_keys"


@responses.activate
def test_refresh_inference_key_only_keeps_old_behavior(monkeypatch):
    responses.add(
        responses.GET,
        f"{OPENROUTER_API}/key",
        json={"data": {"usage_daily": 0.5, "usage_monthly": 2.0}},
        status=200,
    )
    snap = _run_refresh(monkeypatch, inference="inference-key", mgmt=None)
    assert snap.status == SnapshotStatus.OK
    assert snap.metrics[0].label == "Today $0.50 \u00b7 Month $2.00"
    assert "inference key only" in snap.metrics[0].note
    balance_row = next(m for m in snap.metrics if m.label == "Account balance")
    assert balance_row.note == (
        "Add a management key in Settings to show the balance and spend across "
        "all your keys."
    )
    assert not any(m.tag == KEY_BREAKDOWN_TAG for m in snap.metrics)
    assert snap.raw["usage_source"] == "inference_key"
    assert snap.raw["keys"] is None
    assert snap.raw["key"] == {"usage_daily": 0.5, "usage_monthly": 2.0}
    assert _urls("/keys") == []


@responses.activate
def test_refresh_with_no_keys_is_auth_required(monkeypatch):
    snap = _run_refresh(monkeypatch, inference=None, mgmt=None)
    assert snap.status == SnapshotStatus.AUTH_REQUIRED
    assert snap.error == "Add an OpenRouter management key (or an inference key) in Settings."
    assert len(responses.calls) == 0


@responses.activate
def test_refresh_keys_401_is_auth_required(monkeypatch):
    responses.add(
        responses.GET,
        f"{OPENROUTER_API}/credits",
        json={"data": {"total_credits": 1.0, "total_usage": 0.0}},
        status=200,
    )
    responses.add(responses.GET, f"{OPENROUTER_API}/keys", status=401)
    snap = _run_refresh(monkeypatch, inference="i", mgmt="m")
    assert snap.status == SnapshotStatus.AUTH_REQUIRED
    assert snap.error == "OpenRouter rejected the management key (401). Update or remove it in Settings."


@responses.activate
def test_refresh_keys_500_is_error(monkeypatch):
    responses.add(
        responses.GET,
        f"{OPENROUTER_API}/credits",
        json={"data": {"total_credits": 1.0, "total_usage": 0.0}},
        status=200,
    )
    responses.add(responses.GET, f"{OPENROUTER_API}/keys", status=500)
    snap = _run_refresh(monkeypatch, inference="i", mgmt="m")
    assert snap.status == SnapshotStatus.ERROR
    assert snap.error == "OpenRouter /keys 500"


@responses.activate
def test_refresh_keys_network_error_and_bad_json_are_errors(monkeypatch):
    import requests

    responses.add(
        responses.GET,
        f"{OPENROUTER_API}/credits",
        json={"data": {"total_credits": 1.0, "total_usage": 0.0}},
        status=200,
    )
    responses.add(
        responses.GET, f"{OPENROUTER_API}/keys", body=requests.ConnectionError("boom")
    )
    snap = _run_refresh(monkeypatch, inference=None, mgmt="m")
    assert snap.status == SnapshotStatus.ERROR
    assert "boom" in snap.error

    responses.replace(responses.GET, f"{OPENROUTER_API}/keys", body="<html>", status=200)
    snap = _run_refresh(monkeypatch, inference=None, mgmt="m")
    assert snap.status == SnapshotStatus.ERROR


@responses.activate
def test_refresh_budget_gauge_uses_summed_daily_total(monkeypatch):
    _add_mgmt_endpoints([_key("a", 10.0, daily=1.0), _key("b", 5.0, daily=3.0)])
    cfg = Config()
    cfg.openrouter.daily_budget = 8.0
    snap = _run_refresh(monkeypatch, inference="i", mgmt="m", config=cfg)
    gauge = next(m for m in snap.metrics if m.label.startswith("Today ($"))
    assert gauge.label == "Today ($4.00/$8.00)"
    assert gauge.percent_used == pytest.approx(50.0)


@responses.activate
def test_refresh_snapshot_works_with_compact_text(monkeypatch):
    from aigauge.widget import _openrouter_compact_text

    _add_mgmt_endpoints([_key("a", 10.0, daily=1.0), _key("b", 5.0, daily=3.0)])
    snap = _run_refresh(monkeypatch, inference=None, mgmt="m")
    text, _tooltip = _openrouter_compact_text(snap)
    assert text == "OpenRouter $75.00"


def test_build_key_metrics_absent_with_one_spending_key():
    # A second key with no monthly spend does not count.
    assert _build_key_metrics([_key("a", 5.0), _key("b", 0.0)]) == []
    assert _build_key_metrics([_key("a", 5.0), _key("b", None)]) == []
    assert _build_key_metrics([]) == []


def test_build_key_metrics_sorted_with_shares_and_dollars():
    rows = _build_key_metrics(
        [_key("small", 1.0, daily=0.1), _key("zero", 0.0), _key("big", 3.0, daily=0.5)]
    )
    assert [m.label for m in rows] == ["Keys: this month", "big", "small"]
    assert all(m.tag == KEY_BREAKDOWN_TAG for m in rows)
    header, big, small = rows
    assert header.percent_used is None
    assert header.note == (
        "Spend per API key for the current UTC month. "
        "Keys with no spend this month are not listed."
    )
    assert big.percent_used == pytest.approx(75.0)
    assert small.percent_used == pytest.approx(25.0)
    assert big.percent_used + small.percent_used == pytest.approx(100.0)
    assert big.reset_label == "$3.00"
    assert small.reset_label == "$1.00"
    assert big.note == "big\nToday $0.50 \u00b7 Month $3.00"
    assert big.resets_at is None and big.window is None


def test_build_key_metrics_disabled_key_counts_and_is_flagged():
    keys = [_key("live", 2.0), _key("old", 6.0, disabled=True)]
    rows = _build_key_metrics(keys)
    old = next(m for m in rows if m.label == "old")
    assert old.note.splitlines()[-1] == "Disabled"
    assert old.percent_used == pytest.approx(75.0)
    assert _account_usage(keys)["usage_monthly"] == pytest.approx(8.0)
    live = next(m for m in rows if m.label == "live")
    assert "Disabled" not in live.note


def test_build_key_metrics_name_fallbacks_and_truncation():
    long_name = "x" * (MODEL_DISPLAY_MAX_LEN + 10)
    keys = [
        _key(long_name, 4.0),
        {"name": None, "label": "sk-or-v1-abc...def", "usage_monthly": 3.0},
        {"usage_monthly": 2.0},
    ]
    rows = _build_key_metrics(keys)[1:]
    assert rows[0].label == "x" * (MODEL_DISPLAY_MAX_LEN - 1) + "\u2026"
    assert len(rows[0].label) == MODEL_DISPLAY_MAX_LEN
    assert rows[0].note.splitlines()[0] == long_name
    assert rows[1].label == "sk-or-v1-abc...def"
    assert rows[2].label == "Unnamed key"


def test_build_key_metrics_caps_rows():
    keys = [_key(f"k{i}", float(i + 1)) for i in range(MAX_KEY_BREAKDOWN_ROWS + 3)]
    rows = _build_key_metrics(keys)
    assert len(rows) == 1 + MAX_KEY_BREAKDOWN_ROWS
    total = sum(float(i + 1) for i in range(MAX_KEY_BREAKDOWN_ROWS + 3))
    # Shares are against every spending key, not just the listed ones.
    assert rows[1].label == f"k{MAX_KEY_BREAKDOWN_ROWS + 2}"
    assert rows[1].percent_used == pytest.approx((MAX_KEY_BREAKDOWN_ROWS + 3) / total * 100.0)


def test_build_snapshot_orders_keys_before_models():
    keys = [_key("a", 3.0, daily=1.0), _key("b", 1.0, daily=1.0)]
    snap = _build_snapshot(
        credits={"total_credits": 100.0, "total_usage": 25.0},
        key_info=_account_usage(keys),
        top_models=[("gpt-4o", 5.0)],
        daily_budget=10.0,
        mgmt_key_configured=True,
        keys=keys,
    )
    labels = [m.label for m in snap.metrics]
    assert labels[0].startswith("Balance $75.00")
    assert labels[1] == "Today ($2.00/$10.00)"
    assert labels[2] == "Keys: this month"
    assert labels.index("Keys: this month") < labels.index("Models: last 30 completed UTC days")
