from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Callable

from PyQt6.QtCore import QObject

from ..models import SnapshotStatus, UsageMetric, UsageSnapshot
from ._common import (
    has_usage_page_signal,
    is_security_verification_page,
    normalize_percent,
)
from ._scrape_runner import ScrapeRunner
from .base import Provider
from .diagnostics import log_page_diagnosis
from .idle import idle_reset_state

CODEX_USAGE_URL = "https://chatgpt.com/settings/usage?tab=overview"
_EXPECTED_ROWS = ("session", "weekly")
_LABEL_FREE_NOTE = "Reset time unavailable: the page was read without its labels."
log = logging.getLogger("aigauge.providers.codex")

# Read only the Plan limits section: credits and purchased resets can contain
# unrelated percentages and repeat the limit names.
EXTRACTOR_JS = r"""
(() => {
  function visibleText(el) {
    return ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
  }

  function windowTextAfterLabel(label, nextLabels) {
    const lowerText = limitsText.toLowerCase();
    const start = lowerText.indexOf(label.toLowerCase());
    if (start < 0) return '';
    let end = limitsText.length;
    for (const nextLabel of nextLabels) {
      const candidate = lowerText.indexOf(nextLabel.toLowerCase(), start + label.length);
      if (candidate >= 0 && candidate < end) end = candidate;
    }
    return limitsText.slice(start, end).trim();
  }

  function maybeSelectOverviewTab() {
    // "N% left" is a current limit. A bare percent also appears in the
    // Analytics history table ("Weekly limits ... 58.7%").
    if (/Weekly limit/i.test(limitsText) && /\d+(?:\.\d+)?\s*%\s*left\b/i.test(limitsText)) {
      return null;
    }

    const labels = Array.from(document.querySelectorAll('button,a,[role="tab"],[role="button"]'));
    const label = labels.find(el => visibleText(el).toLowerCase() === 'overview');
    if (!label) return null;

    const target = label.closest('button,a,[role="tab"],[role="button"]') || label;
    // The live tabs are plain buttons that carry only aria-pressed.
    const selected =
      target.getAttribute('aria-pressed') === 'true' ||
      target.getAttribute('aria-selected') === 'true' ||
      target.getAttribute('data-state') === 'active' ||
      /\bactive\b|\bselected\b/.test(String(target.className || ''));
    if (selected) return 'waiting for plan limits';

    target.click();
    return 'selected overview tab';
  }

  function readCard(label, nextLabels) {
    const text = windowTextAfterLabel(label, nextLabels);
    if (!text) return null;
    const pctMatch = text.match(/(\d+(?:\.\d+)?)\s*%\s*left\b/i);
    if (!pctMatch) return null;
    const resetMatch = text.match(/Resets?\s+(?:(?:at|on|in)\s+)?(.+?)(?=\s*$|\s+\d+(?:\.\d+)?\s*%)/i);
    return {
      raw: text.slice(0, 400),
      percent: parseFloat(pctMatch[1]),
      kind: 'remaining',
      reset_text: resetMatch ? resetMatch[1].trim() : null,
    };
  }

  function retryPayload(reason) {
    return {
      __retry_after_ms: 1200,
      __retry_reason: reason,
      logged_out: false,
      session: null,
      weekly: null,
      url: location.href,
      title: document.title,
      body_text: bodyText.slice(0, 2000),
    };
  }

  function numericAttribute(el, name) {
    // Read the attribute, not the property: an indeterminate <progress> has
    // no value attribute, yet its .value property reads 0.
    const raw = el.getAttribute(name);
    if (raw === null || String(raw).trim() === '') return null;
    const number = Number(raw);
    return Number.isFinite(number) ? number : null;
  }

  // Each limit card holds a <progress max="100"> whose value is the percent
  // remaining, whatever language the labels are in. Any such bar without a
  // usable value means the cards are still loading, so nothing is read.
  function remainingBarValues() {
    const values = Array.from(document.querySelectorAll('progress'))
      .filter(el => numericAttribute(el, 'max') === 100)
      .map(el => numericAttribute(el, 'value'));
    return values.every(value => value !== null && value >= 0 && value <= 100) ? values : [];
  }

  function barCard(value) {
    return {raw: 'progress value=' + value, percent: value, kind: 'remaining', reset_text: null};
  }

  const bodyText = visibleText(document.body);
  const limitsText = bodyText.split(/\bPlan limits\b/i).pop()
    .split(/\bCredits\b|\bUsage limit resets\b|\bCredit usage history\b/i)[0];
  const overviewReason = maybeSelectOverviewTab();
  if (overviewReason) return retryPayload(overviewReason);

  const lowerText = bodyText.toLowerCase();
  const isLoggedOut =
    !!document.querySelector('a[href*="/auth/login"], a[href*="/login"]') ||
    location.pathname.includes('/auth/login') ||
    location.pathname === '/login' ||
    document.title.toLowerCase().includes('login') ||
    (/log in|sign in/.test(lowerText) && !/Plan limits/i.test(bodyText));
  let session = readCard('5-hour limit', ['Weekly limit']);
  let weekly = readCard('Weekly limit', ['5-hour limit']);

  // Label-free fallback, only when no label was readable. Two bars are the
  // 5-hour and weekly cards in DOM order. A single bar is a weekly-only plan
  // or a half-rendered page, so it has to be seen on two consecutive runs.
  let labelFree = false;
  if (!session && !weekly && !isLoggedOut) {
    const bars = remainingBarValues();
    const asked = window.__aigaugeCodexSingleBarAsked === true;
    window.__aigaugeCodexSingleBarAsked = bars.length === 1;
    if (bars.length === 2) {
      session = barCard(bars[0]);
      weekly = barCard(bars[1]);
      labelFree = true;
    } else if (bars.length === 1) {
      if (!asked) return retryPayload('confirming single usage bar');
      weekly = barCard(bars[0]);
      labelFree = true;
    }
  }

  const payload = {
    logged_out: isLoggedOut,
    session: session,
    weekly: weekly,
    url: location.href,
    title: document.title,
    has_percent_text: /%/.test(bodyText),
    has_usage_text: /Plan limits/i.test(bodyText),
    // Computed from the full text: body_text below is cut to 2,000 characters.
    has_plan_limits: /\bPlan limits\b/i.test(bodyText),
    has_five_hour_heading: /5-hour limit/i.test(limitsText),
    body_text: bodyText.slice(0, 2000),
  };
  if (labelFree) payload.label_free = true;
  return payload;
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
    """Parse a relative countdown or an absolute reset time."""
    if not text:
        return None
    text = text.strip().rstrip(".")
    text = re.sub(r"^at\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+at\s+", " ", text, count=1, flags=re.IGNORECASE)
    now = datetime.now()

    # Overview renders day-aware countdowns such as "6d 10h" and "5h 0m".
    rel = re.fullmatch(
        r"(?:in\s+)?(?:(\d+)\s*(?:days?|d))?\s*"
        r"(?:(\d+)\s*(?:hours?|hrs?|h))?\s*"
        r"(?:(\d+)\s*(?:minutes?|mins?|m))?\s*"
        r"(?:(\d+)\s*(?:seconds?|secs?|s))?",
        text,
        re.IGNORECASE,
    )
    if rel and any(group is not None for group in rel.groups()):
        days, hours, minutes, seconds = (int(group or 0) for group in rel.groups())
        return now + timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)

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


def _is_codex_usage_url(url: str) -> bool:
    normalized = url.split("?", maxsplit=1)[0].split("#", maxsplit=1)[0]
    return normalized == CODEX_USAGE_URL.split("?", maxsplit=1)[0]


def _payload_has_usage_signal(payload: dict[str, Any]) -> bool:
    if bool(payload.get("has_percent_text")) or bool(payload.get("has_usage_text")):
        return True
    page_text = f"{payload.get('title', '')} {payload.get('body_text', '')}".lower()
    return "%" in page_text or "usage limit" in page_text or "usage" in page_text


def _plan_limits_text(body_text: str) -> str:
    text = re.sub(r"\s+", " ", body_text or "").strip()
    text = re.split(r"\bPlan limits\b", text, flags=re.IGNORECASE)[-1]
    return re.split(
        r"\bCredits\b|\bUsage limit resets\b|\bCredit usage history\b",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]


def _parse_body_card(
    body_text: str,
    label: str,
    next_labels: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    text = _plan_limits_text(body_text)
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
    pct_match = re.search(r"(\d+(?:\.\d+)?)\s*%\s*left\b", window, re.IGNORECASE)
    if not pct_match:
        return None

    reset_match = re.search(
        r"Resets?\s+(?:(?:at|on|in)\s+)?(.+?)(?=\s*$|\s+\d+(?:\.\d+)?\s*%)",
        window,
        re.IGNORECASE,
    )
    return {
        "raw": window[:400],
        "percent": float(pct_match.group(1)),
        "kind": "remaining",
        "reset_text": reset_match.group(1).strip() if reset_match else None,
    }


def _looks_like_empty_signed_in_usage(payload: dict[str, Any]) -> bool:
    url = str(payload.get("url") or "")
    if not _is_codex_usage_url(url):
        return False
    if _payload_has_usage_signal(payload):
        return False
    body_text = str(payload.get("body_text") or "").lower()
    return any(marker in body_text for marker in ("codex", "chatgpt", "tasks", "cloud"))


def _is_weekly_only_usage_layout(payload: dict[str, Any]) -> bool:
    """Some plans have a weekly limit without a five-hour limit.

    The extractor reports what it saw in the whole Plan limits section.
    ``body_text`` is cut to 2,000 characters, so it only serves payloads that
    carry no flags.
    """
    if "has_plan_limits" in payload and "has_five_hour_heading" in payload:
        return bool(payload["has_plan_limits"]) and not bool(
            payload["has_five_hour_heading"]
        )
    text = re.sub(r"\s+", " ", str(payload.get("body_text") or "")).lower()
    limits_text = _plan_limits_text(text)
    return (
        "plan limits" in text
        and "weekly limit" in limits_text
        and "5-hour limit" not in limits_text
    )


def _logged_out_kind(payload: dict[str, Any]) -> str | None:
    """How the payload says the session is signed out.

    ``"url"``: the page is a login or logout URL, which is conclusive.
    ``"page"``: only the extractor's ``logged_out`` flag says so, while the URL
    is still the usage page. A page that had not finished rendering can set it,
    so it needs a second load to be believed. ``None``: not signed out.
    """
    url = str(payload.get("url") or "").lower()
    if "/auth/login" in url or "/login" in url or "/logout" in url:
        return "url"
    if bool(payload.get("logged_out")) and not (
        has_usage_page_signal(payload) or _looks_like_empty_signed_in_usage(payload)
    ):
        return "page"
    return None


def _is_logged_out_payload(payload: dict[str, Any]) -> bool:
    return _logged_out_kind(payload) is not None


def _build_snapshot(
    payload: dict[str, Any],
    *,
    account_id: str = "codex",
    confirm_soft_signout: bool = False,
) -> UsageSnapshot:
    """Classify a scraped payload.

    With ``confirm_soft_signout`` set, a sign-out seen only through the page's
    ``logged_out`` flag becomes a retryable ERROR snapshot instead of
    AUTH_REQUIRED, so the caller can reload once before believing it.
    """
    signout = _logged_out_kind(payload)
    if signout == "page" and confirm_soft_signout:
        log.info(
            "codex sign-out unconfirmed, reloading account=%s title=%r",
            account_id,
            payload.get("title"),
        )
        return UsageSnapshot(
            provider=account_id,
            status=SnapshotStatus.ERROR,
            error="Codex page had not finished loading; reloading.",
            raw=payload,
        )
    if signout is not None:
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
    # Set by the extractor when it read the cards' progress bars because no
    # label was readable (a page in another language, or reworded labels).
    label_free = bool(payload.get("label_free"))
    for key, label, source_label, next_labels in (
        ("session", "Session", "5-hour limit", ("Weekly limit",)),
        ("weekly", "Weekly", "Weekly limit", ("5-hour limit",)),
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
        if label_free:
            # No reset text was looked for, so its absence says nothing about
            # an idle window: show the percent alone and say why.
            resets_at, reset_label, note = None, None, _LABEL_FREE_NOTE
        else:
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

    labels = {metric.label.lower(): metric for metric in metrics}
    # A five-hour heading without data means the Overview is still loading.
    # The label-free extractor confirms a lone bar itself before reporting it.
    weekly_only_layout = set(labels) == {"weekly"} and (
        label_free or _is_weekly_only_usage_layout(payload)
    )
    if metrics and set(labels) != {"session", "weekly"} and not weekly_only_layout:
        log_page_diagnosis(
            log,
            provider=account_id,
            classification="partial_usage_rows",
            payload=payload,
            expected_rows=_EXPECTED_ROWS,
            level=logging.WARNING,
        )
        return UsageSnapshot(
            provider=account_id,
            status=SnapshotStatus.ERROR,
            error="Codex usage page only rendered part of the usage cards; retrying.",
            raw=payload,
        )

    if not metrics or all(m.percent_used is None for m in metrics):
        if _looks_like_empty_signed_in_usage(payload):
            log_page_diagnosis(
                log,
                provider=account_id,
                classification="empty_signed_in_usage",
                payload=payload,
                expected_rows=_EXPECTED_ROWS,
                level=logging.WARNING,
            )
            return UsageSnapshot(
                provider=account_id,
                status=SnapshotStatus.ERROR,
                error="ChatGPT usage Overview loaded without plan limits; retrying.",
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

    if label_free:
        # Keep a real layout change visible in the log while the tile works.
        log_page_diagnosis(
            log,
            provider=account_id,
            classification="label_free_fallback",
            payload=payload,
            expected_rows=_EXPECTED_ROWS,
        )
    return UsageSnapshot(
        provider=account_id,
        status=SnapshotStatus.OK,
        metrics=metrics,
        raw=payload,
    )


_BUILD_MAX_ATTEMPTS = 2


class CodexProvider(Provider):
    name = "codex"
    display_name = "Codex"

    def __init__(self, parent: QObject | None = None, account_id: str = "codex"):
        self._parent = parent
        self._account_id = account_id
        self._runner: ScrapeRunner | None = None  # held to prevent GC

    def refresh(self, on_done: Callable[[UsageSnapshot], None]) -> None:
        # Per refresh call, so each refresh starts from the first attempt.
        # The runner reloads only when a build returns ERROR, and the first
        # attempt turns a soft sign-out into one.
        attempts = [0]

        def _build(payload: dict[str, Any]) -> UsageSnapshot:
            attempts[0] += 1
            return _build_snapshot(
                payload,
                account_id=self._account_id,
                confirm_soft_signout=attempts[0] < _BUILD_MAX_ATTEMPTS,
            )

        cache_buster = int(datetime.now().timestamp())
        self._runner = ScrapeRunner(
            account_id=self._account_id,
            url=f"{CODEX_USAGE_URL}&aigauge_ts={cache_buster}",
            extractor_js=EXTRACTOR_JS,
            build=_build,
            log=log,
            wait_ms=7000,
            transport_max_attempts=1,
            build_max_attempts=_BUILD_MAX_ATTEMPTS,
            parent=self._parent,
        )
        self._runner.run(on_done)
