from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Callable

from PyQt6.QtCore import QObject

from ..models import SnapshotStatus, UsageMetric, UsageSnapshot
from ..webview.scraper import HeadlessScraper
from .base import Provider
from .idle import idle_reset_state

CODEX_USAGE_URL = "https://chatgpt.com/codex/cloud/settings/analytics#usage"
log = logging.getLogger("usage_view.providers.codex")

# Walks the rendered analytics page, finds the two "Balance" cards by their
# headings, and reads the percentage + reset text out of each. Returns raw text
# fragments so Python can do the unit-aware normalization.
EXTRACTOR_JS = r"""
(() => {
  function findCardByLabel(label) {
    const headings = Array.from(document.querySelectorAll('h1,h2,h3,h4,div,span,p'));
    const heading = headings.find(el => {
      const t = (el.textContent || '').trim();
      return t === label || t.toLowerCase() === label.toLowerCase();
    });
    if (!heading) return null;
    let card = heading;
    for (let i = 0; i < 6 && card.parentElement; i++) {
      card = card.parentElement;
      const txt = card.textContent || '';
      if (/%/.test(txt) && /reset/i.test(txt)) return card;
    }
    return null;
  }

  function readCard(label) {
    const card = findCardByLabel(label);
    if (!card) return null;
    const text = (card.textContent || '').replace(/\s+/g, ' ').trim();
    const pctMatch = text.match(/(\d+(?:\.\d+)?)\s*%/);
    const remaining = /remaining/i.test(text);
    const used = /used/i.test(text);
    const resetMatch = text.match(/Resets?\s+(?:in\s+)?(.+?)(?=\s*$|\s+(?:Daily|Weekly|All|Current))/i);
    return {
      raw: text.slice(0, 400),
      percent: pctMatch ? parseFloat(pctMatch[1]) : null,
      kind: remaining ? 'remaining' : (used ? 'used' : 'unknown'),
      reset_text: resetMatch ? resetMatch[1].trim() : null,
    };
  }

  const bodyText = (document.body.textContent || '').replace(/\s+/g, ' ').trim();
  const lowerText = bodyText.toLowerCase();
  const isLoggedOut =
    !!document.querySelector('a[href*="/auth/login"], a[href*="/login"]') ||
    location.pathname.includes('/auth/login') ||
    location.pathname === '/login' ||
    document.title.toLowerCase().includes('login') ||
    (/log in|sign in/.test(lowerText) && !/usage limit/i.test(bodyText));
  return {
    logged_out: isLoggedOut,
    session: readCard('5 hour usage limit'),
    weekly: readCard('Weekly usage limit'),
    url: location.href,
    title: document.title,
    body_text: bodyText.slice(0, 500),
  };
})();
"""


_WEEKDAYS = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}


