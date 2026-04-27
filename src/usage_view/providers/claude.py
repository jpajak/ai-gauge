from __future__ import annotations

from datetime import timedelta
from typing import Any, Callable

from PyQt6.QtCore import QObject

from ..models import SnapshotStatus, UsageMetric, UsageSnapshot
from ..webview.scraper import HeadlessScraper
from .codex import _parse_reset_text  # reuse the same heuristic parser
from .base import Provider
from .idle import idle_reset_state

CLAUDE_USAGE_URL = "https://claude.ai/settings/usage"

# Claude's settings/usage page renders rows like:
#   "Current session  Resets in 2 hr 59 min  [bar]  64% used"
#   "All models       Resets in 6 hr 29 min  [bar]  30% used"
# We locate each row by its label text, then read the % and reset string.
EXTRACTOR_JS = r"""
(() => {
  const ROW_LABELS = [
    'Current session',
    'All models',
    'Claude Design',
    'Daily included routine runs'
  ];

  function norm(el) {
    return (el.textContent || '').replace(/\s+/g, ' ').trim();
  }

  function findRowByLabel(label) {
    const candidates = Array.from(document.querySelectorAll('div, section, li'));
    let best = null;
    let bestScore = Infinity;
    for (const el of candidates) {
      const t = norm(el);
      if (!t.toLowerCase().includes(label.toLowerCase())) continue;
      if (!/%/.test(t)) continue;
      let score = t.length;
      for (const other of ROW_LABELS) {
        if (other !== label && t.toLowerCase().includes(other.toLowerCase())) {
          score += 10000;
        }
      }
      // Prefer actual row-ish containers over large sections or page wrappers.
      const rect = el.getBoundingClientRect();
      if (rect.height > 140) score += 5000;
      if (score < bestScore) {
        best = el;
        bestScore = score;
      }
    }
    return best;
  }

  function readRow(label) {
    const row = findRowByLabel(label);
    if (!row) return null;
    const text = norm(row);
    const pctMatches = Array.from(text.matchAll(/(\d+(?:\.\d+)?)\s*%/g));
    const pctMatch = pctMatches[pctMatches.length - 1];
    const remaining = /remaining/i.test(text);
    const used = /used/i.test(text);
    const resetMatch = text.match(/Resets?\s+(?:in\s+)?(.+?)(?=\s*$|\s+(?:Daily|Weekly|All|Current|Claude|You)\b|\s*\d+%)/i);
    return {
      raw: text.slice(0, 400),
      percent: pctMatch ? parseFloat(pctMatch[1]) : null,
      kind: remaining ? 'remaining' : (used ? 'used' : 'unknown'),
      reset_text: resetMatch ? resetMatch[1].trim() : null,
    };
  }

  const isLoggedOut =
    !!document.querySelector('a[href*="/login"]') &&
    !document.body.textContent.includes('Plan usage limits');

  return {
    logged_out: isLoggedOut,
    session: readRow('Current session'),
    weekly_all: readRow('All models'),
    weekly_design: readRow('Claude Design'),
    url: location.href,
  };
})();
"""


def _normalize_percent(percent: float | None, kind: str) -> float | None:
    if percent is None:
        return None
    if kind == "remaining":
        return max(0.0, 100.0 - percent)
    return percent


def _build_snapshot(payload: dict[str, Any]) -> UsageSnapshot:
    if payload.get("logged_out"):
        return UsageSnapshot(
            provider="claude",
            status=SnapshotStatus.AUTH_REQUIRED,
            error="Not signed in to Claude.",
        )

    rows = (
        ("session", "Session", timedelta(hours=5)),
        ("weekly_all", "Weekly", timedelta(days=7)),
        ("weekly_design", "Design", timedelta(days=7)),
    )
    metrics: list[UsageMetric] = []
    for key, label, reset_window in rows:
        card = payload.get(key)
        if not card:
            continue
        percent = _normalize_percent(card.get("percent"), card.get("kind", ""))
        if percent is None:
            continue
        resets_at = _parse_reset_text(card.get("reset_text"))
        resets_at, reset_label, idle_note = idle_reset_state(
            percent=percent,
            resets_at=resets_at,
            window=reset_window,
        )
        metrics.append(
            UsageMetric(
                label=label,
                percent_used=percent,
                resets_at=resets_at,
                reset_label=reset_label,
                note=idle_note or card.get("reset_text"),
            )
        )

    if not metrics:
        return UsageSnapshot(
            provider="claude",
            status=SnapshotStatus.ERROR,
            error="Could not read usage from page (layout may have changed).",
            raw=payload,
        )

    return UsageSnapshot(
        provider="claude",
        status=SnapshotStatus.OK,
        metrics=metrics,
        raw=payload,
    )


class ClaudeProvider(Provider):
    name = "claude"
    display_name = "Claude"

    def __init__(self, parent: QObject | None = None):
        self._parent = parent
        self._scraper: HeadlessScraper | None = None

    def refresh(self, on_done: Callable[[UsageSnapshot], None]) -> None:
        def _handle(result: Any, error: str) -> None:
            self._scraper = None
            if error or not isinstance(result, dict):
                on_done(
                    UsageSnapshot(
                        provider="claude",
                        status=SnapshotStatus.ERROR,
                        error=error or "no data extracted",
                    )
                )
                return
            on_done(_build_snapshot(result))

        self._scraper = HeadlessScraper(
            provider="claude",
            url=CLAUDE_USAGE_URL,
            extractor_js=EXTRACTOR_JS,
            wait_ms=5000,
            parent=self._parent,
        )
        self._scraper.done.connect(_handle)
