from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import parse_qsl, urlparse

import requests

from ..config import Config, get_github_pat
from ..models import SnapshotStatus, UsageMetric, UsageSnapshot
from .base import Provider

GITHUB_API = "https://api.github.com"
GITHUB_API_VERSION = "2026-03-10"
COPILOT_PRODUCT = "copilot"
COPILOT_AI_CREDITS_SKU = "copilot_ai_credits"
COPILOT_AI_UNIT_SKU = "copilot_ai_unit"
LEGACY_PREMIUM_REQUEST_SKU = "copilot_premium_request"
log = logging.getLogger("aigauge.providers.copilot")


def _github_headers(pat: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }


def _usage_params(username: str | None = None) -> dict[str, int | str]:
    now = datetime.now(timezone.utc)
    params: dict[str, int | str] = {"year": now.year, "month": now.month}
    if username:
        params["user"] = username
    return params


def _summary_params(username: str | None = None) -> dict[str, int | str]:
    params = _usage_params(username)
    params["product"] = COPILOT_PRODUCT
    return params


def _this_month_start_utc(now_utc: datetime) -> datetime:
    """First day of this calendar month in UTC."""
    return datetime(now_utc.year, now_utc.month, 1, tzinfo=timezone.utc)


def _next_month_start_utc(now_utc: datetime) -> datetime:
    """First day of next calendar month - Copilot usage resets monthly."""
    if now_utc.month == 12:
        return datetime(now_utc.year + 1, 1, 1, tzinfo=timezone.utc)
    return datetime(now_utc.year, now_utc.month + 1, 1, tzinfo=timezone.utc)


