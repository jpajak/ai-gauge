import logging
from datetime import datetime, timedelta

import pytest
import requests
import responses

import aigauge.config
from aigauge.config import Config
from aigauge.models import SnapshotStatus
from aigauge.providers.minimax import (
    MINIMAX_API_HOSTS,
    MINIMAX_USAGE_URL,
    MiniMaxProvider,
    _build_snapshot,
)

INTL, CN, CN_OLD = MINIMAX_API_HOSTS
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


def test_contract_constants():
    assert MINIMAX_USAGE_URL == "https://platform.minimax.io/console/usage"
    assert MINIMAX_API_HOSTS == (
        "https://api.minimax.io",
        "https://api.minimax.cn",
        "https://api.minimaxi.com",
    )
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
    assert weekly.note == "Weekly allowance boosted to 150%."
    assert five.note is None


def test_build_snapshot_boost_of_1000_has_no_note():
    weekly = _build_snapshot(_payload(_row(weekly_boost_permille=1000))).metrics[1]

    assert weekly.note is None


def test_build_snapshot_boost_and_exhausted_notes_combine():
    row = _row(current_weekly_status=2, weekly_boost_permille=1250)

    weekly = _build_snapshot(_payload(row)).metrics[1]

    assert weekly.percent_used == 100.0
    assert weekly.note == "Limit reached. Weekly allowance boosted to 125%."


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


@responses.activate
def test_refresh_wrong_host_2049_advances_and_remembers_working_host(monkeypatch):
    responses.add(responses.GET, INTL + QUOTA, json=_error_payload(2049), status=200)
    responses.add(responses.GET, CN + QUOTA, json=_payload(), status=200)
    provider = _provider(monkeypatch)

    first = _refresh(provider)

    assert first.status == SnapshotStatus.OK
    assert first.raw["host"] == CN
    assert [c.request.url for c in responses.calls] == [INTL + QUOTA, CN + QUOTA]

    responses.calls.reset()
    second = _refresh(provider)

    assert second.status == SnapshotStatus.OK
    assert [c.request.url for c in responses.calls] == [CN + QUOTA]


@responses.activate
def test_refresh_remembered_host_failing_with_2049_tries_the_others(monkeypatch):
    responses.add(responses.GET, INTL + QUOTA, json=_payload(), status=200)
    responses.add(responses.GET, CN + QUOTA, json=_error_payload(2049), status=200)
    provider = _provider(monkeypatch)
    provider._host = CN

    snapshot = _refresh(provider)

    assert snapshot.status == SnapshotStatus.OK
    assert [c.request.url for c in responses.calls] == [CN + QUOTA, INTL + QUOTA]
    assert provider._host == INTL


@responses.activate
def test_refresh_http_404_falls_through_to_next_host(monkeypatch):
    responses.add(responses.GET, INTL + QUOTA, status=404)
    responses.add(responses.GET, CN + QUOTA, status=404)
    responses.add(responses.GET, CN_OLD + QUOTA, json=_payload(), status=200)
    provider = _provider(monkeypatch)

    snapshot = _refresh(provider)

    assert snapshot.status == SnapshotStatus.OK
    assert snapshot.raw["host"] == CN_OLD
    assert provider._host == CN_OLD


@responses.activate
def test_refresh_all_hosts_exhausted_names_last_host(monkeypatch):
    responses.add(responses.GET, INTL + QUOTA, json=_error_payload(2049), status=200)
    responses.add(responses.GET, CN + QUOTA, status=404)
    responses.add(
        responses.GET, CN_OLD + QUOTA, json=_error_payload(2049), status=200
    )
    provider = _provider(monkeypatch)

    snapshot = _refresh(provider)

    assert snapshot.status == SnapshotStatus.ERROR
    assert CN_OLD in (snapshot.error or "")
    assert len(responses.calls) == 3
    assert provider._host is None


@responses.activate
def test_refresh_does_not_advance_host_on_other_failures(monkeypatch):
    responses.add(responses.GET, INTL + QUOTA, status=500)
    provider = _provider(monkeypatch)

    snapshot = _refresh(provider)

    assert snapshot.status == SnapshotStatus.ERROR
    assert snapshot.error == "MiniMax usage API returned HTTP 500."
    assert len(responses.calls) == 1


@responses.activate
def test_refresh_base_resp_1004_is_auth_required_without_retry(monkeypatch):
    responses.add(
        responses.GET,
        INTL + QUOTA,
        json=_error_payload(1004, "login fail"),
        status=200,
    )
    provider = _provider(monkeypatch)

    snapshot = _refresh(provider)

    assert snapshot.status == SnapshotStatus.AUTH_REQUIRED
    assert snapshot.error == "MiniMax rejected the API key. Update it in Settings."
    assert len(responses.calls) == 1


@responses.activate
def test_refresh_base_resp_2062_is_error_no_plan(monkeypatch):
    responses.add(responses.GET, INTL + QUOTA, json=_error_payload(2062), status=200)
    provider = _provider(monkeypatch)

    snapshot = _refresh(provider)

    assert snapshot.status == SnapshotStatus.ERROR
    assert snapshot.error == "This key has no active MiniMax Coding Plan."
    assert len(responses.calls) == 1


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
def test_refresh_http_401_is_auth_required(monkeypatch):
    responses.add(responses.GET, INTL + QUOTA, status=401)
    provider = _provider(monkeypatch)

    snapshot = _refresh(provider)

    assert snapshot.status == SnapshotStatus.AUTH_REQUIRED
    assert "rejected" in snapshot.error


@responses.activate
def test_refresh_http_403_is_error(monkeypatch):
    responses.add(responses.GET, INTL + QUOTA, status=403)
    provider = _provider(monkeypatch)

    snapshot = _refresh(provider)

    assert snapshot.status == SnapshotStatus.ERROR
    assert "Coding Plan" in snapshot.error


@responses.activate
def test_refresh_transport_error_is_error(monkeypatch):
    responses.add(
        responses.GET, INTL + QUOTA, body=requests.ConnectionError("offline")
    )
    provider = _provider(monkeypatch)

    snapshot = _refresh(provider)

    assert snapshot.status == SnapshotStatus.ERROR
    assert "failed" in snapshot.error
    assert len(responses.calls) == 1


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
