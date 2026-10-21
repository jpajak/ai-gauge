import logging
from datetime import datetime, timedelta

import pytest
import requests
import responses

import aigauge.config
from aigauge.config import Config
from aigauge.models import SnapshotStatus
from aigauge.providers.minimax import (
    MINIMAX_REGIONS,
    MINIMAX_USAGE_URL,
    MiniMaxProvider,
    _build_snapshot,
)

INTL = MINIMAX_REGIONS["international"][1]
CN = MINIMAX_REGIONS["china"][1]
QUOTA = "/v1/token_plan/remains"
END_5H_MS = 1_790_000_000_000
END_WEEK_MS = 1_790_400_000_000
LAYOUT_ERROR = (
    "Could not read MiniMax Coding Plan quota (response format may have changed)."
)


def _row(**overrides) -> dict:
    """A limited 'general' row shaped like the official CLI's QuotaModelRemain."""
    row = {
        "model_name": "general",
        "start_time": END_5H_MS - 5 * 3600 * 1000,
        "end_time": END_5H_MS,
        "remains_time": 1000,
        "current_interval_total_count": 1000,
        "current_interval_usage_count": 750,
        "current_interval_remaining_percent": 75,
        "current_interval_status": 1,
        "current_weekly_total_count": 5000,
        "current_weekly_usage_count": 4000,
        "current_weekly_remaining_percent": 80,
        "current_weekly_status": 1,
        "weekly_start_time": END_WEEK_MS - 7 * 86400 * 1000,
        "weekly_end_time": END_WEEK_MS,
        "weekly_remains_time": 3000,
        "weekly_boost_permille": 1000,
    }
    row.update(overrides)
    return row


def _unavailable(name: str = "video") -> dict:
    """The shape the API uses for a model the plan does not include."""
    return _row(
        model_name=name,
        current_interval_total_count=0,
        current_interval_usage_count=0,
        current_interval_remaining_percent=100,
        current_interval_status=3,
        current_weekly_total_count=0,
        current_weekly_usage_count=0,
        current_weekly_remaining_percent=100,
        current_weekly_status=3,
    )


def _idle(name: str) -> dict:
    """Both windows unlimited but with real totals: available, not limited."""
    return _row(
        model_name=name,
        current_interval_status=3,
        current_weekly_status=3,
        current_interval_total_count=10,
        current_weekly_total_count=10,
    )


def _drop(row: dict, *keys: str) -> dict:
    for key in keys:
        del row[key]
    return row


INTERVAL_FIELDS = (
    "current_interval_remaining_percent",
    "current_interval_usage_count",
    "current_interval_total_count",
)
WEEKLY_FIELDS = (
    "current_weekly_remaining_percent",
    "current_weekly_usage_count",
    "current_weekly_total_count",
)


def _payload(*rows: dict) -> dict:
    return {
        "model_remains": list(rows) if rows else [_row()],
        "base_resp": {"status_code": 0, "status_msg": "success"},
    }


def _error_payload(code: int, msg: str = "") -> dict:
    return {"base_resp": {"status_code": code, "status_msg": msg}}


def _provider(monkeypatch, key="sk-cp-test", **kwargs) -> MiniMaxProvider:
    provider = MiniMaxProvider(
        Config(),
        account_id=kwargs.pop("account_id", "minimax-work"),
        key_loader=lambda account_id: key,
        **kwargs,
    )
    monkeypatch.setattr(
        provider,
        "_run_async",
        lambda work, on_done: on_done(work()),
    )
    return provider


def _refresh(provider: MiniMaxProvider):
    captured = []
    provider.refresh(captured.append)
    assert len(captured) == 1
    return captured[0]


def _local(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000)


