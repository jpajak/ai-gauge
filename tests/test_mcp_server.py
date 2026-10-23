from datetime import datetime, timedelta

from aigauge import mcp_server, usage_cache
from aigauge.config import Config
from aigauge.mcp_server import check_usage_guard, recommend_ai_account
from aigauge.models import SnapshotStatus, UsageMetric, UsageSnapshot
from aigauge.usage_cache import (
    CACHE_SCHEMA_VERSION,
    invalidate_usage_cache,
    read_usage_cache,
    write_usage_cache,
)


def _config(**policies: int) -> Config:
    config = Config(mcp_enabled=True)
    config.mcp_pause_policies = policies
    return config


def _cache(
    percent: object,
    *,
    account_id: str = "codex",
    status: str = "ok",
    fetched_at: datetime | None = None,
    guard_eligible: bool = True,
) -> dict:
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "published_at": datetime.now().isoformat(),
        "accounts": {
            account_id: {
                "status": status,
                "fetched_at": (fetched_at or datetime.now()).isoformat(),
                "metrics": [
                    {
                        "label": "Weekly",
                        "percent_used": percent,
                        "guard_eligible": guard_eligible,
                    }
                ],
            }
        },
    }


def _patch_context(monkeypatch, config: Config, cache: dict) -> None:
    monkeypatch.setattr("aigauge.mcp_server.Config.load", lambda: config)
    monkeypatch.setattr("aigauge.mcp_server.read_usage_cache", lambda: cache)


def test_guard_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr("aigauge.mcp_server.Config.load", lambda: Config())
    monkeypatch.setattr(
        "aigauge.mcp_server.read_usage_cache",
        lambda: (_ for _ in ()).throw(AssertionError("disabled guard read cache")),
    )

    result = check_usage_guard("codex")

    assert result["allowed"] is False
    assert result["reason_code"] == "integration_disabled"


def test_guard_blocks_at_configured_threshold(monkeypatch):
    _patch_context(monkeypatch, _config(codex=90), _cache(91))

    result = check_usage_guard("codex")

    assert result["allowed"] is False
    assert result["reason_code"] == "threshold_reached"


def test_guard_allows_below_threshold(monkeypatch):
    _patch_context(monkeypatch, _config(codex=90), _cache(72))

    result = check_usage_guard("codex")

    assert result["allowed"] is True
    assert result["reason_code"] == "allowed_below_threshold"


def test_guard_without_policy_is_explicitly_unguarded(monkeypatch):
    _patch_context(monkeypatch, _config(), {"accounts": {}})

    result = check_usage_guard("codex")

    assert result["allowed"] is True
    assert result["guard_configured"] is False
    assert result["reason_code"] == "no_policy"


def test_current_account_guard_uses_explicit_binding(monkeypatch):
    config = _config(**{"codex": 80})
    _patch_context(monkeypatch, config, _cache(85))
    monkeypatch.setattr(mcp_server, "_bound_account_id", "codex")

    result = mcp_server.check_current_account_usage()

    assert result["allowed"] is False
    assert result["account"]["account_id"] == "codex"


def test_current_account_guard_requires_binding(monkeypatch):
    monkeypatch.setattr(mcp_server, "_bound_account_id", None)

    result = mcp_server.check_current_account_usage()

    assert result["allowed"] is False
    assert result["reason_code"] == "no_binding"


def test_guard_rejects_error_snapshot_with_preserved_metrics(monkeypatch):
    config = _config(codex=90)
    _patch_context(monkeypatch, config, _cache(10, status="error"))

    result = check_usage_guard("codex")

    assert result["allowed"] is False
    assert result["reason_code"] == "snapshot_not_ok"


def test_guard_rejects_stale_snapshot(monkeypatch):
    config = _config(codex=90)
    stale = datetime.now() - timedelta(minutes=config.refresh_interval_minutes + 6)
    _patch_context(monkeypatch, config, _cache(10, fetched_at=stale))

    result = check_usage_guard("codex")

    assert result["allowed"] is False
    assert result["reason_code"] == "snapshot_stale"


