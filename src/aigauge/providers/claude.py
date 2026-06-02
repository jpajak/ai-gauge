from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Callable
from urllib.parse import urlparse

from PyQt6.QtCore import QObject

from ..models import SnapshotStatus, UsageMetric, UsageSnapshot
from ._common import (
    has_usage_page_signal,
    idle_session_weekly_metrics,
    is_security_verification_page,
    normalize_percent,
    page_text,
)
from ._scrape_runner import ScrapeRunner
from .codex import _parse_reset_text  # reuse the same heuristic parser
from .base import Provider
from .diagnostics import log_page_diagnosis
from .idle import idle_reset_state

CLAUDE_USAGE_URL = "https://claude.ai/new#settings/usage"
_EXPECTED_ROWS = ("session", "weekly_all")
log = logging.getLogger("aigauge.providers.claude")

# Claude's usage dialog renders rows like:
#   "Current session  Resets in 2 hr 59 min  [bar]  64% used"
#   "All models       Resets in 6 hr 29 min  [bar]  30% used"
# We locate each row by its label text, then read the % and reset string.
#
# If Claude lands in the signed-in app shell before the usage dialog has
# hydrated, the extractor asks the scraper to poll again in-page rather than
# failing on sidebar/chat text. It waits for the Session/Weekly rows, not just
# any percent text elsewhere in the shell.
EXTRACTOR_JS = r"""
(() => {
  const ROW_LABELS = [
    'Current session',
    'All models',
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

  const bodyText = (document.body.textContent || '').replace(/\s+/g, ' ').trim();
  const isLoggedOut =
    !!document.querySelector('a[href*="/login"]') &&
    !bodyText.includes('Plan usage limits');

  const session = readRow('Current session');
  const weeklyAll = readRow('All models');

  function onUsageRoute() {
    return /\/settings\/usage/.test(location.pathname) ||
      /settings\/usage/i.test(location.hash);
  }

  function ensureUsageRoute() {
    if (onUsageRoute()) return null;
    if (location.hostname !== 'claude.ai') return null;
    if (/Plan usage limits|Current session|All models/i.test(bodyText)) return null;
    location.href = '/new#settings/usage';
    return 'opened usage dialog';
  }

  const routeReason = !isLoggedOut ? ensureUsageRoute() : null;
  if (routeReason) {
    return {
      __retry_after_ms: 1200,
      __retry_reason: routeReason,
      logged_out: false,
      session: null,
      weekly_all: null,
      url: location.href,
      title: document.title,
      body_text: bodyText.slice(0, 8000),
    };
  }

  // The current Claude UI opens usage as a shell/dialog route. Percent text
  // elsewhere in the shell is not enough; wait for the Session/Weekly rows
  // or for the explicit idle-zero usage panel before handing data to Python.
  const usagePanelSignals = /Plan usage limits|Current session|All models/i.test(bodyText);
  const idleUsagePanel = /Plan usage limits/i.test(bodyText) &&
    /Current session/i.test(bodyText) &&
    /All models/i.test(bodyText) &&
    !/%/.test(bodyText);
  const requiredRowsReady = !!session && !!weeklyAll;
  if (!isLoggedOut && (onUsageRoute() || usagePanelSignals) && !requiredRowsReady && !idleUsagePanel) {
    return {
      __retry_after_ms: 1200,
      __retry_reason: 'usage dialog not ready',
      logged_out: false,
      session: null,
      weekly_all: null,
      url: location.href,
      title: document.title,
      body_text: bodyText.slice(0, 8000),
    };
  }

  return {
    logged_out: isLoggedOut,
    session: session,
    weekly_all: weeklyAll,
    url: location.href,
    title: document.title,
    body_text: bodyText.slice(0, 8000),
  };
})();
"""


def _is_claude_usage_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.netloc and parsed.netloc != "claude.ai":
        return False
    return parsed.path == "/settings/usage" or "settings/usage" in parsed.fragment


