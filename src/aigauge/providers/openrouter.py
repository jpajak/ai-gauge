from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Callable

import requests

from ..config import Config, get_openrouter_key, get_openrouter_mgmt_key
from ..models import SnapshotStatus, UsageMetric, UsageSnapshot
from .base import Provider

OPENROUTER_API = "https://openrouter.ai/api/v1"
log = logging.getLogger("aigauge.providers.openrouter")

MODEL_BREAKDOWN_TAG = "model_breakdown"
ACTIVITY_LABEL = "Models"
MAX_MODEL_BREAKDOWN_ROWS = 6


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }


def _next_local_midnight() -> datetime:
    now = datetime.now()
    tomorrow = (now + timedelta(days=1)).date()
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day)


def _fetch_credits(api_key: str) -> dict | None:
    """Returns the credits payload. Raises HTTPError on any non-200 response.

    /credits requires a management key per OpenRouter docs. A 401/403 here means
    the configured management key is invalid, which the caller surfaces as
    AUTH_REQUIRED rather than silently hiding the credits row.
    """
    r = requests.get(
        f"{OPENROUTER_API}/credits",
        headers=_headers(api_key),
        timeout=15,
    )
    r.raise_for_status()
    payload = r.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    log.debug(
        "provider api diagnosis provider=openrouter "
        "classification=credits_ok status=%s payload_keys=%s",
        r.status_code,
        sorted(data.keys()) if isinstance(data, dict) else [],
    )
    return data


def _fetch_key_info(api_key: str) -> dict:
    r = requests.get(
        f"{OPENROUTER_API}/key",
        headers=_headers(api_key),
        timeout=15,
    )
    r.raise_for_status()
    payload = r.json()
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    log.debug(
        "provider api diagnosis provider=openrouter "
        "classification=key_ok status=%s payload_keys=%s",
        r.status_code,
        sorted(data.keys()) if isinstance(data, dict) else [],
    )
    return data