def _resolve_username(pat: str, configured: str | None) -> str | None:
    if configured:
        return configured
    try:
        r = requests.get(
            f"{GITHUB_API}/user",
            headers=_github_headers(pat),
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("login")
    except requests.RequestException:
        return None
    return None


def _fetch_user_premium_usage(pat: str, username: str) -> dict:
    r = requests.get(
        f"{GITHUB_API}/users/{username}/settings/billing/premium_request/usage",
        params=_usage_params(),
        headers=_github_headers(pat),
        timeout=15,
    )
    r.raise_for_status()
    payload = r.json()
    _log_payload("legacy_premium_usage", "user", r, payload)
    return payload


def _fetch_user_credit_usage(pat: str, username: str) -> dict:
    r = requests.get(
        f"{GITHUB_API}/users/{username}/settings/billing/usage/summary",
        params=_summary_params(),
        headers=_github_headers(pat),
        timeout=15,
    )
    r.raise_for_status()
    payload = r.json()
    _log_payload("credit_usage", "user", r, payload)
    return payload


def _fetch_org_premium_usage(pat: str, org: str, username: str) -> dict:
    r = requests.get(
        f"{GITHUB_API}/organizations/{org}/settings/billing/premium_request/usage",
        params=_usage_params(username),
        headers=_github_headers(pat),
        timeout=15,
    )
    r.raise_for_status()
    payload = r.json()
    _log_payload("legacy_premium_usage", "org", r, payload)
    return payload


def _fetch_org_credit_usage(pat: str, org: str) -> dict:
    r = requests.get(
        f"{GITHUB_API}/organizations/{org}/settings/billing/usage/summary",
        params=_summary_params(),
        headers=_github_headers(pat),
        timeout=15,
    )
    r.raise_for_status()
    payload = r.json()
    _log_payload("credit_usage", "org", r, payload)
    return payload


def _fetch_premium_usage(pat: str, username: str) -> dict:
    """Backward-compatible alias for tests and callers."""
    return _fetch_user_premium_usage(pat, username)


def _fetch_credit_usage(pat: str, username: str) -> dict:
    """Fetch current usage-based Copilot AI credit usage for an individual."""
    return _fetch_user_credit_usage(pat, username)


def _norm(value: object) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _item_summary(item: dict) -> dict[str, object]:
    return {
        "keys": sorted(str(key) for key in item.keys()),
        "product": item.get("product"),
        "sku": item.get("sku"),
        "unitType": item.get("unitType") or item.get("unit_type"),
        "model": item.get("model"),
        "grossQuantity": item.get("grossQuantity"),
        "quantity": item.get("quantity"),
        "grossAmount": item.get("grossAmount"),
        "netQuantity": item.get("netQuantity"),
        "netAmount": item.get("netAmount"),
    }


def _safe_scalar(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 120 else value[:117] + "..."
    return type(value).__name__


def _payload_scalar_summary(payload: dict) -> dict[str, object]:
    return {
        str(key): _safe_scalar(value)
        for key, value in payload.items()
        if not isinstance(value, (dict, list))
    }


def _payload_collection_summary(payload: dict) -> dict[str, object]:
    summary: dict[str, object] = {}
    for key, value in payload.items():
        if isinstance(value, list):
            summary[str(key)] = f"list[{len(value)}]"
        elif isinstance(value, dict):
            summary[str(key)] = f"dict[{len(value)}]"
    return summary


def _query_summary(response: requests.Response) -> dict[str, object]:
    parsed = urlparse(response.url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    return {
        "year": query.get("year"),
        "month": query.get("month"),
        "product": query.get("product"),
        "has_user_param": "user" in query,
    }


def _log_payload(
    endpoint: str,
    scope: str,
    response: requests.Response,
    payload: dict,
) -> None:
    raw_items = payload.get("usageItems", []) or []
    items = raw_items if isinstance(raw_items, list) else []
    sample = [_item_summary(item) for item in items[:5] if isinstance(item, dict)]
    log.info(
        "provider api diagnosis provider=copilot "
        "classification=payload endpoint=%s scope=%s status=%s request_id=%s "
        "payload_keys=%s item_count=%s credit_item_count=%s "
        "legacy_item_count=%s query=%r scalar_fields=%r collection_sizes=%r "
        "sample=%r",
        endpoint,
        scope,
        response.status_code,
        response.headers.get("x-github-request-id", ""),
        sorted(str(key) for key in payload.keys()),
        len(items),
        sum(1 for item in items if isinstance(item, dict) and _is_credit_item(item)),
        sum(
            1
            for item in items
            if isinstance(item, dict) and _is_legacy_premium_item(item)
        ),
        _query_summary(response),
        _payload_scalar_summary(payload),
        _payload_collection_summary(payload),
        sample,
    )


def _is_credit_item(item: dict) -> bool:
    sku = _norm(item.get("sku"))
    unit = _norm(item.get("unitType") or item.get("unit_type"))
    product = _norm(item.get("product"))
    return (
        sku in (COPILOT_AI_CREDITS_SKU, COPILOT_AI_UNIT_SKU)
        or "ai_credit" in sku
        or "ai_unit" in sku
        or "ai_credit" in unit
        or "ai_unit" in unit
        or (product == COPILOT_PRODUCT and "credit" in unit)
        or (product == COPILOT_PRODUCT and "unit" in unit)
    )


def _is_legacy_premium_item(item: dict) -> bool:
    sku = _norm(item.get("sku"))
    unit = _norm(item.get("unitType") or item.get("unit_type"))
    return (
        sku == LEGACY_PREMIUM_REQUEST_SKU
        or "premium_request" in sku
        or "premium_request" in unit
    )


def _credit_quantity(item: dict, *, net: bool = False) -> float:
    if net:
        value = item.get("netQuantity")
        if value is not None:
            return float(value or 0)
        amount = item.get("netAmount")
        if amount is not None:
            return float(amount or 0) * 100.0
        return 0.0
    keys = ("grossQuantity", "quantity")
    for key in keys:
        value = item.get(key)
        if value is not None:
            return float(value or 0)
    amount = item.get("grossAmount")
    if amount is not None:
        return float(amount or 0) * 100.0
    return 0.0


def _item_quantity(item: dict) -> float:
    """Premium allowance usage is the gross consumed count.

    GitHub's billing report separates total consumed requests from net billable
    requests. Included Pro/Pro+ allowance is discounted down to $0, so netQuantity
    can be 0 even when the user has consumed most of their monthly allowance.
    """
    for key in ("grossQuantity", "quantity", "discountQuantity", "netQuantity"):
        value = item.get(key)
        if value is not None:
            return float(value or 0)
    return 0.0


def _net_quantity(item: dict) -> float:
    return float(item.get("netQuantity", 0) or 0)


def _format_quantity(value: float) -> str:
    return f"{value:.1f}" if value % 1 else f"{int(value)}"


def _build_snapshot(payload: dict, quota: int, *, unit: str = "auto") -> UsageSnapshot:
    items = payload.get("usageItems", []) or []
    if unit == "auto":
        unit = (
            "premium_requests"
            if any(_is_legacy_premium_item(item) for item in items)
            and not any(_is_credit_item(item) for item in items)
            else "credits"
        )
    if unit == "credits":
        usage_items = [item for item in items if _is_credit_item(item)]
        used = sum(_credit_quantity(item) for item in usage_items)
        billed = sum(_credit_quantity(item, net=True) for item in usage_items)
        label_unit = "Credits"
        billable_text = "AI credits"
        empty_note = (
            "No Copilot AI credit usage returned yet. GitHub usage reporting can "
            "lag, and org-billed usage may only appear at the billing organization."
        )
    else:
        usage_items = [item for item in items if _is_legacy_premium_item(item)] or items
        used = sum(_item_quantity(item) for item in usage_items)
        billed = sum(_net_quantity(item) for item in usage_items)
        label_unit = "Premium"
        billable_text = "premium requests"
        empty_note = (
            "No user-billed premium requests returned. If Copilot is billed "
            "through an organization or enterprise, GitHub omits it from this "
            "user-level endpoint."
        )
    percent = (used / quota * 100.0) if quota > 0 else None
    now_utc = datetime.now(timezone.utc)
    this_utc = _this_month_start_utc(now_utc)
    next_utc = _next_month_start_utc(now_utc)
    window = next_utc - this_utc

    metric = UsageMetric(
        label=f"{label_unit} ({_format_quantity(used)}/{quota})",
        percent_used=percent,
        resets_at=next_utc.astimezone().replace(tzinfo=None),
        note=(
            f"{billed:g} billable {billable_text} beyond included allowance."
            if usage_items
            else empty_note
        ),
        window=window,
    )
    return UsageSnapshot(
        provider="copilot",
        status=SnapshotStatus.OK,
        metrics=[metric],
        raw=payload,
    )


def _build_legacy_snapshot(payload: dict, quota: int) -> UsageSnapshot:
    return _build_snapshot(payload, quota, unit="premium_requests")


def _log_snapshot_decision(
    *,
    endpoint: str,
    quota: int,
    snapshot: UsageSnapshot,
) -> None:
    metric = snapshot.metrics[0] if snapshot.metrics else None
    log.info(
        "provider api diagnosis provider=copilot "
        "classification=snapshot endpoint=%s quota=%s label=%r percent=%s note=%r",
        endpoint,
        quota,
        metric.label if metric else None,
        metric.percent_used if metric else None,
        metric.note if metric else None,
    )


class CopilotProvider(Provider):
    name = "copilot"
    display_name = "Copilot"

    def __init__(self, config: Config, pool=None):
        self._config = config
        # QThreadPool import is deferred so unit tests can import this module
        # without PyQt6 installed.
        self._pool = pool

    def refresh(self, on_done: Callable[[UsageSnapshot], None]) -> None:
        pat = get_github_pat()
        if not pat:
            log.info(
                "provider api diagnosis provider=copilot classification=missing_pat"
            )
            on_done(
                UsageSnapshot(
                    provider="copilot",
                    status=SnapshotStatus.AUTH_REQUIRED,
                    error="GitHub PAT not set. Configure it in Settings.",
                )
            )
            return

        config = self._config

        def work() -> UsageSnapshot:
            username = _resolve_username(pat, config.copilot.username)
            if not username:
                log.info(
                    "provider api diagnosis provider=copilot "
                    "classification=username_unresolved username_configured=%s "
                    "billing_org_configured=%s",
                    bool(config.copilot.username),
                    bool(config.copilot.billing_org),
                )
                return UsageSnapshot(
                    provider="copilot",
                    status=SnapshotStatus.AUTH_REQUIRED,
                    error="Could not resolve GitHub username (PAT may lack read:user).",
                )
            billing_org = config.copilot.billing_org
            try:
                if billing_org:
                    payload = _fetch_org_credit_usage(pat, billing_org)
                else:
                    payload = _fetch_user_credit_usage(pat, username)
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                request_id = (
                    exc.response.headers.get("x-github-request-id", "")
                    if exc.response is not None
                    else ""
                )
                log.warning(
                    "provider api diagnosis provider=copilot "
                    "classification=http_error endpoint=credit_usage status=%s scope=%s "
                    "billing_org_configured=%s username_configured=%s request_id=%s",
                    status,
                    "org" if billing_org else "user",
                    bool(billing_org),
                    bool(config.copilot.username),
                    request_id,
                )
                if status in (400, 404):
                    try:
                        if billing_org:
                            legacy_payload = _fetch_org_premium_usage(
                                pat, billing_org, username
                            )
                        else:
                            legacy_payload = _fetch_user_premium_usage(pat, username)
                        snapshot = _build_legacy_snapshot(
                            legacy_payload, config.copilot.monthly_quota
                        )
                        _log_snapshot_decision(
                            endpoint="legacy_premium_usage_after_credit_error",
                            quota=config.copilot.monthly_quota,
                            snapshot=snapshot,
                        )
                        return snapshot
                    except requests.HTTPError:
                        log.info(
                            "provider api diagnosis provider=copilot "
                            "classification=legacy_usage_unavailable scope=%s",
                            "org" if billing_org else "user",
                        )
                if status in (401, 403):
                    detail = (
                        " Re-issue with organization Administration read permission "
                        "and billing access."
                        if billing_org
                        else " Re-issue with Plan read permission."
                    )
                    return UsageSnapshot(
                        provider="copilot",
                        status=SnapshotStatus.AUTH_REQUIRED,
                        error=f"GitHub rejected PAT ({status}).{detail}",
                    )
                return UsageSnapshot(
                    provider="copilot",
                    status=SnapshotStatus.ERROR,
                    error=f"GitHub API {status}",
                )
            if any(_is_credit_item(item) for item in payload.get("usageItems", []) or []):
                snapshot = _build_snapshot(payload, config.copilot.monthly_quota)
                _log_snapshot_decision(
                    endpoint="credit_usage",
                    quota=config.copilot.monthly_quota,
                    snapshot=snapshot,
                )
                return snapshot

            try:
                if billing_org:
                    legacy_payload = _fetch_org_premium_usage(
                        pat, billing_org, username
                    )
                else:
                    legacy_payload = _fetch_user_premium_usage(pat, username)
                if any(
                    _is_legacy_premium_item(item)
                    for item in legacy_payload.get("usageItems", []) or []
                ):
                    snapshot = _build_legacy_snapshot(
                        legacy_payload, config.copilot.monthly_quota
                    )
                    _log_snapshot_decision(
                        endpoint="legacy_premium_usage",
                        quota=config.copilot.monthly_quota,
                        snapshot=snapshot,
                    )
                    return snapshot
            except requests.HTTPError:
                log.info(
                    "provider api diagnosis provider=copilot "
                    "classification=legacy_usage_unavailable scope=%s",
                    "org" if billing_org else "user",
                )
            snapshot = _build_snapshot(payload, config.copilot.monthly_quota)
            _log_snapshot_decision(
                endpoint="credit_usage_empty",
                quota=config.copilot.monthly_quota,
                snapshot=snapshot,
            )
            return snapshot

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
                        "provider api diagnosis provider=copilot "
                        "classification=unexpected_exception type=%s",
                        type(exc).__name__,
                    )
                    snapshot = UsageSnapshot(
                        provider="copilot",
                        status=SnapshotStatus.ERROR,
                        error=str(exc),
                    )
                on_done(snapshot)

        pool = self._pool or QThreadPool.globalInstance()
        pool.start(_Worker())
