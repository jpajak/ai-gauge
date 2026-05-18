from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Callable

from PyQt6.QtCore import QObject

from ..models import SnapshotStatus, UsageMetric, UsageSnapshot
from ._common import (
    idle_session_weekly_metrics,
    is_security_verification_page,
    normalize_percent,
)
from ._scrape_runner import ScrapeRunner
from .base import Provider
from .diagnostics import log_page_diagnosis
from .idle import idle_reset_state

CODEX_ANALYTICS_URL = "https://chatgpt.com/codex/cloud/settings/analytics"
CODEX_USAGE_URL = f"{CODEX_ANALYTICS_URL}#personal-usage"
_EXPECTED_ROWS = ("session", "weekly")
log = logging.getLogger("aigauge.providers.codex")

# Walks the rendered analytics page, finds the two "Balance" cards by their
# headings, and reads the percentage + reset text out of each. Returns raw text
# fragments so Python can do the unit-aware normalization.
EXTRACTOR_JS = r"""
(() => {
  function visibleText(el) {
    return ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
  }

  function windowTextAfterLabel(label, nextLabels) {
    const bodyText = visibleText(document.body);
    const lowerText = bodyText.toLowerCase();
    const start = lowerText.indexOf(label.toLowerCase());
    if (start < 0) return '';
    let end = bodyText.length;
    for (const nextLabel of nextLabels) {
      const candidate = lowerText.indexOf(nextLabel.toLowerCase(), start + label.length);
      if (candidate >= 0 && candidate < end) end = candidate;
    }
    return bodyText.slice(start, end).trim();
  }

  function maybeSelectPersonalUsageTab(bodyText) {
    if (/5 hour usage limit/i.test(bodyText) && /Weekly usage limit/i.test(bodyText)) {
      return null;
    }

    const labels = Array.from(document.querySelectorAll('button,a,[role="tab"],[role="button"],div,span,p'));
    const label = labels.find(el => visibleText(el).toLowerCase() === 'personal usage');
    if (!label) return null;

    const target = label.closest('button,a,[role="tab"],[role="button"]') || label;
    const selected =
      target.getAttribute('aria-selected') === 'true' ||
      target.getAttribute('data-state') === 'active' ||
      /\bactive\b|\bselected\b/.test(String(target.className || ''));
    if (selected) return 'waiting for personal usage cards';

    target.click();
    return 'selected personal usage tab';
  }

  function findCardByLabel(label) {
    const candidates = Array.from(document.querySelectorAll('article,section,[role="group"],div,li'))
      .map(el => {
        const text = visibleText(el);
        return { el, text };
      })
      .filter(({ text }) => {
        const lower = text.toLowerCase();
        return lower.includes(label.toLowerCase()) && /%/.test(text) && text.length < 1600;
      })
      .sort((a, b) => {
        const score = item =>
          (item.el.tagName.toLowerCase() === 'article' ? -1000 : 0) +
          (/reset/i.test(item.text) ? -100 : 0) +
          item.text.length;
        return score(a) - score(b);
      });
    if (candidates.length) return candidates[0].el;

    const headings = Array.from(document.querySelectorAll('h1,h2,h3,h4,div,span,p'));
    const heading = headings.find(el => {
      const t = visibleText(el);
      return t === label || t.toLowerCase() === label.toLowerCase();
    });
    if (!heading) return null;
    let card = heading;
    for (let i = 0; i < 6 && card.parentElement; i++) {
      card = card.parentElement;
      const txt = visibleText(card);
      if (/%/.test(txt)) return card;
    }
    return null;
  }

  function readCard(label, nextLabels) {
    const card = findCardByLabel(label);
    const text = card ? visibleText(card) : windowTextAfterLabel(label, nextLabels);
    if (!text) return null;
    const pctMatch = text.match(/(\d+(?:\.\d+)?)\s*%/);
    const remaining = /remaining/i.test(text);
    const used = /used/i.test(text);
    const resetMatch = text.match(/Resets?\s+(?:(?:at|on|in)\s+)?(.+?)(?=\s*$|\s+(?:Daily|Weekly|All|Current|Personal|Team|5 hour)\b|\s+\d+(?:\.\d+)?\s*%)/i);
    return {
      raw: text.slice(0, 400),
      percent: pctMatch ? parseFloat(pctMatch[1]) : null,
      kind: remaining ? 'remaining' : (used ? 'used' : 'unknown'),
      reset_text: resetMatch ? resetMatch[1].trim() : null,
    };
  }

  const bodyText = visibleText(document.body);
  const personalTabReason = maybeSelectPersonalUsageTab(bodyText);
  if (personalTabReason) {
    return {
      __retry_after_ms: 1200,
      __retry_reason: personalTabReason,
      logged_out: false,
      session: null,
      weekly: null,
      url: location.href,
      title: document.title,
      body_text: bodyText.slice(0, 2000),
    };
  }

  const lowerText = bodyText.toLowerCase();
  const isLoggedOut =
    !!document.querySelector('a[href*="/auth/login"], a[href*="/login"]') ||
    location.pathname.includes('/auth/login') ||
    location.pathname === '/login' ||
    document.title.toLowerCase().includes('login') ||
    (/log in|sign in/.test(lowerText) && !/usage limit/i.test(bodyText));
  return {
    logged_out: isLoggedOut,
    session: readCard('5 hour usage limit', ['Weekly usage limit']),
    weekly: readCard('Weekly usage limit', ['Personal usage', 'Team usage']),
    url: location.href,
    title: document.title,
    has_percent_text: /%/.test(bodyText),
    has_usage_text: /usage limit/i.test(bodyText),
    body_text: bodyText.slice(0, 2000),
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
    text = re.sub(r"^at\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+at\s+", " ", text, count=1, flags=re.IGNORECASE)
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


def _is_codex_analytics_url(url: str) -> bool:
    normalized = url.split("?", maxsplit=1)[0].split("#", maxsplit=1)[0]
    return normalized == CODEX_ANALYTICS_URL


def _payload_has_usage_signal(payload: dict[str, Any]) -> bool:
    if bool(payload.get("has_percent_text")) or bool(payload.get("has_usage_text")):
        return True
    page_text = f"{payload.get('title', '')} {payload.get('body_text', '')}".lower()
    return "%" in page_text or "usage limit" in page_text or "usage" in page_text


def _parse_body_card(
    body_text: str,
    label: str,
    next_labels: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    text = re.sub(r"\s+", " ", body_text or "").strip()
    lower_text = text.lower()
    start = lower_text.find(label.lower())
    if start < 0:
        return None

    end = len(text)
    for next_label in next_labels:
        candidate = lower_text.find(next_label.lower(), start + len(label))
        if candidate >= 0:
            end = min(end, candidate)
    window = text[start:end].strip()
    pct_match = re.search(r"(\d+(?:\.\d+)?)\s*%", window)
    if not pct_match:
        return None

    reset_match = re.search(
        r"Resets?\s+(?:(?:at|on|in)\s+)?(.+?)(?=\s*$|\s+(?:Daily|Weekly|All|Current|Personal|Team|5 hour)\b|\s+\d+(?:\.\d+)?\s*%)",
        window,
        re.IGNORECASE,
    )
    remaining = re.search(r"\bremaining\b", window, re.IGNORECASE)
    used = re.search(r"\bused\b", window, re.IGNORECASE)
    return {
        "raw": window[:400],
        "percent": float(pct_match.group(1)),
        "kind": "remaining" if remaining else ("used" if used else "unknown"),
        "reset_text": reset_match.group(1).strip() if reset_match else None,
    }


def _looks_like_empty_signed_in_usage(payload: dict[str, Any]) -> bool:
    url = str(payload.get("url") or "")
    if not _is_codex_analytics_url(url):
        return False
    if _payload_has_usage_signal(payload):
        return False
    body_text = str(payload.get("body_text") or "").lower()
    return any(marker in body_text for marker in ("codex", "chatgpt", "tasks", "cloud"))


def _is_logged_out_payload(payload: dict[str, Any]) -> bool:
    url = str(payload.get("url") or "").lower()
    return (
        bool(payload.get("logged_out"))
        or "/auth/login" in url
        or "/login" in url
        or "/logout" in url
    )


def _build_snapshot(
    payload: dict[str, Any],
    *,
    account_id: str = "codex",
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
            error="Not signed in to ChatGPT.",
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
            error="ChatGPT security verification required. Click Connect and complete the browser check.",
            raw=payload,
        )

    metrics: list[UsageMetric] = []
    body_text = str(payload.get("body_text") or "")
    for key, label, source_label, next_labels in (
        ("session", "Session", "5 hour usage limit", ("Weekly usage limit",)),
        ("weekly", "Weekly", "Weekly usage limit", ("Personal usage", "Team usage")),
    ):
        card = payload.get(key) or _parse_body_card(
            body_text,
            source_label,
            next_labels,
        )
        if not card:
            continue
        percent = normalize_percent(card.get("percent"), card.get("kind", ""))
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
                window=reset_window,
            )
        )

    if not metrics or all(m.percent_used is None for m in metrics):
        if _looks_like_empty_signed_in_usage(payload):
            log_page_diagnosis(
                log,
                provider=account_id,
                classification="empty_signed_in_usage",
                payload=payload,
                expected_rows=_EXPECTED_ROWS,
            )
            return UsageSnapshot(
                provider=account_id,
                status=SnapshotStatus.OK,
                metrics=idle_session_weekly_metrics(),
                raw=payload,
            )
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


class CodexProvider(Provider):
    name = "codex"
    display_name = "Codex"

    def __init__(self, parent: QObject | None = None, account_id: str = "codex"):
        self._parent = parent
        self._account_id = account_id
        self._runner: ScrapeRunner | None = None  # held to prevent GC

    def refresh(self, on_done: Callable[[UsageSnapshot], None]) -> None:
        def _build(payload: dict[str, Any]) -> UsageSnapshot:
            return _build_snapshot(payload, account_id=self._account_id)

        self._runner = ScrapeRunner(
            account_id=self._account_id,
            url=CODEX_USAGE_URL,
            extractor_js=EXTRACTOR_JS,
            build=_build,
            log=log,
            wait_ms=5000,
            transport_max_attempts=1,
            build_max_attempts=2,
            parent=self._parent,
        )
        self._runner.run(on_done)
