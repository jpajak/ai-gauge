from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import time

from .config import app_data_dir
from .models import UsageSnapshot


CACHE_SCHEMA_VERSION = 1
_MAX_CACHE_BYTES = 1024 * 1024


def usage_cache_path() -> Path:
    return app_data_dir() / "mcp-usage.json"


def _empty_cache(message: str) -> dict:
    """Stand-in for a cache AI Gauge never published or could not read.

    ``available`` distinguishes this from a real publication with no accounts.
    Without it a reader cannot tell "AI Gauge is not running" from "the last
    refresh failed", because both arrive as an empty account map carrying the
    current schema version.
    """
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "published_at": None,
        "available": False,
        "accounts": {},
        "message": message,
    }


def write_usage_cache(snapshots: dict[str, UsageSnapshot]) -> None:
    """Publish the minimal sanitized usage projection used by the MCP guard."""
    # Timestamps carry an explicit UTC offset. Snapshots are stamped in naive
    # local time, so around a DST fall-back a reading taken before the shift
    # otherwise reads as up to an hour in the future and the guard blocks until
    # the wall clock catches up. astimezone() pins the offset in effect when the
    # reading was taken, keeping the comparison monotonic across the change.
    payload: dict[str, object] = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "published_at": datetime.now().astimezone().isoformat(),
        "available": True,
        "accounts": {},
    }
    accounts = payload["accounts"]
    assert isinstance(accounts, dict)
    for account_id, snapshot in snapshots.items():
        accounts[account_id] = {
            "status": snapshot.status.value,
            "fetched_at": snapshot.fetched_at.astimezone().isoformat(),
            "metrics": [
                {
                    "label": metric.label,
                    "percent_used": metric.percent_used,
                    "resets_at": (
                        metric.resets_at.isoformat() if metric.resets_at else None
                    ),
                    "reset_label": metric.reset_label,
                    # Tagged rows are presentation breakdowns, not limit
                    # gauges, and balance rows opt out with guard_eligible.
                    "guard_eligible": metric.tag is None and metric.guard_eligible,
                }
                for metric in snapshot.metrics
            ],
        }
    path = usage_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # A fixed .tmp name lets overlapping writers clobber one another. On
    # Windows, readers may also briefly hold the destination without delete
    # sharing, making os.replace raise WinError 32. Give every publication its
    # own closed temporary file and retry that short sharing window.
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            json.dump(payload, temporary, indent=2, allow_nan=False)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        for attempt in range(6):
            try:
                os.replace(temporary_path, path)
                temporary_path = None
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.02 * (attempt + 1))
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def invalidate_usage_cache() -> None:
    path = usage_cache_path()
    for attempt in range(6):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.02 * (attempt + 1))


def read_usage_cache() -> dict:
    path = usage_cache_path()
    if not path.exists():
        return _empty_cache("AI Gauge has not published MCP usage.")
    try:
        if path.stat().st_size > _MAX_CACHE_BYTES:
            return _empty_cache("AI Gauge MCP usage cache is too large.")
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else _empty_cache(
            "AI Gauge MCP usage cache is invalid."
        )
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        return _empty_cache("AI Gauge MCP usage cache is unavailable.")
