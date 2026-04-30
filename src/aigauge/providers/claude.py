from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Callable

from PyQt6.QtCore import QObject

from ..models import SnapshotStatus, UsageMetric, UsageSnapshot
from ..webview.scraper import HeadlessScraper
from .codex import _parse_reset_text  # reuse the same heuristic parser
from .base import Provider
from .diagnostics import log_page_diagnosis
from .idle import idle_reset_state

CLAUDE_USAGE_URL = "https://claude.ai/settings/usage"
_EXPECTED_ROWS = ("session", "weekly_all", "weekly_design")
log = logging.getLogger("aigauge.providers.claude")

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
    title: document.title,
    body_text: (document.body.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 8000),
  };
})();
"""


def _normalize_percent(percent: float | None, kind: str) -> float | None:
    if percent is None:
        return None
    if kind == "remaining":
        return max(0.0, 100.0 - percent)
    return percent


def _looks_like_empty_signed_in_usage(payload: dict[str, Any]) -> bool:
    if payload.get("url") != CLAUDE_USAGE_URL:
        return False
    title = str(payload.get("title") or "").strip().lower()
    if title != "claude":
        return False
    page_text = str(payload.get("body_text") or "").lower()
    # Require positive evidence the usage panel actually rendered. Without
    # this, a partially-loaded page (sidebar only, main pane still fetching)
    # gets misclassified as idle and shown as 0/0.
    if "plan usage limits" not in page_text:
        return False
    # If percent text is on the page but the row extractor missed it, that's
    # a layout change — not idle.
    if "%" in page_text:
        return False
    return True


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
    return bool(payload.get("logged_out")) or "/logout" in url or "/login" in url


def _is_load_failed_payload(payload: dict[str, Any]) -> bool:
    page_text = f"{payload.get('title', '')} {payload.get('body_text', '')}".lower()
    return (
        "can't reach claude" in page_text
        or "check your connection" in page_text
        or "try again" in page_text and "claude" in page_text
    )


def _build_snapshot(
    payload: dict[str, Any],
    *,
    show_design: bool = False,
) -> UsageSnapshot:
    page_text = f"{payload.get('title', '')} {payload.get('body_text', '')}".lower()
    if _is_logged_out_payload(payload):
        log_page_diagnosis(
            log,
            provider="claude",
            classification="logged_out",
            payload=payload,
            expected_rows=_EXPECTED_ROWS,
        )
        return UsageSnapshot(
            provider="claude",
            status=SnapshotStatus.AUTH_REQUIRED,
            error="Not signed in to Claude.",
            raw=payload,
        )
    if _is_load_failed_payload(payload):
        log_page_diagnosis(
            log,
            provider="claude",
            classification="load_failed",
            payload=payload,
            expected_rows=_EXPECTED_ROWS,
            level=logging.WARNING,
        )
        return UsageSnapshot(
            provider="claude",
            status=SnapshotStatus.ERROR,
            error="Claude page load failed. Check your connection and try again.",
            raw=payload,
        )
    if (
        "verify you are human" in page_text
        or "just a moment" in page_text
        or "cloudflare" in page_text
    ):
        log_page_diagnosis(
            log,
            provider="claude",
            classification="security_verification",
            payload=payload,
            expected_rows=_EXPECTED_ROWS,
        )
        return UsageSnapshot(
            provider="claude",
            status=SnapshotStatus.AUTH_REQUIRED,
            error="Claude security verification required. Click Connect and complete the browser check.",
            raw=payload,
        )

    rows = (
        ("session", "Session", timedelta(hours=5)),
        ("weekly_all", "Weekly", timedelta(days=7)),
    )
    if show_design:
        rows += (("weekly_design", "Design", timedelta(days=7)),)
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
                window=reset_window,
            )
        )

    if not metrics:
        if _looks_like_empty_signed_in_usage(payload):
            log_page_diagnosis(
                log,
                provider="claude",
                classification="empty_signed_in_usage",
                payload=payload,
                expected_rows=_EXPECTED_ROWS,
            )
            metrics = _empty_usage_metrics()
        else:
            log_page_diagnosis(
                log,
                provider="claude",
                classification="layout_changed",
                payload=payload,
                expected_rows=_EXPECTED_ROWS,
                level=logging.WARNING,
            )
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

    _MAX_BUILD_ATTEMPTS = 2

    def __init__(self, parent: QObject | None = None, show_design: bool = False):
        self._parent = parent
        self._scraper: HeadlessScraper | None = None
        self._show_design = show_design
        self._build_attempts = 0
        self._on_done: Callable[[UsageSnapshot], None] | None = None

    def refresh(self, on_done: Callable[[UsageSnapshot], None]) -> None:
        self._on_done = on_done
        self._build_attempts = 0
        self._start_scrape()

    def _start_scrape(self) -> None:
        self._build_attempts += 1
        self._scraper = HeadlessScraper(
            provider="claude",
            url=CLAUDE_USAGE_URL,
            extractor_js=EXTRACTOR_JS,
            wait_ms=5000,
            max_attempts=2,
            parent=self._parent,
        )
        self._scraper.done.connect(self._handle)

    def _handle(self, result: Any, error: str) -> None:
        self._scraper = None
        on_done = self._on_done
        if on_done is None:
            return
        if error or not isinstance(result, dict):
            snapshot = UsageSnapshot(
                provider="claude",
                status=SnapshotStatus.ERROR,
                error=error or "no data extracted",
            )
            log.warning(
                "provider snapshot error provider=claude reason=%s", snapshot.error
            )
            self._on_done = None
            on_done(snapshot)
            return
        snapshot = _build_snapshot(result, show_design=self._show_design)
        if (
            snapshot.status == SnapshotStatus.ERROR
            and self._build_attempts < self._MAX_BUILD_ATTEMPTS
        ):
            # The page loaded but the usage panel hadn't populated. Retry the
            # whole scrape — usually the second attempt sees the rendered DOM.
            log.warning(
                "provider transient error provider=claude attempt=%s reason=%s — retrying",
                self._build_attempts,
                snapshot.error,
            )
            self._start_scrape()
            return
        if snapshot.status == SnapshotStatus.ERROR:
            log.warning(
                "provider snapshot error provider=claude reason=%s", snapshot.error
            )
        self._on_done = None
        on_done(snapshot)