def _fetch_activity(
    api_key: str,
    activity_date: str | None = None,
) -> tuple[list[dict], str | None]:
    """Returns (rows, error_message).

    On success: (rows, None). On any HTTP error or network failure:
    ([], "human readable reason"). The caller surfaces the error in the tile
    instead of silently dropping the model breakdown.

    /activity requires a management key per OpenRouter docs.
    """
    try:
        r = requests.get(
            f"{OPENROUTER_API}/activity",
            headers=_headers(api_key),
            params={"date": activity_date} if activity_date else None,
            timeout=15,
        )
    except requests.RequestException as exc:
        log.warning(
            "provider api diagnosis provider=openrouter "
            "classification=activity_request_error type=%s",
            type(exc).__name__,
        )
        return [], f"network error: {exc}"
    if r.status_code != 200:
        log.warning(
            "provider api diagnosis provider=openrouter "
            "classification=activity_http_error status=%s",
            r.status_code,
        )
        return [], f"/activity returned HTTP {r.status_code}"
    try:
        payload = r.json()
    except ValueError as exc:
        log.warning(
            "provider api diagnosis provider=openrouter "
            "classification=activity_json_decode_error",
        )
        return [], f"/activity returned non-JSON response: {exc}"
    rows = payload.get("data", []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        log.warning(
            "provider api diagnosis provider=openrouter "
            "classification=activity_unexpected_shape payload_type=%s",
            type(payload).__name__,
        )
        return [], "/activity response shape unexpected"
    log.debug(
        "provider api diagnosis provider=openrouter "
        "classification=activity_ok status=%s rows=%d date=%s",
        r.status_code,
        len(rows),
        activity_date or "last_30_completed_days",
    )
    return rows, None


def _activity_model_costs(rows: list[dict]) -> list[tuple[str, float]]:
    """Aggregate per-model cost from activity rows, sorted by cost descending."""
    by_model: dict[str, float] = defaultdict(float)
    for row in rows:
        if not isinstance(row, dict):
            continue
        model = row.get("model") or row.get("model_permaslug") or row.get("model_name")
        if not model:
            continue
        cost = row.get("usage")
        if cost is None:
            cost = row.get("cost", 0)
        try:
            by_model[str(model)] += float(cost or 0)
        except (TypeError, ValueError):
            continue
    return sorted(by_model.items(), key=lambda kv: kv[1], reverse=True)


def _build_credits_metric(credits: dict | None) -> UsageMetric | None:
    remaining = _remaining_balance(credits)
    if remaining is None:
        return None
    return UsageMetric(
        label=f"Balance (${remaining:.2f} left)",
        percent_used=None,
        note="Account balance from OpenRouter.",
    )


def _remaining_balance(credits: dict | None) -> float | None:
    if not credits:
        return None
    total = credits.get("total_credits")
    used = credits.get("total_usage")
    try:
        total_f = float(total) if total is not None else None
        used_f = float(used) if used is not None else None
    except (TypeError, ValueError):
        return None
    if total_f is None or used_f is None:
        return None
    return max(0.0, total_f - used_f)


def _daily_usage(key_info: dict) -> float | None:
    return _usage_amount(key_info, "usage_daily")


def _usage_amount(key_info: dict, field: str) -> float | None:
    raw = key_info.get(field)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _build_summary_metric(
    credits: dict | None,
    key_info: dict,
    daily_budget: float | None,
) -> UsageMetric | None:
    remaining = _remaining_balance(credits)

    parts: list[str] = []
    if remaining is not None:
        parts.append(f"Balance ${remaining:.2f} left")
    spend_fields = [
        ("today", _usage_amount(key_info, "usage_daily")),
        ("month", _usage_amount(key_info, "usage_monthly")),
    ]
    spend_parts = [
        f"{label} ${amount:.2f}" for label, amount in spend_fields if amount is not None
    ]
    if spend_parts:
        parts.append(f"Spend {' / '.join(spend_parts)}")
    if not parts:
        return None
    return UsageMetric(
        label=" · ".join(parts),
        percent_used=None,
        note=(
            "OpenRouter account balance, current UTC day spend, and current "
            "UTC month spend."
        ),
    )


def _build_daily_metric(key_info: dict, daily_budget: float | None) -> UsageMetric | None:
    daily_f = _daily_usage(key_info)
    if daily_f is None:
        return None
    midnight = _next_local_midnight()
    if daily_budget and daily_budget > 0:
        percent = max(0.0, min(100.0, daily_f / daily_budget * 100.0))
        return UsageMetric(
            label=f"Today (${daily_f:.2f}/${daily_budget:.2f})",
            percent_used=percent,
            resets_at=midnight,
            window=timedelta(days=1),
        )
    return None


def _build_model_metrics(
    top_models: list[tuple[str, float]],
    activity_date: str | None = None,
) -> list[UsageMetric]:
    if not top_models:
        return []
    activity_total = sum(cost for _, cost in top_models)
    metrics: list[UsageMetric] = [
        UsageMetric(
            label=(
                f"{ACTIVITY_LABEL}: {activity_date} UTC"
                if activity_date
                else f"{ACTIVITY_LABEL}: last 30 completed UTC days"
            ),
            percent_used=None,
            note=(
                "OpenRouter /activity returns completed UTC days only; "
                "the current UTC day is not included."
            ),
            tag=MODEL_BREAKDOWN_TAG,
        )
    ]
    for model, cost in top_models[:MAX_MODEL_BREAKDOWN_ROWS]:
        percent = (cost / activity_total * 100.0) if activity_total > 0 else None
        metrics.append(
            UsageMetric(
                label=model,
                percent_used=percent,
                note=f"${cost:.2f}",
                tag=MODEL_BREAKDOWN_TAG,
            )
        )
    return metrics


def _build_snapshot(
    credits: dict | None,
    key_info: dict,
    top_models: list[tuple[str, float]],
    daily_budget: float | None,
    *,
    mgmt_key_configured: bool,
    activity_error: str | None = None,
    activity_date: str | None = None,
) -> UsageSnapshot:
    metrics: list[UsageMetric] = []

    summary_metric = _build_summary_metric(credits, key_info, daily_budget)
    if summary_metric:
        metrics.append(summary_metric)
    if not mgmt_key_configured:
        # Management key was deliberately not configured. Show a clearly
        # visible row pointing at Settings, not a buried hint on another row.
        metrics.append(
            UsageMetric(
                label="Account balance",
                percent_used=None,
                note="Add a management key in Settings to show remaining credits.",
            )
        )
    # If mgmt_key_configured is True but credits is None here, _fetch_credits
    # raised and the caller already returned an ERROR/AUTH_REQUIRED snapshot,
    # so we never get here with that combination.

    daily_metric = _build_daily_metric(key_info, daily_budget)
    if daily_metric:
        metrics.append(daily_metric)

    if activity_error:
        # /activity failed. Surface a clearly visible row instead of silently
        # dropping the model breakdown.
        metrics.append(
            UsageMetric(
                label=f"{ACTIVITY_LABEL}: unavailable",
                percent_used=None,
                note=f"Unavailable: {activity_error}",
            )
        )
    elif mgmt_key_configured and not top_models:
        metrics.append(
            UsageMetric(
                label=f"{ACTIVITY_LABEL}: none",
                percent_used=None,
                note="No activity returned for the last 30 completed UTC days.",
            )
        )
    else:
        metrics.extend(_build_model_metrics(top_models, activity_date))

    return UsageSnapshot(
        provider="openrouter",
        status=SnapshotStatus.OK,
        metrics=metrics,
        raw={
            "credits": credits,
            "key": key_info,
            "top_models": [list(pair) for pair in top_models],
            "activity_error": activity_error,
            "activity_date": activity_date,
            "activity_window": "last_30_completed_utc_days"
            if mgmt_key_configured and activity_date is None
            else None,
            "mgmt_key_configured": mgmt_key_configured,
        },
    )


class OpenRouterProvider(Provider):
    name = "openrouter"
    display_name = "OpenRouter"

    def __init__(self, config: Config, pool=None):
        self._config = config
        self._pool = pool

    def refresh(self, on_done: Callable[[UsageSnapshot], None]) -> None:
        api_key = get_openrouter_key()
        mgmt_key = get_openrouter_mgmt_key()
        log.debug(
            "provider api diagnosis provider=openrouter "
            "classification=refresh_start inference_key_configured=%s "
            "management_key_configured=%s",
            bool(api_key),
            bool(mgmt_key),
        )
        if not api_key:
            log.info(
                "provider api diagnosis provider=openrouter classification=missing_key"
            )
            on_done(
                UsageSnapshot(
                    provider="openrouter",
                    status=SnapshotStatus.AUTH_REQUIRED,
                    error="OpenRouter API key not set. Configure it in Settings.",
                )
            )
            return

        config = self._config

        def work() -> UsageSnapshot:
            # /credits requires a management key per OpenRouter docs. Only call
            # it when the user provided one. If they didn't, _build_snapshot
            # adds a visible "configure management key" row instead of silently
            # hiding the credits.
            credits: dict | None = None
            if mgmt_key:
                try:
                    credits = _fetch_credits(mgmt_key)
                except requests.HTTPError as exc:
                    status = exc.response.status_code if exc.response is not None else 0
                    log.warning(
                        "provider api diagnosis provider=openrouter "
                        "classification=credits_http_error status=%s",
                        status,
                    )
                    if status in (401, 403):
                        return UsageSnapshot(
                            provider="openrouter",
                            status=SnapshotStatus.AUTH_REQUIRED,
                            error=(
                                f"OpenRouter rejected the management key "
                                f"({status}). Update or remove it in Settings."
                            ),
                        )
                    return UsageSnapshot(
                        provider="openrouter",
                        status=SnapshotStatus.ERROR,
                        error=f"OpenRouter /credits {status}",
                    )
                except requests.RequestException as exc:
                    return UsageSnapshot(
                        provider="openrouter",
                        status=SnapshotStatus.ERROR,
                        error=str(exc),
                    )

            try:
                key_info = _fetch_key_info(api_key)
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                log.warning(
                    "provider api diagnosis provider=openrouter "
                    "classification=key_http_error status=%s",
                    status,
                )
                if status in (401, 403):
                    return UsageSnapshot(
                        provider="openrouter",
                        status=SnapshotStatus.AUTH_REQUIRED,
                        error=f"OpenRouter rejected the inference key ({status}).",
                    )
                return UsageSnapshot(
                    provider="openrouter",
                    status=SnapshotStatus.ERROR,
                    error=f"OpenRouter /key {status}",
                )
            except requests.RequestException as exc:
                return UsageSnapshot(
                    provider="openrouter",
                    status=SnapshotStatus.ERROR,
                    error=str(exc),
                )

            if mgmt_key:
                activity_date = None
                activity, activity_error = _fetch_activity(mgmt_key)
            else:
                log.debug(
                    "provider api diagnosis provider=openrouter "
                    "classification=management_endpoints_skipped reason=missing_mgmt_key"
                )
                activity = []
                activity_error = (
                    "Add a management key in Settings to show model activity."
                )
            top_models = _activity_model_costs(activity)
            return _build_snapshot(
                credits,
                key_info,
                top_models,
                config.openrouter.daily_budget,
                mgmt_key_configured=bool(mgmt_key),
                activity_error=activity_error,
                activity_date=activity_date if mgmt_key else None,
            )

        self._run_async(work, on_done)

    def _run_async(
        self,
        work: Callable[[], UsageSnapshot],
        on_done: Callable[[UsageSnapshot], None],
    ) -> None:
        from PyQt6.QtCore import QRunnable, QThreadPool  # local import: keep tests Qt-free

        class _Worker(QRunnable):
            def run(self_inner) -> None:  # noqa: N805
                try:
                    snapshot = work()
                except Exception as exc:  # noqa: BLE001
                    log.exception(
                        "provider api diagnosis provider=openrouter "
                        "classification=unexpected_exception type=%s",
                        type(exc).__name__,
                    )
                    snapshot = UsageSnapshot(
                        provider="openrouter",
                        status=SnapshotStatus.ERROR,
                        error=str(exc),
                    )
                on_done(snapshot)

        pool = self._pool or QThreadPool.globalInstance()
        pool.start(_Worker())