def _parse_reset_text(text: str | None) -> datetime | None:
    """Best-effort parse of strings like 'Mon 6:00 PM', '1:55 PM', or '2h 59m'."""
    if not text:
        return None
    text = text.strip().rstrip(".")
    now = datetime.now()

    # Relative: "in 2 hr 59 min", "2h 59m", "6 hr 29 min"
    rel = re.match(
        r"(?:in\s+)?(?:(\d+)\s*(?:hr|h|hour)s?)?\s*(?:(\d+)\s*(?:min|m|minute)s?)?",
        text,
        re.IGNORECASE,
    )
    if rel and (rel.group(1) or rel.group(2)):
        hours = int(rel.group(1) or 0)
        minutes = int(rel.group(2) or 0)
        if hours or minutes:
            return now + timedelta(hours=hours, minutes=minutes)

    # Absolute date+time: "Apr 29, 2026 8:53 AM"
    for fmt in ("%b %d, %Y %I:%M %p", "%b %d %I:%M %p", "%B %d, %Y %I:%M %p"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    # Weekday + time: "Mon 6:00 PM", "Monday 18:00" → next matching weekday.
    weekday_match = re.match(r"([A-Za-z]+)\s+(.+)$", text)
    if weekday_match:
        weekday = _WEEKDAYS.get(weekday_match.group(1).lower())
        time_text = weekday_match.group(2).strip()
        if weekday is not None:
            for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M"):
                try:
                    t = datetime.strptime(time_text, fmt).time()
                    days_ahead = (weekday - now.weekday()) % 7
                    candidate = datetime.combine(
                        now.date() + timedelta(days=days_ahead),
                        t,
                    )
                    if candidate <= now:
                        candidate += timedelta(days=7)
                    return candidate
                except ValueError:
                    continue

    # Time-of-day only: "1:55 PM" → today (or tomorrow if past)
    for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M"):
        try:
            t = datetime.strptime(text, fmt).time()
            candidate = datetime.combine(now.date(), t)
            if candidate < now:
                candidate += timedelta(days=1)
            return candidate
        except ValueError:
            continue

    return None


def _normalize_percent(percent: float | None, kind: str) -> float | None:
    if percent is None:
        return None
    if kind == "remaining":
        return max(0.0, 100.0 - percent)
    return percent


def _looks_like_empty_signed_in_usage(payload: dict[str, Any]) -> bool:
    url = str(payload.get("url") or "")
    if not url.startswith(CODEX_USAGE_URL.split("#", maxsplit=1)[0]):
        return False
    page_text = f"{payload.get('title', '')} {payload.get('body_text', '')}".lower()
    if "%" in page_text or "usage limit" in page_text:
        return False
    return any(marker in page_text for marker in ("codex", "chatgpt", "tasks", "cloud"))


def _empty_usage_metrics() -> list[UsageMetric]:
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


def _is_logged_out_payload(payload: dict[str, Any]) -> bool:
    url = str(payload.get("url") or "").lower()
    return (
        bool(payload.get("logged_out"))
        or "/auth/login" in url
        or "/login" in url
        or "/logout" in url
    )


def _build_snapshot(payload: dict[str, Any]) -> UsageSnapshot:
    page_text = f"{payload.get('title', '')} {payload.get('body_text', '')}".lower()
    if _is_logged_out_payload(payload):
        return UsageSnapshot(
            provider="codex",
            status=SnapshotStatus.AUTH_REQUIRED,
            error="Not signed in to ChatGPT.",
            raw=payload,
        )
    if (
        "verify you are human" in page_text
        or "just a moment" in page_text
        or "cloudflare" in page_text
    ):
        return UsageSnapshot(
            provider="codex",
            status=SnapshotStatus.AUTH_REQUIRED,
            error="ChatGPT security verification required. Click Connect and complete the browser check.",
            raw=payload,
        )

    metrics: list[UsageMetric] = []
    for key, label in (("session", "Session"), ("weekly", "Weekly")):
        card = payload.get(key)
        if not card:
            continue
        percent = _normalize_percent(card.get("percent"), card.get("kind", ""))
        resets_at = _parse_reset_text(card.get("reset_text"))
        reset_window = timedelta(hours=5) if key == "session" else timedelta(days=7)
        resets_at, reset_label, idle_note = idle_reset_state(
            percent=percent,
            resets_at=resets_at,
            window=reset_window,
        )
        note = idle_note or card.get("reset_text")
        metrics.append(
            UsageMetric(
                label=label,
                percent_used=percent,
                resets_at=resets_at,
                reset_label=reset_label,
                note=note,
            )
        )

    if not metrics or all(m.percent_used is None for m in metrics):
        if _looks_like_empty_signed_in_usage(payload):
            return UsageSnapshot(
                provider="codex",
                status=SnapshotStatus.OK,
                metrics=_empty_usage_metrics(),
                raw=payload,
            )
        return UsageSnapshot(
            provider="codex",
            status=SnapshotStatus.ERROR,
            error="Could not read usage from page (layout may have changed).",
            raw=payload,
        )

    return UsageSnapshot(
        provider="codex",
        status=SnapshotStatus.OK,
        metrics=metrics,
        raw=payload,
    )


class CodexProvider(Provider):
    name = "codex"
    display_name = "Codex"

    def __init__(self, parent: QObject | None = None):
        self._parent = parent
        self._scraper: HeadlessScraper | None = None  # held to prevent GC

    def refresh(self, on_done: Callable[[UsageSnapshot], None]) -> None:
        def _handle(result: Any, error: str) -> None:
            self._scraper = None
            if error or not isinstance(result, dict):
                snapshot = UsageSnapshot(
                    provider="codex",
                    status=SnapshotStatus.ERROR,
                    error=error or "no data extracted",
                )
                log.warning(
                    "provider snapshot error provider=codex reason=%s", snapshot.error
                )
                on_done(snapshot)
                return
            snapshot = _build_snapshot(result)
            if snapshot.status == SnapshotStatus.ERROR:
                log.warning(
                    "provider snapshot error provider=codex reason=%s", snapshot.error
                )
            on_done(snapshot)

        self._scraper = HeadlessScraper(
            provider="codex",
            url=CODEX_USAGE_URL,
            extractor_js=EXTRACTOR_JS,
            wait_ms=5000,
            parent=self._parent,
        )
        self._scraper.done.connect(_handle)
