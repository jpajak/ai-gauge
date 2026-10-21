from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from ..config import Config
from ..models import SnapshotStatus, UsageMetric, UsageSnapshot
from .base import Provider

# Each region's console usage page, for the Settings "Open usage" button.
MINIMAX_USAGE_URLS = {
    "international": "https://platform.minimax.io/console/usage",
    "china": "https://platform.minimax.cn/console/usage",
}
MINIMAX_USAGE_URL = MINIMAX_USAGE_URLS["international"]
# MiniMax serves each region from its own API host, and a key is only valid on
# its own region's host. The account picks the region (Settings), and the key is
# sent only to that region's host: it is never tried against another one.
MINIMAX_REGIONS = {
    "international": ("International", "https://api.minimax.io"),
    "china": ("China", "https://api.minimax.cn"),
}
_QUOTA_PATH = "/v1/token_plan/remains"

_LAYOUT_ERROR = (
    "Could not read MiniMax Coding Plan quota (response format may have changed)."
)

# base_resp.status_code values the API uses for the cases we handle.
_CODE_BAD_KEY = 1004
_CODE_WRONG_REGION = 2049
_CODE_NO_PLAN = 2062

# current_*_status values.
_STATUS_LIMITED = 1
_STATUS_EXHAUSTED = 2
_STATUS_UNLIMITED = 3

log = logging.getLogger("aigauge.providers.minimax")


class _ApiError(Exception):
    """MiniMax answered HTTP 200 with a non-zero base_resp.status_code."""

    def __init__(self, code: int, message: str):
        super().__init__(f"MiniMax API error {code}")
        self.code = code
        self.message = message


class _Redirected(Exception):
    """The quota endpoint answered with a 3xx. It is never followed, because
    the request carries the API key. Holds the status and the target's
    hostname only, never the full Location URL."""

    def __init__(self, status: int, location_host: str):
        super().__init__(f"MiniMax redirected the quota request (HTTP {status})")
        self.status = status
        self.location_host = location_host


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }


def _fetch_usage(host: str, api_key: str) -> dict[str, Any]:
    """GET the quota payload from the region's host, without following redirects.

    Raises _Redirected on a 3xx (raise_for_status does not raise on those),
    requests.HTTPError on a 4xx or 5xx status and ValueError on a body that is
    not a JSON object. Never puts the body into an exception message.
    """
    response = requests.get(
        f"{host}{_QUOTA_PATH}",
        headers=_headers(api_key),
        timeout=15,
        allow_redirects=False,
    )
    if 300 <= response.status_code < 400:
        location = response.headers.get("Location") or ""
        raise _Redirected(response.status_code, urlparse(location).hostname or "")
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError:
        raise ValueError("MiniMax returned an unreadable response.") from None
    if not isinstance(payload, dict):
        raise ValueError("MiniMax returned an unexpected response.")
    log.debug(
        "provider api diagnosis provider=minimax "
        "classification=usage_ok host=%s status=%s payload_keys=%s",
        host,
        response.status_code,
        sorted(payload),
    )
    return payload


def _check_base_resp(payload: dict[str, Any]) -> None:
    """Raise _ApiError when base_resp carries a non-zero status code."""
    base = payload.get("base_resp")
    if not isinstance(base, dict) or base.get("status_code") in (None, 0):
        return
    try:
        code = int(base["status_code"])
    except (TypeError, ValueError):
        raise ValueError("MiniMax returned an unexpected response.") from None
    if code == 0:
        return
    msg = base.get("status_msg")
    raise _ApiError(code, msg.strip()[:200] if isinstance(msg, str) else "")


