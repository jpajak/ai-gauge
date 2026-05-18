from __future__ import annotations

from typing import Any

from ..models import UsageMetric


SECURITY_VERIFICATION_MARKERS = (
    "verify you are human",
    "just a moment",
    "cloudflare",
)


def normalize_percent(percent: float | None, kind: str) -> float | None:
    """Convert provider-reported percentages to a uniform "percent used"."""
    if percent is None:
        return None
    if kind == "remaining":
        return max(0.0, 100.0 - percent)
    return percent


def page_text(payload: dict[str, Any]) -> str:
    return f"{payload.get('title', '')} {payload.get('body_text', '')}".lower()


def is_security_verification_page(payload: dict[str, Any]) -> bool:
    text = page_text(payload)
    return any(marker in text for marker in SECURITY_VERIFICATION_MARKERS)


def idle_session_weekly_metrics() -> list[UsageMetric]:
    """Two zero/idle metrics labelled Session and Weekly.

    Both Claude and Codex emit this shape when the account is signed in but
    has never hit a limit (so the page renders the panel without a
    countdown).
    """
    return [
        UsageMetric(
            "Session",
            0.0,
            None,
            "idle",
            "Countdown starts when you next use this limit.",
        ),
        UsageMetric(
            "Weekly",
            0.0,
            None,
            "idle",
            "Countdown starts when you next use this limit.",
        ),
    ]