def _looks_like_empty_signed_in_usage(payload: dict[str, Any]) -> bool:
    if not _is_claude_usage_url(str(payload.get("url") or "")):
        return False
    title = str(payload.get("title") or "").strip().lower()
    if title != "claude":
        return False
    body = str(payload.get("body_text") or "").lower()
    # Require positive evidence the usage panel actually rendered. Without
    # this, a partially-loaded page (sidebar only, main pane still fetching)
    # gets misclassified as idle and shown as 0/0.
    if "plan usage limits" not in body:
        return False
    # If percent text is on the page but the row extractor missed it, that's
    # a layout change — not idle.
    if "%" in body:
        return False
    return True


def _is_logged_out_payload(payload: dict[str, Any]) -> bool:
    url = str(payload.get("url") or "").lower()
    return bool(payload.get("logged_out")) or "/logout" in url or "/login" in url


def _is_load_failed_payload(payload: dict[str, Any]) -> bool:
    if has_usage_page_signal(payload):
        return False
    text = page_text(payload)
    return (
        "can't reach claude" in text
        or "check your connection" in text
        or ("try again" in text and "claude" in text)
    )


def _build_snapshot(
    payload: dict[str, Any],
    *,
    account_id: str = "claude",
) -> UsageSnapshot:
    if _is_logged_out_payload(payload):
        log_page_diagnosis(
            log,
            provider=account_id,
            classification="logged_out",
            payload=payload,
            expected_rows=_EXPECTED_ROWS,
        )
        return UsageSnapshot(
            provider=account_id,
            status=SnapshotStatus.AUTH_REQUIRED,
            error="Not signed in to Claude.",
            raw=payload,
        )
    if _is_load_failed_payload(payload):
        log_page_diagnosis(
            log,
            provider=account_id,
            classification="load_failed",
            payload=payload,
            expected_rows=_EXPECTED_ROWS,
            level=logging.WARNING,
        )
        return UsageSnapshot(
            provider=account_id,
            status=SnapshotStatus.ERROR,
            error="Claude page load failed. Check your connection and try again.",
            raw=payload,
        )
    if is_security_verification_page(payload):
        log_page_diagnosis(
            log,
            provider=account_id,
            classification="security_verification",
            payload=payload,
            expected_rows=_EXPECTED_ROWS,
        )
        return UsageSnapshot(
            provider=account_id,
            status=SnapshotStatus.AUTH_REQUIRED,
            error="Claude security verification required. Click Connect and complete the browser check.",
            raw=payload,
        )

    rows = (
        ("session", "Session", timedelta(hours=5)),
        ("weekly_all", "Weekly", timedelta(days=7)),
    )
    metrics: list[UsageMetric] = []
    for key, label, reset_window in rows:
        card = payload.get(key)
        if not card:
            continue
        percent = normalize_percent(card.get("percent"), card.get("kind", ""))
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
                provider=account_id,
                classification="empty_signed_in_usage",
                payload=payload,
                expected_rows=_EXPECTED_ROWS,
            )
            metrics = idle_session_weekly_metrics()
        else:
            log_page_diagnosis(
                log,
                provider=account_id,
                classification="layout_changed",
                payload=payload,
                expected_rows=_EXPECTED_ROWS,
                level=logging.WARNING,
            )
            return UsageSnapshot(
                provider=account_id,
                status=SnapshotStatus.ERROR,
                error="Could not read usage from page (layout may have changed).",
                raw=payload,
            )

    return UsageSnapshot(
        provider=account_id,
        status=SnapshotStatus.OK,
        metrics=metrics,
        raw=payload,
    )


class ClaudeProvider(Provider):
    name = "claude"
    display_name = "Claude"

    def __init__(
        self,
        parent: QObject | None = None,
        account_id: str = "claude",
    ):
        self._parent = parent
        self._account_id = account_id
        self._runner: ScrapeRunner | None = None  # held to prevent GC

    def refresh(self, on_done: Callable[[UsageSnapshot], None]) -> None:
        def _build(payload: dict[str, Any]) -> UsageSnapshot:
            return _build_snapshot(payload, account_id=self._account_id)

        self._runner = ScrapeRunner(
            account_id=self._account_id,
            url=CLAUDE_USAGE_URL,
            extractor_js=EXTRACTOR_JS,
            build=_build,
            log=log,
            wait_ms=7000,
            transport_max_attempts=2,
            build_max_attempts=2,
            parent=self._parent,
        )
        self._runner.run(on_done)