def _number(value: object) -> float | None:
    """A finite float from a JSON number or numeric string, else None."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _status(row: dict[str, Any], key: str) -> int | None:
    number = _number(row.get(key))
    return int(number) if number is not None else None


# Below this an epoch value is seconds (1e12 ms is September 2001; 1e12 s is
# year 33658), above it milliseconds. The API documents milliseconds; seconds
# are accepted as openclaw's MiniMax fetcher does.
_SECONDS_BELOW = 1e12


def _epoch_ms_to_local(value: object) -> datetime | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    seconds = number if number < _SECONDS_BELOW else number / 1000
    try:
        # fromtimestamp returns local, timezone-naive time, which is the model
        # the rest of AI Gauge uses (see opencode_go._parse_reset_at).
        return datetime.fromtimestamp(seconds)
    except (OverflowError, OSError, ValueError):
        return None


def _percent_used(row: dict[str, Any], prefix: str) -> float | None:
    """Percent used for one window, or None when nothing readable is present.

    The *_usage_count fields hold the REMAINING count despite their name (the
    official CLI passes them as the `remaining` argument of remainingPct), so
    the fallback is 100 - usage_count / total * 100.
    """
    remaining = _number(row.get(f"{prefix}_remaining_percent"))
    if remaining is None:
        count = _number(row.get(f"{prefix}_usage_count"))
        total = _number(row.get(f"{prefix}_total_count"))
        if count is None or total is None or total <= 0:
            return None
        remaining = count / total * 100.0
    return max(0.0, min(100.0, 100.0 - remaining))


def _is_unavailable_model(row: dict[str, Any]) -> bool:
    # Both windows unlimited with zero totals is how the API reports a model the
    # plan does not include (official CLI issue 173), not a real unlimited plan.
    return (
        _status(row, "current_interval_status") == _STATUS_UNLIMITED
        and _status(row, "current_weekly_status") == _STATUS_UNLIMITED
        and _number(row.get("current_interval_total_count")) == 0
        and _number(row.get("current_weekly_total_count")) == 0
    )


def _is_preferred_name(row: dict[str, Any]) -> bool:
    name = row.get("model_name")
    if not isinstance(name, str):
        return False
    name = name.strip().lower()
    return name == "general" or name.startswith("minimax-m")


def _select_row(payload: dict[str, Any]) -> dict[str, Any] | None:
    remains = payload.get("model_remains")
    if not isinstance(remains, list):
        return None
    rows = [
        row for row in remains if isinstance(row, dict) and not _is_unavailable_model(row)
    ]
    for row in rows:
        if _is_preferred_name(row):
            return row
    for row in rows:
        if {
            _status(row, "current_interval_status"),
            _status(row, "current_weekly_status"),
        } & {_STATUS_LIMITED, _STATUS_EXHAUSTED}:
            return row
    return rows[0] if rows else None


def _window_metric(
    row: dict[str, Any],
    *,
    label: str,
    window: timedelta,
    prefix: str,
    status_key: str,
    end_key: str,
) -> UsageMetric | None:
    """One metric, or None when the window has no readable percent or count."""
    status = _status(row, status_key)
    if status == _STATUS_UNLIMITED:
        return UsageMetric(
            label=label,
            percent_used=None,
            resets_at=None,
            reset_label="Unlimited",
            window=window,
        )
    if status == _STATUS_EXHAUSTED:
        percent: float | None = 100.0
        note: str | None = "Limit reached."
    else:
        percent = _percent_used(row, prefix)
        note = None
    if percent is None:
        return None
    return UsageMetric(
        label=label,
        percent_used=percent,
        resets_at=_epoch_ms_to_local(row.get(end_key)),
        window=window,
        note=note,
    )


def _build_snapshot(
    payload: dict[str, Any],
    *,
    account_id: str = "minimax",
    host: str | None = None,
) -> UsageSnapshot:
    row = _select_row(payload)
    if row is None:
        raise ValueError(_LAYOUT_ERROR)

    five_hour = _window_metric(
        row,
        label="5h",
        window=timedelta(hours=5),
        prefix="current_interval",
        status_key="current_interval_status",
        end_key="end_time",
    )
    if five_hour is None:
        raise ValueError(_LAYOUT_ERROR)
    metrics = [five_hour]

    weekly = _window_metric(
        row,
        label="Weekly",
        window=timedelta(days=7),
        prefix="current_weekly",
        status_key="current_weekly_status",
        end_key="weekly_end_time",
    )
    if weekly is not None:
        boost = _number(row.get("weekly_boost_permille"))
        if boost is not None and boost > 1000 and weekly.percent_used is not None:
            # The console shows remaining against the base allowance, so it
            # can pass 100%; give its figure so the two can be reconciled.
            console_left = (100 - weekly.percent_used) * boost / 1000
            boosted = (
                f"Weekly allowance boosted to {boost / 10:g}% "
                f"(console: {console_left:.0f}% left)."
            )
            weekly.note = f"{weekly.note} {boosted}" if weekly.note else boosted
        metrics.append(weekly)

    model_name = row.get("model_name")
    return UsageSnapshot(
        provider=account_id,
        status=SnapshotStatus.OK,
        metrics=metrics,
        raw={
            "host": host,
            "model_name": model_name if isinstance(model_name, str) else None,
        },
    )


class MiniMaxProvider(Provider):
    name = "minimax"
    display_name = "MiniMax"

    def __init__(
        self,
        config: Config,
        parent=None,
        account_id: str = "minimax",
        pool=None,
        key_loader: Callable[[str], str | None] | None = None,
        region: str = "international",
    ):
        # Keep config and parent in the signature for compatibility with the
        # other account providers and existing app construction.
        self._config = config
        self._account_id = account_id
        self._pool = pool
        self._key_loader = key_loader
        self._region = region
        self._host_logged = False

    def _load_key(self) -> str | None:
        if self._key_loader is not None:
            return self._key_loader(self._account_id)
        # Imported here so tests, which always inject key_loader, never touch
        # the system keychain.
        from ..config import get_account_api_key

        return get_account_api_key("minimax", self._account_id)

    def _error(self, status: SnapshotStatus, message: str) -> UsageSnapshot:
        return UsageSnapshot(
            provider=self._account_id,
            status=status,
            error=message,
        )

    def refresh(self, on_done: Callable[[UsageSnapshot], None]) -> None:
        region = MINIMAX_REGIONS.get(self._region)
        if region is None:
            # Checked before the key is loaded: nothing is sent anywhere.
            on_done(
                self._error(
                    SnapshotStatus.ERROR, "Choose a MiniMax region in Settings."
                )
            )
            return
        label, base_url = region
        host_name = urlparse(base_url).hostname or base_url
        where = f"{host_name} ({label})"

        api_key = self._load_key()
        if not api_key:
            on_done(
                self._error(
                    SnapshotStatus.AUTH_REQUIRED,
                    "Add a MiniMax Coding Plan API key in Settings.",
                )
            )
            return

        def work() -> UsageSnapshot:
            try:
                payload = _fetch_usage(base_url, api_key)
                _check_base_resp(payload)
                if not self._host_logged:
                    # Once per provider instance: which region's host answered.
                    self._host_logged = True
                    log.info(
                        "provider api host provider=%s region=%s host=%s",
                        self._account_id,
                        self._region,
                        host_name,
                    )
                return _build_snapshot(
                    payload,
                    account_id=self._account_id,
                    host=base_url,
                )
            except _Redirected as exc:
                log.warning(
                    "provider api diagnosis provider=%s "
                    "classification=redirect status=%s location_host=%s",
                    self._account_id,
                    exc.status,
                    exc.location_host,
                )
                return self._error(
                    SnapshotStatus.ERROR,
                    f"{host_name} redirected the quota request "
                    f"(HTTP {exc.status}); AI Gauge does not follow redirects "
                    "with an API key.",
                )
            except _ApiError as exc:
                log.warning(
                    "provider api diagnosis provider=%s "
                    "classification=base_resp_error code=%s host=%s",
                    self._account_id,
                    exc.code,
                    host_name,
                )
                if exc.code == _CODE_BAD_KEY:
                    return self._error(
                        SnapshotStatus.AUTH_REQUIRED,
                        f"MiniMax rejected the API key at {where}. "
                        "Check the key and the region in Settings.",
                    )
                if exc.code == _CODE_WRONG_REGION:
                    return self._error(
                        SnapshotStatus.ERROR,
                        f"This key does not belong to the {label} region "
                        f"({host_name}). Check the region in Settings.",
                    )
                if exc.code == _CODE_NO_PLAN:
                    return self._error(
                        SnapshotStatus.ERROR,
                        "This key has no active MiniMax Coding Plan.",
                    )
                detail = f": {exc.message}" if exc.message else ""
                return self._error(
                    SnapshotStatus.ERROR,
                    f"MiniMax API error {exc.code}{detail}",
                )
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                log.warning(
                    "provider api diagnosis provider=%s "
                    "classification=usage_http_error status=%s host=%s",
                    self._account_id,
                    status,
                    host_name,
                )
                if status == 401:
                    return self._error(
                        SnapshotStatus.AUTH_REQUIRED,
                        f"MiniMax rejected the API key at {where}. "
                        "Check the key and the region in Settings.",
                    )
                if status == 403:
                    return self._error(
                        SnapshotStatus.ERROR,
                        f"MiniMax refused the key at {where}. Check the "
                        "region in Settings and that the key has an active "
                        "Coding Plan.",
                    )
                if status == 404:
                    return self._error(
                        SnapshotStatus.ERROR,
                        f"This key does not belong to the {label} region "
                        f"({host_name}). Check the region in Settings.",
                    )
                return self._error(
                    SnapshotStatus.ERROR,
                    f"MiniMax usage API returned HTTP {status}.",
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                log.warning(
                    "provider api diagnosis provider=%s "
                    "classification=unreachable host=%s error=%s",
                    self._account_id,
                    host_name,
                    type(exc).__name__,
                )
                return self._error(
                    SnapshotStatus.ERROR,
                    f"Could not reach {where}. Check the connection, or the "
                    "region in Settings.",
                )
            except requests.RequestException as exc:
                # Request errors carry the URL, never the Authorization header.
                return self._error(
                    SnapshotStatus.ERROR,
                    f"MiniMax usage request failed: {exc}",
                )
            except (TypeError, ValueError) as exc:
                log.warning(
                    "provider api diagnosis provider=%s "
                    "classification=unexpected_response error=%s",
                    self._account_id,
                    exc,
                )
                return self._error(SnapshotStatus.ERROR, str(exc))

        self._run_async(work, on_done)