def test_reset_time_accepts_seconds_or_milliseconds():
    seconds = END_5H_MS // 1000
    from_ms = _build_snapshot(_payload(_row(end_time=END_5H_MS))).metrics[0]
    from_s = _build_snapshot(_payload(_row(end_time=seconds))).metrics[0]
    assert from_s.resets_at == from_ms.resets_at == _local(END_5H_MS)
    weekly_ms = _build_snapshot(
        _payload(_row(weekly_end_time=END_WEEK_MS))
    ).metrics[1]
    weekly_s = _build_snapshot(
        _payload(_row(weekly_end_time=END_WEEK_MS // 1000))
    ).metrics[1]
    assert weekly_s.resets_at == weekly_ms.resets_at == _local(END_WEEK_MS)


def test_contract_constants():
    assert MINIMAX_USAGE_URL == "https://platform.minimax.io/console/usage"
    assert MINIMAX_REGIONS == {
        "international": ("International", "https://api.minimax.io"),
        "china": ("China", "https://api.minimax.cn"),
    }
    assert MiniMaxProvider.name == "minimax"
    assert MiniMaxProvider.display_name == "MiniMax"


# --- _build_snapshot -------------------------------------------------------


def test_build_snapshot_limited_maps_percent_windows_and_resets():
    snapshot = _build_snapshot(_payload(), account_id="minimax-work", host=INTL)

    assert snapshot.provider == "minimax-work"
    assert snapshot.status == SnapshotStatus.OK
    five, weekly = snapshot.metrics
    assert (five.label, five.percent_used) == ("5h", 25.0)
    assert (weekly.label, weekly.percent_used) == ("Weekly", 20.0)
    assert five.window == timedelta(hours=5)
    assert weekly.window == timedelta(days=7)
    assert five.resets_at == _local(END_5H_MS)
    assert weekly.resets_at == _local(END_WEEK_MS)
    assert five.resets_at.tzinfo is None
    assert five.note is None and weekly.note is None
    assert snapshot.raw == {"host": INTL, "model_name": "general"}


def test_build_snapshot_exhausted_window_shows_full_with_note():
    row = _row(
        current_interval_status=2,
        current_interval_remaining_percent=0,
        current_weekly_status=2,
        current_weekly_remaining_percent=12,
    )

    five, weekly = _build_snapshot(_payload(row)).metrics

    assert five.percent_used == 100.0 and five.note == "Limit reached."
    assert weekly.percent_used == 100.0 and weekly.note == "Limit reached."
    assert five.resets_at == _local(END_5H_MS)


def test_build_snapshot_exhausted_without_percent_fields_still_full():
    row = _drop(_row(current_interval_status=2), *INTERVAL_FIELDS)

    five = _build_snapshot(_payload(row)).metrics[0]

    assert five.percent_used == 100.0
    assert five.note == "Limit reached."


def test_build_snapshot_weekly_unlimited():
    row = _row(current_weekly_status=3, current_weekly_remaining_percent=100)

    five, weekly = _build_snapshot(_payload(row)).metrics

    assert five.percent_used == 25.0
    assert weekly.label == "Weekly"
    assert weekly.percent_used is None
    assert weekly.resets_at is None
    assert weekly.reset_label == "Unlimited"
    assert weekly.window == timedelta(days=7)


def test_build_snapshot_five_hour_unlimited_same_treatment():
    row = _row(current_interval_status=3)

    five, weekly = _build_snapshot(_payload(row)).metrics

    assert five.label == "5h"
    assert five.percent_used is None
    assert five.resets_at is None
    assert five.reset_label == "Unlimited"
    assert five.window == timedelta(hours=5)
    assert weekly.percent_used == 20.0


def test_build_snapshot_boosted_weekly_keeps_base_percent_and_notes_boost():
    row = _row(weekly_boost_permille=1500, current_weekly_remaining_percent=60)

    five, weekly = _build_snapshot(_payload(row)).metrics

    assert weekly.percent_used == 40.0
    assert weekly.note == "Weekly allowance boosted to 150% (console: 90% left)."
    assert five.note is None


def test_build_snapshot_boost_of_1000_has_no_note():
    weekly = _build_snapshot(_payload(_row(weekly_boost_permille=1000))).metrics[1]

    assert weekly.note is None


def test_build_snapshot_boost_and_exhausted_notes_combine():
    row = _row(current_weekly_status=2, weekly_boost_permille=1250)

    weekly = _build_snapshot(_payload(row)).metrics[1]

    assert weekly.percent_used == 100.0
    assert weekly.note == (
        "Limit reached. Weekly allowance boosted to 125% (console: 0% left)."
    )


def test_build_snapshot_count_fallback_treats_usage_count_as_remaining():
    row = _row(
        current_interval_usage_count=900,
        current_interval_total_count=1000,
        current_weekly_usage_count=1000,
        current_weekly_total_count=4000,
    )
    _drop(
        row,
        "current_interval_remaining_percent",
        "current_weekly_remaining_percent",
    )

    five, weekly = _build_snapshot(_payload(row)).metrics

    # 900 of 1000 remaining means 10% used, not 90%.
    assert five.percent_used == pytest.approx(10.0)
    assert weekly.percent_used == pytest.approx(75.0)


def test_build_snapshot_prefers_percent_over_counts():
    row = _row(
        current_interval_remaining_percent=40,
        current_interval_usage_count=1000,
        current_interval_total_count=1000,
    )

    assert _build_snapshot(_payload(row)).metrics[0].percent_used == 60.0


def test_build_snapshot_clamps_out_of_range_percent():
    over = _build_snapshot(
        _payload(_row(current_interval_remaining_percent=130))
    ).metrics[0]
    under = _build_snapshot(
        _payload(_row(current_interval_remaining_percent=-20))
    ).metrics[0]

    assert over.percent_used == 0.0
    assert under.percent_used == 100.0


def test_build_snapshot_non_finite_percent_falls_back_to_counts():
    row = _row(
        current_interval_remaining_percent=float("nan"),
        current_interval_usage_count=500,
        current_interval_total_count=1000,
    )

    assert _build_snapshot(_payload(row)).metrics[0].percent_used == 50.0


def test_build_snapshot_non_finite_everything_is_layout_error():
    row = _row(
        current_interval_remaining_percent=float("inf"),
        current_interval_usage_count=float("nan"),
    )

    with pytest.raises(ValueError) as exc:
        _build_snapshot(_payload(row))
    assert str(exc.value) == LAYOUT_ERROR


def test_build_snapshot_no_percent_or_count_for_five_hour_is_layout_error():
    row = _drop(_row(), *INTERVAL_FIELDS)

    with pytest.raises(ValueError) as exc:
        _build_snapshot(_payload(row))
    assert str(exc.value) == LAYOUT_ERROR


def test_build_snapshot_zero_total_without_percent_is_layout_error():
    row = _row(current_interval_total_count=0, current_interval_usage_count=0)
    _drop(row, "current_interval_remaining_percent")

    with pytest.raises(ValueError):
        _build_snapshot(_payload(row))


def test_build_snapshot_unreadable_weekly_is_dropped_not_fatal():
    row = _drop(_row(), *WEEKLY_FIELDS)

    snapshot = _build_snapshot(_payload(row))

    assert [m.label for m in snapshot.metrics] == ["5h"]


def test_build_snapshot_skips_unavailable_model_for_general():
    general = _row(model_name="general", current_interval_remaining_percent=70)

    snapshot = _build_snapshot(_payload(_unavailable(), general))

    assert snapshot.raw["model_name"] == "general"
    assert snapshot.metrics[0].percent_used == 30.0


def test_build_snapshot_unavailable_row_not_chosen_even_with_preferred_name():
    video = _unavailable("minimax-m3")
    other = _row(model_name="speech", current_interval_remaining_percent=10)

    snapshot = _build_snapshot(_payload(video, other))

    assert snapshot.raw["model_name"] == "speech"


def test_build_snapshot_prefers_minimax_m_name_case_insensitively():
    other = _row(model_name="speech-hd", current_interval_remaining_percent=10)
    m_model = _row(model_name="MiniMax-M3", current_interval_remaining_percent=55)

    snapshot = _build_snapshot(_payload(other, m_model))

    assert snapshot.raw["model_name"] == "MiniMax-M3"
    assert snapshot.metrics[0].percent_used == 45.0


def test_build_snapshot_falls_back_to_first_limited_row_when_no_preferred_name():
    limited = _row(model_name="image", current_interval_remaining_percent=20)

    snapshot = _build_snapshot(_payload(_idle("music"), limited))

    assert snapshot.raw["model_name"] == "image"
    assert snapshot.metrics[0].percent_used == 80.0


def test_build_snapshot_falls_back_to_first_remaining_row():
    snapshot = _build_snapshot(_payload(_idle("alpha"), _idle("beta")))

    assert snapshot.raw["model_name"] == "alpha"


@pytest.mark.parametrize(
    "payload",
    [
        {"model_remains": []},
        {},
        {"model_remains": "nope"},
        {"model_remains": [None, 3, "x"]},
        _payload(_unavailable()),
    ],
)
def test_build_snapshot_no_usable_row_is_layout_error(payload):
    with pytest.raises(ValueError) as exc:
        _build_snapshot(payload)
    assert str(exc.value) == LAYOUT_ERROR


def test_build_snapshot_missing_or_bad_reset_time_leaves_resets_at_none():
    row = _row(end_time=None, weekly_end_time="soon")

    five, weekly = _build_snapshot(_payload(row)).metrics

    assert five.resets_at is None
    assert weekly.resets_at is None
    assert five.percent_used == 25.0


def test_build_snapshot_raw_holds_only_host_and_model_name():
    snapshot = _build_snapshot(_payload(), host=INTL)

    assert set(snapshot.raw) == {"host", "model_name"}


# --- refresh ---------------------------------------------------------------


def test_refresh_without_key_requests_setup(monkeypatch):
    provider = _provider(monkeypatch, key=None)

    snapshot = _refresh(provider)

    assert snapshot.provider == "minimax-work"
    assert snapshot.status == SnapshotStatus.AUTH_REQUIRED
    assert snapshot.error == "Add a MiniMax Coding Plan API key in Settings."


def test_refresh_without_key_does_not_touch_the_network(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("no request without a key")

    monkeypatch.setattr(requests, "get", boom)
    provider = _provider(monkeypatch, key="")

    assert _refresh(provider).status == SnapshotStatus.AUTH_REQUIRED


@responses.activate
def test_refresh_sends_bearer_key_and_accept_header_with_timeout(monkeypatch):
    responses.add(responses.GET, INTL + QUOTA, json=_payload(), status=200)
    seen = {}
    real_get = requests.get

    def spy(url, **kwargs):
        seen.update(kwargs)
        return real_get(url, **kwargs)

    monkeypatch.setattr(requests, "get", spy)
    provider = _provider(monkeypatch, key="sk-cp-secret")

    snapshot = _refresh(provider)

    assert snapshot.status == SnapshotStatus.OK
    assert [m.label for m in snapshot.metrics] == ["5h", "Weekly"]
    request = responses.calls[0].request
    assert request.url == INTL + QUOTA
    assert request.headers["Authorization"] == "Bearer sk-cp-secret"
    assert request.headers["Accept"] == "application/json"
    assert seen["timeout"] == 15
    assert snapshot.raw == {"host": INTL, "model_name": "general"}
    assert "sk-cp-secret" not in repr(snapshot)


def _urls() -> list[str]:
    return [c.request.url for c in responses.calls]


def _redirect(host: str, status: int = 302, location: str = "https://evil.example/x"):
    responses.add(
        responses.GET, host + QUOTA, status=status, headers={"Location": location}
    )


# Each outcome: how the host answers, the snapshot status and the message
# (with {host} and {label} filled in for the region under test).
OUTCOMES = {
    "http_401": (
        dict(status=401),
        SnapshotStatus.AUTH_REQUIRED,
        "MiniMax rejected the API key at {host} ({label}). "
        "Check the key and the region in Settings.",
    ),
    "base_resp_1004": (
        dict(json=_error_payload(1004, "login fail"), status=200),
        SnapshotStatus.AUTH_REQUIRED,
        "MiniMax rejected the API key at {host} ({label}). "
        "Check the key and the region in Settings.",
    ),
    "http_403": (
        dict(status=403),
        SnapshotStatus.ERROR,
        "MiniMax refused the key at {host} ({label}). Check the region in "
        "Settings and that the key has an active Coding Plan.",
    ),
    "http_404": (
        dict(status=404),
        SnapshotStatus.ERROR,
        "This key does not belong to the {label} region ({host}). "
        "Check the region in Settings.",
    ),
    "base_resp_2049": (
        dict(json=_error_payload(2049), status=200),
        SnapshotStatus.ERROR,
        "This key does not belong to the {label} region ({host}). "
        "Check the region in Settings.",
    ),
    "connection_error": (
        dict(body=requests.ConnectionError("offline")),
        SnapshotStatus.ERROR,
        "Could not reach {host} ({label}). Check the connection, or the "
        "region in Settings.",
    ),
    "timeout": (
        dict(body=requests.Timeout("slow")),
        SnapshotStatus.ERROR,
        "Could not reach {host} ({label}). Check the connection, or the "
        "region in Settings.",
    ),
}
REGION_CASES = [
    ("international", INTL, "api.minimax.io", "International"),
    ("china", CN, "api.minimax.cn", "China"),
]


@responses.activate
@pytest.mark.parametrize("outcome", OUTCOMES)
@pytest.mark.parametrize("region,base,host,label", REGION_CASES)
def test_refresh_failure_makes_one_request_to_the_selected_region_only(
    monkeypatch, region, base, host, label, outcome
):
    kwargs, status, message = OUTCOMES[outcome]
    # Every host is registered; only the selected one may be called.
    for other in (INTL, CN):
        responses.add(responses.GET, other + QUOTA, **kwargs)
    provider = _provider(monkeypatch, region=region)

    snapshot = _refresh(provider)

    assert snapshot.status == status
    assert snapshot.error == message.format(host=host, label=label)
    assert _urls() == [base + QUOTA]


@responses.activate
@pytest.mark.parametrize("region,base,host,label", REGION_CASES)
def test_refresh_redirect_is_not_followed_and_names_only_the_host(
    monkeypatch, caplog, region, base, host, label
):
    _redirect(base, 302, "https://evil.example/steal?token=abc")
    responses.add(
        responses.GET, "https://evil.example/steal", json=_payload(), status=200
    )
    provider = _provider(monkeypatch, region=region)

    with caplog.at_level(logging.INFO, logger="aigauge"):
        snapshot = _refresh(provider)

    assert snapshot.status == SnapshotStatus.ERROR
    assert snapshot.error == (
        f"{host} redirected the quota request (HTTP 302); AI Gauge does not "
        "follow redirects with an API key."
    )
    assert _urls() == [base + QUOTA]
    assert (
        "provider api diagnosis provider=minimax-work classification=redirect "
        "status=302 location_host=evil.example"
    ) in caplog.text
    assert "token=abc" not in caplog.text
    assert "token=abc" not in snapshot.error


@responses.activate
def test_refresh_china_region_requests_only_the_china_host(monkeypatch):
    responses.add(responses.GET, CN + QUOTA, json=_payload(), status=200)
    responses.add(responses.GET, INTL + QUOTA, json=_payload(), status=200)
    provider = _provider(monkeypatch, region="china")

    snapshot = _refresh(provider)

    assert snapshot.status == SnapshotStatus.OK
    assert snapshot.raw["host"] == CN
    assert _urls() == [CN + QUOTA]


@responses.activate
def test_refresh_default_region_is_international(monkeypatch):
    responses.add(responses.GET, INTL + QUOTA, json=_payload(), status=200)
    provider = _provider(monkeypatch)

    assert _refresh(provider).status == SnapshotStatus.OK
    assert _urls() == [INTL + QUOTA]


@responses.activate
def test_refresh_passes_allow_redirects_false(monkeypatch):
    responses.add(responses.GET, INTL + QUOTA, json=_payload(), status=200)
    responses.add(responses.GET, CN + QUOTA, json=_payload(), status=200)
    seen = []
    real_get = requests.get

    def spy(url, **kwargs):
        seen.append(kwargs)
        return real_get(url, **kwargs)

    monkeypatch.setattr(requests, "get", spy)

    _refresh(_provider(monkeypatch, region="international"))
    _refresh(_provider(monkeypatch, region="china"))

    assert len(seen) == 2
    assert all(kwargs["allow_redirects"] is False for kwargs in seen)


@pytest.mark.parametrize("region", ["", "mars", "CHINA"])
def test_refresh_unknown_region_sends_no_request_and_loads_no_key(
    monkeypatch, region
):
    def boom(*args, **kwargs):
        raise AssertionError("no request or key load for an unknown region")

    monkeypatch.setattr(requests, "get", boom)
    provider = MiniMaxProvider(
        Config(), account_id="minimax-work", key_loader=boom, region=region
    )

    snapshot = _refresh(provider)

    assert snapshot.status == SnapshotStatus.ERROR
    assert snapshot.error == "Choose a MiniMax region in Settings."


@responses.activate
def test_refresh_logs_the_region_host_once(monkeypatch, caplog):
    responses.add(responses.GET, CN + QUOTA, json=_payload(), status=200)
    provider = _provider(monkeypatch, region="china")

    with caplog.at_level(logging.INFO, logger="aigauge"):
        _refresh(provider)
        _refresh(provider)

    messages = [record.getMessage() for record in caplog.records]
    host_lines = [message for message in messages if "api host" in message]
    assert host_lines == [
        "provider api host provider=minimax-work region=china host=api.minimax.cn"
    ]


@responses.activate
def test_refresh_2062_is_the_no_plan_error_after_one_request(monkeypatch):
    responses.add(responses.GET, INTL + QUOTA, json=_error_payload(2062), status=200)
    responses.add(responses.GET, CN + QUOTA, json=_payload(), status=200)
    provider = _provider(monkeypatch)

    snapshot = _refresh(provider)

    assert snapshot.status == SnapshotStatus.ERROR
    assert snapshot.error == "This key has no active MiniMax Coding Plan."
    assert _urls() == [INTL + QUOTA]


@responses.activate
def test_refresh_http_500_is_reported_after_one_request(monkeypatch):
    responses.add(responses.GET, INTL + QUOTA, status=500)
    responses.add(responses.GET, CN + QUOTA, json=_payload(), status=200)
    provider = _provider(monkeypatch)

    snapshot = _refresh(provider)

    assert snapshot.status == SnapshotStatus.ERROR
    assert snapshot.error == "MiniMax usage API returned HTTP 500."
    assert _urls() == [INTL + QUOTA]


@responses.activate
def test_refresh_other_base_resp_code_reports_code_and_message(monkeypatch):
    responses.add(
        responses.GET,
        INTL + QUOTA,
        json=_error_payload(1002, "rate limited"),
        status=200,
    )
    provider = _provider(monkeypatch)

    snapshot = _refresh(provider)

    assert snapshot.status == SnapshotStatus.ERROR
    assert "1002" in snapshot.error and "rate limited" in snapshot.error


@responses.activate
def test_refresh_malformed_json_is_error_without_body(monkeypatch):
    responses.add(
        responses.GET, INTL + QUOTA, body="<html>secret-body-text</html>", status=200
    )
    provider = _provider(monkeypatch)

    snapshot = _refresh(provider)

    assert snapshot.status == SnapshotStatus.ERROR
    assert "secret-body-text" not in snapshot.error


@responses.activate
def test_refresh_non_object_json_is_error(monkeypatch):
    responses.add(responses.GET, INTL + QUOTA, json=[1, 2, 3], status=200)
    provider = _provider(monkeypatch)

    assert _refresh(provider).status == SnapshotStatus.ERROR


@responses.activate
def test_refresh_unreadable_quota_shape_is_layout_error(monkeypatch):
    responses.add(responses.GET, INTL + QUOTA, json={"model_remains": []}, status=200)
    provider = _provider(monkeypatch)

    snapshot = _refresh(provider)

    assert snapshot.status == SnapshotStatus.ERROR
    assert snapshot.error == LAYOUT_ERROR


@responses.activate
def test_refresh_logs_do_not_contain_key_or_body(monkeypatch, caplog):
    responses.add(
        responses.GET,
        INTL + QUOTA,
        json=_error_payload(1004, "body-detail"),
        status=200,
    )
    provider = _provider(monkeypatch, key="sk-cp-never-log")

    with caplog.at_level(logging.DEBUG, logger="aigauge"):
        _refresh(provider)

    assert "sk-cp-never-log" not in caplog.text
    assert "body-detail" not in caplog.text


@responses.activate
def test_refresh_default_key_loader_resolves_lazily_from_config(monkeypatch):
    calls = []

    def loader(kind, account_id):
        calls.append((kind, account_id))
        return "sk-cp-from-config"

    # The helper is added by a parallel change; raising=False lets this run first.
    monkeypatch.setattr(aigauge.config, "get_account_api_key", loader, raising=False)
    responses.add(responses.GET, INTL + QUOTA, json=_payload(), status=200)
    provider = MiniMaxProvider(Config(), account_id="minimax-2")
    monkeypatch.setattr(provider, "_run_async", lambda work, on_done: on_done(work()))

    snapshot = _refresh(provider)

    assert snapshot.status == SnapshotStatus.OK
    assert calls == [("minimax", "minimax-2")]
    assert responses.calls[0].request.headers["Authorization"] == (
        "Bearer sk-cp-from-config"
    )