def test_guard_rejects_future_or_invalid_timestamp(monkeypatch):
    config = _config(codex=90)
    cache = _cache(10)
    cache["accounts"]["codex"]["fetched_at"] = "not-a-date"
    _patch_context(monkeypatch, config, cache)

    assert check_usage_guard("codex")["reason_code"] == "invalid_timestamp"

    future = datetime.now() + timedelta(minutes=1)
    _patch_context(monkeypatch, config, _cache(10, fetched_at=future))
    assert check_usage_guard("codex")["reason_code"] == "invalid_timestamp"


def test_guard_rejects_invalid_or_ineligible_percent(monkeypatch):
    config = _config(codex=90)
    for value in (True, "10", float("nan"), float("inf")):
        _patch_context(monkeypatch, config, _cache(value))
        assert check_usage_guard("codex")["reason_code"] == "no_eligible_metric"

    _patch_context(monkeypatch, config, _cache(10, guard_eligible=False))
    assert check_usage_guard("codex")["reason_code"] == "no_eligible_metric"


def test_openrouter_breakdown_percentage_does_not_trip_guard(monkeypatch):
    config = _config(openrouter=90)
    config.providers.openrouter = True
    cache = _cache(10, account_id="openrouter")
    cache["accounts"]["openrouter"]["metrics"].append(
        {
            "label": "Only model",
            "percent_used": 100,
            "guard_eligible": False,
        }
    )
    _patch_context(monkeypatch, config, cache)

    result = check_usage_guard("openrouter")

    assert result["allowed"] is True
    assert result["account"]["max_percent_used"] == 10


def test_guard_fails_closed_for_account_absent_from_cache(monkeypatch):
    """A configured account the cache has never seen still yields a row.

    _account_row never returns None, so the guard evaluates an unknown-status
    row and blocks rather than hitting a separate missing-row path.
    """
    config = _config(codex=90)
    published = _cache(10, account_id="claude")
    published["accounts"]["claude"]["status"] = "ok"
    _patch_context(monkeypatch, config, published)

    result = check_usage_guard("codex")

    assert result["allowed"] is False
    assert result["reason_code"] == "snapshot_not_ok"
    assert result["account"]["status"] == "unknown"
    assert result["account"]["max_percent_used"] is None


def test_dst_shifted_local_timestamp_stays_fresh(monkeypatch):
    """Offset-aware timestamps survive a clock that moved backwards.

    A naive local stamp taken before a fall-back reads as future-dated
    afterwards and trips invalid_timestamp; the published offset prevents that.
    """
    from datetime import timezone

    config = _config(codex=90)
    ahead = timezone(timedelta(hours=2))
    behind = timezone(timedelta(hours=1))
    taken = datetime.now(ahead) - timedelta(minutes=2)
    cache = _cache(10)
    cache["accounts"]["codex"]["fetched_at"] = taken.isoformat()
    _patch_context(monkeypatch, config, cache)

    assert check_usage_guard("codex")["reason_code"] == "allowed_below_threshold"

    # Same instant, expressed against the post-transition offset.
    cache["accounts"]["codex"]["fetched_at"] = taken.astimezone(behind).isoformat()
    assert check_usage_guard("codex")["reason_code"] == "allowed_below_threshold"


def test_published_cache_timestamps_carry_utc_offset(tmp_path, monkeypatch):
    monkeypatch.setattr("aigauge.usage_cache.app_data_dir", lambda: tmp_path)
    write_usage_cache(
        {
            "codex": UsageSnapshot(
                provider="codex",
                status=SnapshotStatus.OK,
                metrics=[UsageMetric("Weekly", 42)],
            )
        }
    )
    cached = read_usage_cache()

    assert datetime.fromisoformat(cached["published_at"]).tzinfo is not None
    fetched_at = cached["accounts"]["codex"]["fetched_at"]
    assert datetime.fromisoformat(fetched_at).tzinfo is not None


def test_guard_rejects_removed_or_disabled_account(monkeypatch):
    config = _config(codex=90)
    config.browser_accounts = []
    _patch_context(monkeypatch, config, _cache(10))

    result = check_usage_guard("codex")

    assert result["allowed"] is False
    assert result["reason_code"] == "account_unavailable"


