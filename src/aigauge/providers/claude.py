from __future__ import annotations

import logging
import re
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

CLAUDE_USAGE_URL = "https://claude.ai/settings/usage"
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
  const ROW_LABEL_GROUPS = [
    ['Current session'],
    // Claude renamed these rows in September 2026. Keep the old labels for
    // accounts that have not received the new usage-dialog rollout yet.
    ['All models', 'This week'],
    ['Fable this week', 'Fable'],
    ['Daily included routine runs']
  ];

  function norm(el) {
    return (el.textContent || '').replace(/\s+/g, ' ').trim();
  }

  // A label only counts when it actually heads a usage row — i.e. it is
  // followed by the reset text or the percentage. Claude's usage page also
  // mentions model names in prose banners ("Fable 5 is still included with
  // your Max plan"), and a container holding such a banner plus a *different*
  // row would otherwise be read as that model's row, reporting the neighbour's
  // percentage. Every occurrence is checked, so a wrapper holding both the
  // banner and the real row still qualifies.
  function labelTailMatches(text, label, predicate) {
    const lower = text.toLowerCase();
    const needle = label.toLowerCase();
    let i = lower.indexOf(needle);
    while (i !== -1) {
      const after = text.slice(i + needle.length).trim();
      if (predicate(after)) return true;
      i = lower.indexOf(needle, i + 1);
    }
    return false;
  }

  function withoutVersion(after) {
    // Absorb a row renamed to "Fable 5". The prose banner starts "Fable 5 is
    // still included…", which does not match any valid row tail below.
    return after.replace(/^\d+(?:\.\d+)?\s+/, '');
  }

  function isIdleRowTail(after) {
    const tail = withoutVersion(after);
    return /^resets?\s+when\b/i.test(tail) ||
      /^starts\s+when\b.*\bmessage\b/i.test(tail) ||
      /^starts\s+with\b.*\b(?:first\s+)?message\b/i.test(tail) ||
      /^you\s+haven['’]t\s+used\b.*\byet\b/i.test(tail);
  }

  function hasUsageValue(after) {
    // The prose between a row label and its value changes fairly often. Treat
    // the percentage as the stable signal instead of enumerating every status
    // sentence (for example "Paused until your week resets"). rowText() below
    // keeps the match inside this row, so a neighbouring percentage cannot
    // make an otherwise unrelated label look valid.
    return /\d+(?:\.\d+)?\s*%(?:\s*(?:used|remaining))?/i.test(after) ||
      /\b(?:used|remaining)\s*:?\s*\d+(?:\.\d+)?\s*%/i.test(after);
  }

  function headsARow(text, label) {
    return labelTailMatches(text, label, (after) => {
      const tail = withoutVersion(after);
      // A standalone label or reset caption is not a hydrated row. The value
      // may live in a parent element, which will be considered separately.
      return isIdleRowTail(after) || hasUsageValue(tail);
    });
  }

  function hasIdleRowCopy(text, label) {
    return labelTailMatches(text, label, isIdleRowTail);
  }

  function isEmbeddedLabel(text, index, label) {
    // "This week" is also part of the distinct "Fable this week" label.
    // Do not let the Fable row stand in for the all-model weekly row.
    return label.toLowerCase() === 'this week' &&
      text.slice(Math.max(0, index - 6), index).toLowerCase() === 'fable ';
  }

  function nextRowLabel(text, start) {
    const lower = text.toLowerCase();
    let end = text.length;
    for (const group of ROW_LABEL_GROUPS) {
      for (const label of group) {
        let index = lower.indexOf(label.toLowerCase(), start);
        while (index !== -1 && isEmbeddedLabel(text, index, label)) {
          index = lower.indexOf(label.toLowerCase(), index + 1);
        }
        if (index !== -1 && index < end) end = index;
      }
    }
    return end;
  }

  function rowText(text, labels) {
    const lower = text.toLowerCase();
    let best = null;
    for (const label of labels) {
      let index = lower.indexOf(label.toLowerCase());
      while (index !== -1) {
        if (!isEmbeddedLabel(text, index, label)) {
          const afterLabel = index + label.length;
          const candidate = text.slice(index, nextRowLabel(text, afterLabel)).trim();
          if (headsARow(candidate, label) && (!best || candidate.length < best.length)) {
            best = candidate;
          }
        }
        index = lower.indexOf(label.toLowerCase(), index + 1);
      }
    }
    return best;
  }

  function findRowByLabels(labels) {
    const candidates = Array.from(document.querySelectorAll('div, section, li'));
    let best = null;
    let bestScore = Infinity;
    for (const el of candidates) {
      const t = norm(el);
      const row = rowText(t, labels);
      if (!row) continue;
      let score = row.length;
      // Prefer actual row-ish containers over large sections or page wrappers.
      const rect = el.getBoundingClientRect();
      if (rect.height > 140) score += 5000;
      if (score < bestScore) {
        best = row;
        bestScore = score;
      }
    }
    return best;
  }

  function readRow(labels) {
    const text = findRowByLabels(labels);
    if (!text) return null;
    const pctMatches = Array.from(text.matchAll(/(\d+(?:\.\d+)?)\s*%/g));
    const pctMatch = pctMatches[pctMatches.length - 1];
    const idle = labels.some(label => hasIdleRowCopy(text, label));
    const remaining = /remaining/i.test(text);
    const used = /used/i.test(text);
    const resetMatch = text.match(/Resets?\s+(?:in\s+)?(.+?)(?=\s*$|\s+(?:Daily|Weekly|All|Current|Claude|Fable|You)\b|\s*\d+%)/i);
    const pausedMatch = text.match(/\b((?:paused|unavailable|blocked)\b.+?\b(?:resets?|renews?))(?=\s*\d+%|\s*$)/i);
    return {
      raw: text.slice(0, 400),
      // The label-specific idle copy is more authoritative than a percentage
      // or "remaining" word inherited from a larger wrapper around other rows.
      percent: idle ? 0 : (pctMatch ? parseFloat(pctMatch[1]) : null),
      kind: idle ? 'used' : (remaining ? 'remaining' : (used ? 'used' : 'unknown')),
      // A paused sentence can itself contain "resets"; preserve the whole
      // status instead of treating the words after that verb as a countdown.
      reset_text: pausedMatch ? pausedMatch[1].trim() : (resetMatch ? resetMatch[1].trim() : null),
    };
  }

  const bodyText = (document.body.textContent || '').replace(/\s+/g, ' ').trim();
  const isLoggedOut =
    (location.hostname === 'claude.ai' &&
      (/^\/(?:login|logout)(?:\/|$)/.test(location.pathname) ||
        /^Sign in\s*[-–—]\s*Claude$/i.test(document.title))) ||
    (!!document.querySelector('a[href*="/login"]') &&
      !/Plan usage limits|Your usage/i.test(bodyText));

  const session = readRow(ROW_LABEL_GROUPS[0]);
  const weeklyAll = readRow(ROW_LABEL_GROUPS[1]);
  // Max-plan accounts only. Never gate readiness on this row — Pro/Free
  // accounts have no Fable row and would retry until timeout.
  const weeklyFable = readRow(ROW_LABEL_GROUPS[2]);

  // Claude's empty-usage page can omit "Current session" entirely. Accept
  // this as a zero session only with explicit idle text and a valid 0% weekly
  // row on the usage route. Do not convert partially loaded panels into zeros.
  // textContent joins adjacent elements without spaces: for example,
  // <h2>Your usage</h2><span>Ultra</span> becomes 'Your usageUltra'.
  // Prefer the usage component's semantic marker when present, but retain
  // text matching for legacy layouts that do not expose that marker.
  const usagePageVisible = !!document.querySelector('[data-usage-bars="usage_page"]') ||
    /Your usage/i.test(bodyText);
  const emptySession = !session && !!weeklyAll && weeklyAll.percent === 0 &&
    weeklyAll.kind === 'used' && onUsageRoute() && usagePageVisible &&
    /No usage yet\.\s*100%\s+of your weekly limit is available/i.test(bodyText);
  const sessionResult = session || (emptySession ? {
    raw: 'No usage yet', percent: 0, kind: 'used', reset_text: null
  } : null);

  function onUsageRoute() {
    return /\/settings\/usage/.test(location.pathname) ||
      /settings\/usage/i.test(location.hash);
  }

  function ensureUsageRoute() {
    if (onUsageRoute()) return null;
    if (location.hostname !== 'claude.ai') return null;
    if (session && weeklyAll) return null;
    if (/Plan usage limits|Your usage/i.test(bodyText) && /Current session/i.test(bodyText)) return null;
    location.href = '/settings/usage';
    return 'opened usage page';
  }

  const routeReason = !isLoggedOut ? ensureUsageRoute() : null;
  if (routeReason) {
    return {
      __retry_after_ms: 1200,
      __retry_reason: routeReason,
      logged_out: false,
      session: null,
      weekly_all: null,
      weekly_fable: null,
      url: location.href,
      title: document.title,
      body_text: bodyText.slice(0, 8000),
    };
  }

  // The current Claude UI opens usage as a shell/dialog route. Percent text
  // elsewhere in the shell is not enough; wait for the Session/Weekly rows
  // or for the explicit idle-zero usage panel before handing data to Python.
  const usagePanelSignals = /Plan usage limits|Your usage|Current session|All models|This week/i.test(bodyText);
  const idleUsagePanel = /Plan usage limits|Your usage/i.test(bodyText) &&
    /Current session/i.test(bodyText) &&
    /All models|This week/i.test(bodyText) &&
    !/%/.test(bodyText);
  const requiredRowsReady = !!sessionResult && !!weeklyAll;
  if (!isLoggedOut && (onUsageRoute() || usagePanelSignals) && !requiredRowsReady && !idleUsagePanel) {
    return {
      __retry_after_ms: 1200,
      __retry_reason: 'usage dialog not ready',
      logged_out: false,
      session: null,
      weekly_all: null,
      weekly_fable: null,
      url: location.href,
      title: document.title,
      body_text: bodyText.slice(0, 8000),
    };
  }

  return {
    logged_out: isLoggedOut,
    session: sessionResult,
    weekly_all: weeklyAll,
    weekly_fable: weeklyFable,
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
    title = str(payload.get("title") or "").strip()
    if re.search(r"(?:^|[-–—]\s*)Claude$", title, re.IGNORECASE) is None:
        return False
    body = str(payload.get("body_text") or "").lower()
    # Require positive evidence the usage panel actually rendered. Without
    # this, a partially-loaded page (sidebar only, main pane still fetching)
    # gets misclassified as idle and shown as 0/0.
    if "plan usage limits" not in body and "your usage" not in body:
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
    show_fable: bool = False,
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
    # Max plans expose a separate weekly Fable limit. Other plans have no such
    # row, and the loop below skips missing cards, so an enabled toggle on a
    # Pro account is a no-op rather than an error.
    if show_fable:
        rows += (("weekly_fable", "Fable", timedelta(days=7)),)
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
        show_fable: bool = False,
    ):
        self._parent = parent
        self._account_id = account_id
        self._show_fable = show_fable
        self._runner: ScrapeRunner | None = None  # held to prevent GC

    def refresh(self, on_done: Callable[[UsageSnapshot], None]) -> None:
        def _build(payload: dict[str, Any]) -> UsageSnapshot:
            return _build_snapshot(
                payload,
                account_id=self._account_id,
                show_fable=self._show_fable,
            )

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
