from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any


class SnapshotStatus(str, Enum):
    OK = "ok"
    AUTH_REQUIRED = "auth_required"
    ERROR = "error"


@dataclass
class UsageMetric:
    """A single percent-used reading with a reset time."""

    label: str
    percent_used: float | None = None
    resets_at: datetime | None = None
    reset_label: str | None = None
    note: str | None = None
    window: timedelta | None = None
    tag: str | None = None
    # True for a limit an agent should pause on; False for balances, which are
    # reported but never count toward a pause.
    guard_eligible: bool = True
    # Shown as the tile header's right-side text instead of as a row.
    in_header: bool = False


@dataclass
class UsageSnapshot:
    """Result returned by Provider.refresh() — one per provider per refresh cycle."""

    provider: str
    status: SnapshotStatus
    metrics: list[UsageMetric] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=datetime.now)
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