def test_recommendation_uses_most_verified_policy_headroom(monkeypatch):
    config = _config(codex=90, claude=80)
    cache = _cache(70)
    cache["accounts"]["claude"] = _cache(
        20, account_id="claude"
    )["accounts"]["claude"]
    _patch_context(monkeypatch, config, cache)

    result = recommend_ai_account()

    assert result["account"]["account_id"] == "claude"
    assert result["headroom_percent"] == 60


def test_recommendation_returns_none_when_all_accounts_blocked(monkeypatch):
    config = _config(codex=90)
    _patch_context(monkeypatch, config, _cache(95))

    result = recommend_ai_account()

    assert result["account"] is None
    assert result["reason_code"] == "no_allowed_account"


def test_usage_cache_is_minimal_and_omits_sensitive_fields(tmp_path, monkeypatch):
    monkeypatch.setattr("aigauge.usage_cache.app_data_dir", lambda: tmp_path)
    snapshot = UsageSnapshot(
        provider="codex",
        status=SnapshotStatus.ERROR,
        metrics=[UsageMetric("Weekly", 42, note="must-not-leak")],
        error="secret exception must-not-leak",
        raw={"cookie": "must-not-leak"},
    )

    write_usage_cache({"codex": snapshot})
    cached = read_usage_cache()

    assert cached["schema_version"] == CACHE_SCHEMA_VERSION
    assert cached["accounts"]["codex"]["metrics"][0]["percent_used"] == 42
    assert "must-not-leak" not in str(cached)


def test_usage_cache_marks_tagged_metrics_ineligible(tmp_path, monkeypatch):
    monkeypatch.setattr("aigauge.usage_cache.app_data_dir", lambda: tmp_path)
    snapshot = UsageSnapshot(
        provider="openrouter",
        status=SnapshotStatus.OK,
        metrics=[
            UsageMetric("Daily", 10),
            UsageMetric("Only model", 100, tag="model_breakdown"),
        ],
    )

    write_usage_cache({"openrouter": snapshot})
    metrics = read_usage_cache()["accounts"]["openrouter"]["metrics"]

    assert [metric["guard_eligible"] for metric in metrics] == [True, False]


def test_usage_cache_honours_guard_eligible_flag(tmp_path, monkeypatch):
    monkeypatch.setattr("aigauge.usage_cache.app_data_dir", lambda: tmp_path)
    snapshot = UsageSnapshot(
        provider="codex",
        status=SnapshotStatus.OK,
        metrics=[
            UsageMetric("Weekly", 10),
            UsageMetric("Credit", 20, guard_eligible=False),
            UsageMetric("Breakdown", 30, tag="model_breakdown", guard_eligible=False),
            UsageMetric("Tagged", 40, tag="model_breakdown"),
        ],
    )

    write_usage_cache({"codex": snapshot})
    metrics = read_usage_cache()["accounts"]["codex"]["metrics"]

    assert [metric["guard_eligible"] for metric in metrics] == [
        True,
        False,
        False,
        False,
    ]


