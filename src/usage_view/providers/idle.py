from __future__ import annotations

from datetime import datetime, timedelta


def idle_reset_state(
    *,
    percent: float | None,
    resets_at: datetime | None,
    window: timedelta,
) -> tuple[datetime | None, str | None, str | None]:
    """Hide reset countdowns for unused windows that have not started yet."""
    if percent != 0 or resets_at is None:
        return resets_at, None, None
    if resets_at - datetime.now() <= window:
        return resets_at, None, None
    return None, "idle", "Countdown starts when you next use this limit."
