from __future__ import annotations

import argparse
from datetime import datetime
from io import TextIOWrapper
import json
import math
import os
import sys
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError as exc:
    if exc.name != "mcp" and not (exc.name or "").startswith("mcp."):
        raise
    FastMCP = None

from .config import Config, browser_accounts, display_name_for_account
from .providers.registry import single_specs
from .usage_cache import CACHE_SCHEMA_VERSION, read_usage_cache

INSTRUCTIONS = """
AI Gauge provides local subscription usage and user-configured pause policies.
Before starting costly AI work, call check_current_account_usage. If allowed is
false, stop and tell the user why. This is a cooperative guard: the MCP protocol
cannot suspend a client that ignores the result.
""".strip()


class _UnavailableMCP:
    """Keep pure guard functions importable without the optional MCP SDK."""

    @staticmethod
    def tool():
        return lambda function: function

    @staticmethod
    def resource(_uri: str):
        return lambda function: function


mcp = (
    FastMCP("AI Gauge", instructions=INSTRUCTIONS, json_response=True)
    if FastMCP is not None
    else _UnavailableMCP()
)
_bound_account_id: str | None = None


def _configured_account_ids(config: Config) -> set[str]:
    account_ids = {
        account.id
        for account in browser_accounts(config, enabled_only=True)
        if getattr(config.providers, account.kind, False)
    }
    account_ids.update(
        spec.id
        for spec in single_specs()
        if getattr(config.providers, spec.id, False)
    )
    return account_ids


