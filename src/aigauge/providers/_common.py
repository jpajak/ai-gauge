from __future__ import annotations

from typing import Any

from ..models import UsageMetric


SECURITY_VERIFICATION_STRONG_MARKERS = (
    "verify you are human",
    "verifying you are human",
    "security verification",
    "checking if the site connection is secure",
    "needs to review the security of your connection",
    "enable javascript and cookies to continue",
    "performance & security by cloudflare",
)

SECURITY_VERIFICATION_SOFT_MARKERS = (
    "cloudflare",
)

SECURITY_VERIFICATION_TITLE_MARKERS = (
    "just a moment",
)

USAGE_PAGE_MARKERS = (
    "plan usage limits",
    "current session",
    "all models",
    "5 hour usage limit",
    "weekly usage limit",
    "personal usage",
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


def _has_parsed_usage_rows(payload: dict[str, Any]) -> bool:
    for value in payload.values():
        if not isinstance(value, dict):
            continue
        if any(key in value for key in ("percent", "kind", "reset_text")):
            return True
    return False


def has_usage_page_signal(payload: dict[str, Any]) -> bool:
    if _has_parsed_usage_rows(payload):
        return True
    text = page_text(payload)
    return any(marker in text for marker in USAGE_PAGE_MARKERS)


def is_security_verification_page(payload: dict[str, Any]) -> bool:
    if has_usage_page_signal(payload):
        return False
    title = str(payload.get("title") or "").lower()
    text = page_text(payload)
    if any(marker in text for marker in SECURITY_VERIFICATION_STRONG_MARKERS):
        return True
    return any(marker in title for marker in SECURITY_VERIFICATION_TITLE_MARKERS) and any(
        marker in text for marker in SECURITY_VERIFICATION_SOFT_MARKERS
    )


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
