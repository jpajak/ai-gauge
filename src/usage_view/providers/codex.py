from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Callable

from PyQt6.QtCore import QObject

from ..models import SnapshotStatus, UsageMetric, UsageSnapshot
from ..webview.scraper import HeadlessScraper
from .base import Provider

CODEX_USAGE_URL = "https://chatgpt.com/codex/cloud/settings/analytics#usage"

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

  const isLoggedOut = !!document.querySelector('a[href*="/auth/login"], a[href*="/login"]');
  return {
    logged_out: isLoggedOut,
    session: readCard('5 hour usage limit'),
    weekly: readCard('Weekly usage limit'),
    url: location.href,
    title: document.title,
    body_text: (document.body.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 500),
  };
})();
"""


def _parse_reset_text(text: str | None) -> datetime | None:
    """Best-effort parse of strings like '1:55 PM' or 'Apr 29, 2026 8:53 AM' or '2h 59m'."""
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


def _build_snapshot(payload: dict[str, Any]) -> UsageSnapshot:
    if payload.get("logged_out"):
        return UsageSnapshot(
            provider="codex",
            status=SnapshotStatus.AUTH_REQUIRED,
            error="Not signed in to ChatGPT.",
        )

    metrics: list[UsageMetric] = []
    for key, label in (("session", "5 hour"), ("weekly", "Weekly")):
        card = payload.get(key)
        if not card:
            continue
        metrics.append(
            UsageMetric(
                label=label,
                percent_used=_normalize_percent(card.get("percent"), card.get("kind", "")),
                resets_at=_parse_reset_text(card.get("reset_text")),
                note=card.get("reset_text"),
            )
        )

    if not metrics or all(m.percent_used is None for m in metrics):
        print(f"Codex scrape failed: {payload}")
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
                on_done(
                    UsageSnapshot(
                        provider="codex",
                        status=SnapshotStatus.ERROR,
                        error=error or "no data extracted",
                    )
                )
                return
            on_done(_build_snapshot(result))

        self._scraper = HeadlessScraper(
            provider="codex",
            url=CODEX_USAGE_URL,
            extractor_js=EXTRACTOR_JS,
            wait_ms=5000,
            parent=self._parent,
        )
        self._scraper.done.connect(_handle)