def _snapshot_age_seconds(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        fetched_at = datetime.fromisoformat(value)
    except ValueError:
        return None
    now = datetime.now(fetched_at.tzinfo) if fetched_at.tzinfo else datetime.now()
    age = (now - fetched_at).total_seconds()
    if age < -5:
        return None
    return max(0.0, age)


def _valid_percent(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    percent = float(value)
    if percent < 0 or not math.isfinite(percent):
        return None
    return percent


def _cached_accounts(cache: dict[str, Any]) -> dict[str, Any]:
    cached = cache.get("accounts", {})
    return cached if isinstance(cached, dict) else {}


def _account_row(
    config: Config,
    cache: dict[str, Any],
    account_id: str,
) -> dict[str, Any]:
    """Build one sanitized row, whether or not the cache holds that account.

    Always returns a row. An account the cache has never seen still gets one,
    reporting an unknown status and no freshness — which the guard rejects. That
    keeps "absent from the cache" a fail-closed input rather than a separate
    missing-row case callers would have to remember to handle.
    """
    snapshot = _cached_accounts(cache).get(account_id, {})
    if not isinstance(snapshot, dict):
        snapshot = {}
    metrics = snapshot.get("metrics", [])
    if not isinstance(metrics, list):
        metrics = []
    eligible = []
    for metric in metrics:
        if not isinstance(metric, dict) or metric.get("guard_eligible") is not True:
            continue
        percent = _valid_percent(metric.get("percent_used"))
        if percent is not None:
            eligible.append(percent)
    age_seconds = _snapshot_age_seconds(snapshot.get("fetched_at"))
    max_age_seconds = (config.refresh_interval_minutes + 5) * 60
    try:
        display_name = display_name_for_account(config, account_id)
    except Exception:
        display_name = account_id
    return {
        "account_id": account_id,
        "display_name": display_name,
        "status": snapshot.get("status", "unknown"),
        "fetched_at": snapshot.get("fetched_at"),
        "data_age_seconds": age_seconds,
        "fresh": age_seconds is not None and age_seconds <= max_age_seconds,
        "max_percent_used": max(eligible, default=None),
        "metrics": metrics,
        "pause_at_percent": config.mcp_pause_policies.get(account_id),
    }


def _account_rows(config: Config, cache: dict[str, Any]) -> list[dict[str, Any]]:
    cached = _cached_accounts(cache)
    configured_ids = _configured_account_ids(config)
    account_ids = [
        account_id for account_id in cached if account_id in configured_ids
    ]
    for account_id in config.mcp_pause_policies:
        if account_id in configured_ids and account_id not in account_ids:
            account_ids.append(account_id)
    return [
        _account_row(config, cache, account_id) for account_id in account_ids
    ]


def _cache_unavailable_reason(cache: dict[str, Any]) -> str | None:
    """Explain why the cache could not be read, or None when it was published.

    Only an explicit ``available: False`` counts as unavailable, so a cache
    written by a build that predates the flag still reads as a real one.
    """
    if cache.get("available") is not False:
        return None
    message = cache.get("message")
    if isinstance(message, str) and message.strip():
        return message
    return "AI Gauge has not published usage data."


def _blocked(
    reason_code: str,
    reason: str,
    *,
    account_id: str | None = None,
    account: dict[str, Any] | None = None,
    guard_configured: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "allowed": False,
        "guard_configured": guard_configured,
        "reason_code": reason_code,
        "reason": reason,
    }
    if account is not None:
        result["account"] = account
    else:
        result["account_id"] = account_id
    return result


def _check_usage_guard(
    config: Config,
    cache: dict[str, Any],
    account_id: str,
) -> dict[str, Any]:
    if not config.mcp_enabled:
        return _blocked(
            "integration_disabled",
            "MCP integration is disabled in AI Gauge Settings.",
            account_id=account_id,
        )
    if account_id not in _configured_account_ids(config):
        return _blocked(
            "account_unavailable",
            "This account is not currently configured and enabled in AI Gauge.",
            account_id=account_id,
        )

    threshold = config.mcp_pause_policies.get(account_id)
    if threshold is None:
        return {
            "allowed": True,
            "guard_configured": False,
            "reason_code": "no_policy",
            "account_id": account_id,
            "reason": "No MCP pause policy is configured for this account.",
        }

    unavailable = _cache_unavailable_reason(cache)
    if unavailable is not None:
        return _blocked(
            "cache_unavailable",
            f"{unavailable} The configured guard fails closed.",
            account_id=account_id,
            guard_configured=True,
        )
    if cache.get("schema_version") != CACHE_SCHEMA_VERSION:
        return _blocked(
            "unsupported_cache_schema",
            "AI Gauge usage cache is incompatible with this helper.",
            account_id=account_id,
            guard_configured=True,
        )
    row = _account_row(config, cache, account_id)
    if row["status"] != "ok":
        return _blocked(
            "snapshot_not_ok",
            "The latest usage refresh is not OK; the configured guard fails closed.",
            account=row,
            guard_configured=True,
        )
    if row["data_age_seconds"] is None:
        return _blocked(
            "invalid_timestamp",
            "Usage freshness could not be verified; the configured guard fails closed.",
            account=row,
            guard_configured=True,
        )
    if not row["fresh"]:
        return _blocked(
            "snapshot_stale",
            "Usage data is stale; the configured guard fails closed.",
            account=row,
            guard_configured=True,
        )
    percent = row["max_percent_used"]
    if percent is None:
        return _blocked(
            "no_eligible_metric",
            "No valid limit percentage is available; the configured guard fails closed.",
            account=row,
            guard_configured=True,
        )

    allowed = percent < threshold
    return {
        "allowed": allowed,
        "guard_configured": True,
        "reason_code": (
            "allowed_below_threshold" if allowed else "threshold_reached"
        ),
        "account": row,
        "reason": (
            f"Usage {percent:g}% is below the {threshold}% pause threshold."
            if allowed
            else f"Usage {percent:g}% reached the {threshold}% pause threshold."
        ),
    }


@mcp.tool()
def get_ai_usage(account_id: str | None = None) -> dict[str, Any]:
    """Return sanitized AI Gauge usage for configured accounts."""
    config = Config.load()
    if not config.mcp_enabled:
        return {
            "integration_enabled": False,
            "reason_code": "integration_disabled",
            "accounts": [],
        }
    cache = read_usage_cache()
    unavailable = _cache_unavailable_reason(cache)
    if unavailable is not None:
        return {
            "integration_enabled": True,
            "reason_code": "cache_unavailable",
            "reason": unavailable,
            "accounts": [],
        }
    if cache.get("schema_version") != CACHE_SCHEMA_VERSION:
        return {
            "integration_enabled": True,
            "reason_code": "unsupported_cache_schema",
            "reason": "AI Gauge usage cache is incompatible with this helper.",
            "accounts": [],
        }
    rows = _account_rows(config, cache)
    if account_id is not None:
        rows = [row for row in rows if row["account_id"] == account_id]
    return {"integration_enabled": True, "accounts": rows}


@mcp.tool()
def check_usage_guard(account_id: str) -> dict[str, Any]:
    """Check whether configured usage policy allows more work on an account."""
    config = Config.load()
    cache = read_usage_cache() if config.mcp_enabled else {}
    return _check_usage_guard(config, cache, account_id)


@mcp.tool()
def check_current_account_usage() -> dict[str, Any]:
    """Check the account explicitly bound to this MCP client profile."""
    if _bound_account_id is None:
        return _blocked(
            "no_binding",
            (
                "This MCP connection is not bound to an AI Gauge account. "
                "Launch it with --account-id <account-id>."
            ),
        )
    return check_usage_guard(_bound_account_id)


@mcp.tool()
def recommend_ai_account() -> dict[str, Any]:
    """Recommend an allowed account with the most configured policy headroom."""
    config = Config.load()
    if not config.mcp_enabled:
        return {
            "account": None,
            "reason_code": "integration_disabled",
            "reason": "MCP integration is disabled in AI Gauge Settings.",
        }
    cache = read_usage_cache()
    candidates = []
    for account_id in config.mcp_pause_policies:
        result = _check_usage_guard(config, cache, account_id)
        if not result["allowed"] or not result["guard_configured"]:
            continue
        row = result["account"]
        headroom = row["pause_at_percent"] - row["max_percent_used"]
        if headroom > 0:
            candidates.append((headroom, row))
    if not candidates:
        return {
            "account": None,
            "reason_code": "no_allowed_account",
            "reason": "No configured account currently has verified policy headroom.",
        }
    headroom, row = max(candidates, key=lambda item: item[0])
    return {
        "account": row,
        "headroom_percent": headroom,
        "reason_code": "recommended",
    }


@mcp.resource("aigauge://usage")
def usage_resource() -> str:
    """Current sanitized usage snapshot as JSON."""
    return json.dumps(get_ai_usage(), indent=2)


@mcp.resource("aigauge://guard-instructions")
def guard_instructions_resource() -> str:
    """Instructions clients should follow to honor pause policies."""
    return INSTRUCTIONS


def _run_stdio_with_owned_streams() -> None:
    """Run MCP without allowing the SDK to close the process's real stdio.

    The MCP SDK wraps ``sys.stdin.buffer`` and ``sys.stdout.buffer`` in new text
    streams. Those wrappers close the underlying process streams at EOF, which
    makes frozen executables emit a shutdown traceback when their bootloader
    later flushes stdout. Give the SDK duplicate handles and restore the real
    streams after it returns.
    """
    original_stdin = sys.stdin
    original_stdout = sys.stdout
    duplicate_stdin: TextIOWrapper | None = None
    duplicate_stdout: TextIOWrapper | None = None
    try:
        duplicate_stdin = TextIOWrapper(
            os.fdopen(os.dup(original_stdin.fileno()), "rb"),
            encoding="utf-8",
            errors="replace",
        )
        duplicate_stdout = TextIOWrapper(
            os.fdopen(os.dup(original_stdout.fileno()), "wb"),
            encoding="utf-8",
        )
        sys.stdin = duplicate_stdin
        sys.stdout = duplicate_stdout
        mcp.run(transport="stdio")
    finally:
        sys.stdin = original_stdin
        sys.stdout = original_stdout
        for stream in (duplicate_stdin, duplicate_stdout):
            if stream is not None:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass


def main() -> None:
    global _bound_account_id
    parser = argparse.ArgumentParser(description="AI Gauge MCP usage server")
    parser.add_argument(
        "--account-id",
        help="AI Gauge account used by this MCP client/profile.",
    )
    args = parser.parse_args()
    if FastMCP is None:
        print(
            "The MCP SDK is not installed. Install AI Gauge with: "
            "pip install 'ai-gauge[mcp]'",
            file=sys.stderr,
        )
        raise SystemExit(2)
    _bound_account_id = args.account_id
    _run_stdio_with_owned_streams()


if __name__ == "__main__":
    main()
