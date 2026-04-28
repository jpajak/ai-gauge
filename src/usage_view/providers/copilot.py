from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable

import requests

from ..config import Config, get_github_pat
from ..models import SnapshotStatus, UsageMetric, UsageSnapshot
from .base import Provider

GITHUB_API = "https://api.github.com"
GITHUB_API_VERSION = "2026-03-10"
log = logging.getLogger("usage_view.providers.copilot")


def _github_headers(pat: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }


def _usage_params(username: str | None = None) -> dict[str, int | str]:
    now = datetime.now()
    params: dict[str, int | str] = {"year": now.year, "month": now.month}
    if username:
        params["user"] = username
    return params


def _next_month_start(now: datetime) -> datetime:
    """First day of next calendar month — Copilot premium requests reset monthly."""
    if now.month == 12:
        return datetime(now.year + 1, 1, 1)
    return datetime(now.year, now.month + 1, 1)


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
    return r.json()


def _fetch_org_premium_usage(pat: str, org: str, username: str) -> dict:
    r = requests.get(
        f"{GITHUB_API}/organizations/{org}/settings/billing/premium_request/usage",
        params=_usage_params(username),
        headers=_github_headers(pat),
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _fetch_premium_usage(pat: str, username: str) -> dict:
    """Backward-compatible alias for tests and callers."""
    return _fetch_user_premium_usage(pat, username)


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


def _build_snapshot(payload: dict, quota: int) -> UsageSnapshot:
    items = payload.get("usageItems", []) or []
    used = sum(_item_quantity(item) for item in items)
    billed = sum(_net_quantity(item) for item in items)
    percent = (used / quota * 100.0) if quota > 0 else None

    metric = UsageMetric(
        label=f"Premium ({int(used)}/{quota})",
        percent_used=percent,
        resets_at=_next_month_start(datetime.now()),
        note=(
            f"{billed:g} billable premium requests beyond included allowance."
            if items
            else "No user-billed premium requests returned. If Copilot is billed "
            "through an organization or enterprise, GitHub omits it from this "
            "user-level endpoint."
        ),
    )
    return UsageSnapshot(
        provider="copilot",
        status=SnapshotStatus.OK,
        metrics=[metric],
        raw=payload,
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
                    payload = _fetch_org_premium_usage(pat, billing_org, username)
                else:
                    payload = _fetch_user_premium_usage(pat, username)
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                request_id = (
                    exc.response.headers.get("x-github-request-id", "")
                    if exc.response is not None
                    else ""
                )
                log.warning(
                    "provider api diagnosis provider=copilot "
                    "classification=http_error status=%s scope=%s "
                    "billing_org_configured=%s username_configured=%s request_id=%s",
                    status,
                    "org" if billing_org else "user",
                    bool(billing_org),
                    bool(config.copilot.username),
                    request_id,
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
            return _build_snapshot(payload, config.copilot.monthly_quota)

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
