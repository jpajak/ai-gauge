from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Any, Callable

import requests

from ..config import Config
from ..models import SnapshotStatus, UsageMetric, UsageSnapshot
from .base import Provider

MINIMAX_USAGE_URL = "https://platform.minimax.io/console/usage"
MINIMAX_API_HOSTS = (
    "https://api.minimax.io",
    "https://api.minimax.cn",
    "https://api.minimaxi.com",
)
_QUOTA_PATH = "/v1/token_plan/remains"

_LAYOUT_ERROR = (
    "Could not read MiniMax Coding Plan quota (response format may have changed)."
)

# base_resp.status_code values the API uses for the cases we handle.
_CODE_BAD_KEY = 1004
_CODE_WRONG_HOST = 2049
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


class _HostsExhausted(Exception):
    def __init__(self, last_host: str):
        super().__init__(last_host)
        self.last_host = last_host


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }


def _fetch_usage(host: str, api_key: str) -> dict[str, Any]:
    """GET the quota payload from one host.

    Raises requests.HTTPError on a non-2xx status and ValueError on a body that
    is not a JSON object. Never puts the body into an exception message.
    """
    response = requests.get(
        f"{host}{_QUOTA_PATH}",
        headers=_headers(api_key),
        timeout=15,
    )
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
            boosted = f"Weekly allowance boosted to {boost / 10:g}%."
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
    ):
        # Keep config and parent in the signature for compatibility with the
        # other account providers and existing app construction.
        self._config = config
        self._account_id = account_id
        self._pool = pool
        self._key_loader = key_loader
        self._host: str | None = None

    def _load_key(self) -> str | None:
        if self._key_loader is not None:
            return self._key_loader(self._account_id)
        # Imported here so tests, which always inject key_loader, never touch
        # the system keychain.
        from ..config import get_account_api_key

        return get_account_api_key("minimax", self._account_id)

    def _hosts(self) -> list[str]:
        """Remembered host first, then the rest in their canonical order."""
        if self._host in MINIMAX_API_HOSTS:
            return [self._host] + [h for h in MINIMAX_API_HOSTS if h != self._host]
        return list(MINIMAX_API_HOSTS)

    def _fetch_any_host(self, api_key: str) -> tuple[str, dict[str, Any]]:
        """Try each host until one answers with a quota payload.

        A host's outcome is one of:
        - success: remember the host and return.
        - wrong host (HTTP 404, base_resp 2049), rejected key (HTTP 401, 403,
          base_resp 1004) or unreachable (connection error, timeout): try the
          next host, because a key is only valid on its own region's host and
          the first host may simply be unreachable from here.
        - anything else (base_resp 2062, another base_resp code, another HTTP
          status): raise at once, since more hosts will not change the answer.

        When every host fails with a move-on outcome, raise the most specific:
        the first rejected-key error, else _HostsExhausted (wrong host), else
        the first connection or timeout error.
        """
        last_host = MINIMAX_API_HOSTS[0]
        rejected: Exception | None = None
        wrong_host = False
        unreachable: Exception | None = None
        for host in self._hosts():
            last_host = host
            try:
                payload = _fetch_usage(host, api_key)
                _check_base_resp(payload)
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                if status == 404:
                    self._log_host(host, "host_not_found")
                    wrong_host = True
                    continue
                if status in (401, 403):
                    self._log_host(host, "rejected_key", f"status={status}")
                    rejected = rejected or exc
                    continue
                raise
            except _ApiError as exc:
                if exc.code == _CODE_WRONG_HOST:
                    self._log_host(host, "wrong_host")
                    wrong_host = True
                    continue
                if exc.code == _CODE_BAD_KEY:
                    self._log_host(host, "rejected_key", f"code={exc.code}")
                    rejected = rejected or exc
                    continue
                raise
            except (requests.ConnectionError, requests.Timeout) as exc:
                self._log_host(host, "unreachable", f"error={type(exc).__name__}")
                unreachable = unreachable or exc
                continue
            self._host = host
            return host, payload
        if rejected is not None:
            raise rejected
        if wrong_host:
            raise _HostsExhausted(last_host)
        assert unreachable is not None
        raise unreachable

    def _log_host(self, host: str, classification: str, extra: str = "") -> None:
        log.info(
            "provider api diagnosis provider=%s classification=%s host=%s%s",
            self._account_id,
            classification,
            host,
            f" {extra}" if extra else "",
        )

    def _error(self, status: SnapshotStatus, message: str) -> UsageSnapshot:
        return UsageSnapshot(
            provider=self._account_id,
            status=status,
            error=message,
        )

    def refresh(self, on_done: Callable[[UsageSnapshot], None]) -> None:
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
                host, payload = self._fetch_any_host(api_key)
                return _build_snapshot(
                    payload,
                    account_id=self._account_id,
                    host=host,
                )
            except _HostsExhausted as exc:
                log.warning(
                    "provider api diagnosis provider=%s "
                    "classification=hosts_exhausted last_host=%s",
                    self._account_id,
                    exc.last_host,
                )
                return self._error(
                    SnapshotStatus.ERROR,
                    "No MiniMax quota endpoint accepted this key "
                    f"(last host tried: {exc.last_host}).",
                )
            except _ApiError as exc:
                log.warning(
                    "provider api diagnosis provider=%s "
                    "classification=base_resp_error code=%s",
                    self._account_id,
                    exc.code,
                )
                if exc.code == _CODE_BAD_KEY:
                    return self._error(
                        SnapshotStatus.AUTH_REQUIRED,
                        "MiniMax rejected the API key on every regional host. "
                        "Update it in Settings.",
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
                    "classification=usage_http_error status=%s",
                    self._account_id,
                    status,
                )
                if status == 401:
                    return self._error(
                        SnapshotStatus.AUTH_REQUIRED,
                        "MiniMax rejected the API key on every regional host. "
                        "Update it in Settings.",
                    )
                if status == 403:
                    return self._error(
                        SnapshotStatus.ERROR,
                        "This API key does not have an active MiniMax Coding Plan.",
                    )
                return self._error(
                    SnapshotStatus.ERROR,
                    f"MiniMax usage API returned HTTP {status}.",
                )
            except requests.RequestException as exc:
                # Connection errors carry the URL, never the Authorization header.
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
