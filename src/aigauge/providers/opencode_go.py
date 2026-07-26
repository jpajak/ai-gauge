from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Callable

from PyQt6.QtCore import QObject

from ..config import OPENCODE_GO_USAGE_URL, Config, browser_account
from ..models import SnapshotStatus, UsageMetric, UsageSnapshot
from ._common import is_security_verification_page
from ._scrape_runner import ScrapeRunner
from .base import Provider
from .diagnostics import log_page_diagnosis

_EXPECTED_ROWS = ("rolling", "weekly", "monthly")
log = logging.getLogger("aigauge.providers.opencode_go")

EXTRACTOR_JS = r"""
(() => {
  function visibleText(el) {
    return ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
  }

  function readItem(item) {
    const label = visibleText(item.querySelector('[data-slot="usage-label"]'));
    const value = visibleText(item.querySelector('[data-slot="usage-value"]'));
    const reset = visibleText(item.querySelector('[data-slot="reset-time"]'));
    const bar = item.querySelector('[data-slot="progress-bar"]');
    const width = bar && bar.style ? bar.style.width : '';
    const percentMatch = (value || width || visibleText(item)).match(/(\d+(?:\.\d+)?)\s*%/);
    return {
      label,
      percent: percentMatch ? parseFloat(percentMatch[1]) : null,
      reset_text: reset || null,
      raw: visibleText(item).slice(0, 400),
    };
  }

  const bodyText = visibleText(document.body);
  const lowerText = bodyText.toLowerCase();
  const items = Array.from(document.querySelectorAll('[data-slot="usage-item"]'));
  const loggedOut =
    location.pathname.includes('/login') ||
    location.pathname.includes('/auth') ||
    document.title.toLowerCase().includes('login') ||
    (/\b(log in|sign in)\b/i.test(bodyText) && !/usage/i.test(bodyText));

  return {
    logged_out: loggedOut,
    usage: items.map(readItem),
    url: location.href,
    title: document.title,
    has_usage_text: /rolling usage|weekly usage|monthly usage/i.test(bodyText),
    has_percent_text: /\d+(?:\.\d+)?\s*%/.test(bodyText),
    body_text: bodyText.slice(0, 2000),
  };
})();
"""


def usage_url(
    config: Config | None = None,
    account_id: str = "opencode_go",
) -> str:
    value = ""
    if config is not None:
        account = browser_account(config, account_id)
        if account is not None:
            value = str(account.usage_url or "").strip()
        if not value:
            value = str(
                getattr(getattr(config, "opencode_go", None), "usage_url", "") or ""
            ).strip()
    return value or OPENCODE_GO_USAGE_URL


def _parse_reset_text(text: str | None) -> datetime | None:
    if not text:
        return None
    text = re.sub(r"^\s*resets?\s+in\s+", "", text.strip(), flags=re.IGNORECASE)
    units = {
        "day": "days",
        "days": "days",
        "hour": "hours",
        "hours": "hours",
        "hr": "hours",
        "hrs": "hours",
        "h": "hours",
        "minute": "minutes",
        "minutes": "minutes",
        "min": "minutes",
        "mins": "minutes",
        "m": "minutes",
    }
    values = {"days": 0, "hours": 0, "minutes": 0}
    for amount, unit in re.findall(r"(\d+)\s*(days?|hours?|hrs?|h|minutes?|mins?|m)\b", text, re.IGNORECASE):
        values[units[unit.lower()]] += int(amount)
    if any(values.values()):
        return datetime.now() + timedelta(**values)
    return None


def _metric_label(label: str) -> str:
    label = re.sub(r"\s+usage\b", "", label.strip(), flags=re.IGNORECASE)
    return label.title()


def _window_for(label: str) -> timedelta | None:
    key = label.lower()
    if key == "rolling":
        return timedelta(hours=5)
    if key == "weekly":
        return timedelta(days=7)
    if key == "monthly":
        return timedelta(days=30)
    return None


def _parse_body_usage(body_text: str) -> list[dict[str, Any]]:
    text = re.sub(r"\s+", " ", body_text or "").strip()
    rows: list[dict[str, Any]] = []
    pattern = re.compile(
        r"\b(Rolling|Weekly|Monthly)\s+Usage\b\s*(\d+(?:\.\d+)?)\s*%"
        r"(?:\s*Resets?\s+in\s+(.+?))?"
        r"(?=\s+\b(?:Rolling|Weekly|Monthly)\s+Usage\b|$)",
        re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        reset = match.group(3).strip() if match.group(3) else None
        rows.append(
            {
                "label": f"{match.group(1).title()} Usage",
                "percent": float(match.group(2)),
                "reset_text": f"Resets in {reset}" if reset else None,
                "raw": match.group(0)[:400],
            }
        )
    return rows


def _is_logged_out_payload(payload: dict[str, Any]) -> bool:
    url = str(payload.get("url") or "").lower()
    if "/login" in url or "/auth" in url:
        return True
    if bool(payload.get("logged_out")):
        return not bool(payload.get("usage"))
    return False


def _build_snapshot(
    payload: dict[str, Any],
    *,
    account_id: str = "opencode_go",
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
            error="Not signed in to OpenCode.",
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
            error="OpenCode security verification required. Click Sign in and complete the browser check.",
            raw=payload,
        )

    usage = payload.get("usage")
    rows = usage if isinstance(usage, list) else []
    if not rows:
        rows = _parse_body_usage(str(payload.get("body_text") or ""))

    metrics: list[UsageMetric] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        label_text = str(row.get("label") or "").strip()
        percent = row.get("percent")
        if not label_text or percent is None:
            continue
        label = _metric_label(label_text)
        key = label.lower()
        if key not in _EXPECTED_ROWS or key in seen:
            continue
        seen.add(key)
        reset_text = str(row.get("reset_text") or "").strip() or None
        metrics.append(
            UsageMetric(
                label=label,
                percent_used=float(percent),
                resets_at=_parse_reset_text(reset_text),
                note=reset_text,
                window=_window_for(label),
            )
        )

    if not metrics:
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
            error="Could not read OpenCode usage from page (layout may have changed).",
            raw=payload,
        )

    return UsageSnapshot(
        provider=account_id,
        status=SnapshotStatus.OK,
        metrics=metrics,
        raw=payload,
    )


class OpenCodeGoProvider(Provider):
    name = "opencode_go"
    display_name = "OpenCode"

    def __init__(
        self,
        config: Config,
        parent: QObject | None = None,
        account_id: str = "opencode_go",
    ):
        self._parent = parent
        self._config = config
        self._account_id = account_id
        self._runner: ScrapeRunner | None = None

    def refresh(self, on_done: Callable[[UsageSnapshot], None]) -> None:
        def _build(payload: dict[str, Any]) -> UsageSnapshot:
            return _build_snapshot(payload, account_id=self._account_id)

        self._runner = ScrapeRunner(
            account_id=self._account_id,
            url=usage_url(self._config, self._account_id),
            extractor_js=EXTRACTOR_JS,
            build=_build,
            log=log,
            wait_ms=5000,
            transport_max_attempts=1,
            build_max_attempts=2,
            parent=self._parent,
        )
        self._runner.run(on_done)