def test_balance_only_account_is_blocked_and_never_recommended(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("aigauge.usage_cache.app_data_dir", lambda: tmp_path)
    write_usage_cache(
        {
            "codex": UsageSnapshot(
                provider="codex",
                status=SnapshotStatus.OK,
                metrics=[UsageMetric("Credit", 5, guard_eligible=False)],
            )
        }
    )
    _patch_context(monkeypatch, _config(codex=90), read_usage_cache())

    assert check_usage_guard("codex")["reason_code"] == "no_eligible_metric"
    result = recommend_ai_account()
    assert result["account"] is None
    assert result["reason_code"] == "no_allowed_account"


def test_usage_cache_can_be_invalidated(tmp_path, monkeypatch):
    monkeypatch.setattr("aigauge.usage_cache.app_data_dir", lambda: tmp_path)
    write_usage_cache({})

    invalidate_usage_cache()

    assert not usage_cache.usage_cache_path().exists()


def test_usage_cache_invalid_utf8_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr("aigauge.usage_cache.app_data_dir", lambda: tmp_path)
    usage_cache.usage_cache_path().write_bytes(b"\xff")
    config = _config(codex=90)
    monkeypatch.setattr("aigauge.mcp_server.Config.load", lambda: config)
    monkeypatch.setattr(mcp_server, "read_usage_cache", read_usage_cache)

    result = check_usage_guard("codex")

    assert result["allowed"] is False
    assert result["reason_code"] == "cache_unavailable"


def test_missing_cache_is_reported_as_unavailable_not_failed_refresh(
    tmp_path, monkeypatch
):
    """A guard blocked because AI Gauge is not running must say so.

    An unreadable cache still carries the current schema version, so without a
    distinct code it surfaces as ``snapshot_not_ok`` — "the latest refresh is
    not OK" — when in fact no refresh was ever published.
    """
    monkeypatch.setattr("aigauge.usage_cache.app_data_dir", lambda: tmp_path)
    config = _config(codex=90)
    monkeypatch.setattr("aigauge.mcp_server.Config.load", lambda: config)
    monkeypatch.setattr(mcp_server, "read_usage_cache", read_usage_cache)

    result = check_usage_guard("codex")

    assert result["allowed"] is False
    assert result["reason_code"] == "cache_unavailable"

    usage = mcp_server.get_ai_usage()

    assert usage["accounts"] == []
    assert usage["reason_code"] == "cache_unavailable"
    assert "has not published" in usage["reason"]


def test_published_cache_is_never_treated_as_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr("aigauge.usage_cache.app_data_dir", lambda: tmp_path)
    write_usage_cache(
        {
            "codex": UsageSnapshot(
                provider="codex",
                status=SnapshotStatus.OK,
                metrics=[UsageMetric("Weekly", 12)],
            )
        }
    )
    config = _config(codex=90)
    monkeypatch.setattr("aigauge.mcp_server.Config.load", lambda: config)
    monkeypatch.setattr(mcp_server, "read_usage_cache", read_usage_cache)

    assert check_usage_guard("codex")["reason_code"] == "allowed_below_threshold"


def test_empty_but_published_cache_is_distinct_from_missing(tmp_path, monkeypatch):
    """Publishing zero accounts is a real refresh, not an unavailable cache."""
    monkeypatch.setattr("aigauge.usage_cache.app_data_dir", lambda: tmp_path)
    write_usage_cache({})

    assert read_usage_cache().get("available") is True


def test_usage_cache_retries_windows_sharing_violation(tmp_path, monkeypatch):
    monkeypatch.setattr("aigauge.usage_cache.app_data_dir", lambda: tmp_path)
    snapshot = UsageSnapshot(
        provider="codex",
        status=SnapshotStatus.OK,
        metrics=[UsageMetric("Weekly", 42)],
    )
    real_replace = usage_cache.os.replace
    attempts = 0

    def briefly_locked(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError(32, "file is being used by another process")
        real_replace(source, destination)

    monkeypatch.setattr("aigauge.usage_cache.os.replace", briefly_locked)
    monkeypatch.setattr("aigauge.usage_cache.time.sleep", lambda _delay: None)

    write_usage_cache({"codex": snapshot})

    assert attempts == 3
    assert read_usage_cache()["accounts"]["codex"]["status"] == "ok"
    assert list(tmp_path.glob("*.tmp")) == []


def test_minimax_account_is_configured_only_when_enabled():
    config = _config()

    assert "minimax" not in mcp_server._configured_account_ids(config)  # noqa: SLF001
    config.providers.minimax = True
    assert "minimax" in mcp_server._configured_account_ids(config)  # noqa: SLF001


def test_minimax_account_can_be_guarded(monkeypatch):
    config = _config(minimax=90)
    config.providers.minimax = True
    _patch_context(monkeypatch, config, _cache(95, account_id="minimax"))

    result = check_usage_guard("minimax")

    assert result["allowed"] is False
    assert result["reason_code"] != "account_unavailable"
