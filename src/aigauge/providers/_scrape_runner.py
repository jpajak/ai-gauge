from __future__ import annotations

import logging
from typing import Any, Callable

from PyQt6.QtCore import QObject

from ..models import SnapshotStatus, UsageSnapshot
from ..webview import runtime as webengine

# Resolved on first scrape rather than imported at module scope: ..webview
# .scraper pulls in QtWebEngine, and importing Chromium aborts on a session
# with no GL context (issue #7). Kept as a module attribute so it remains
# patchable.
HeadlessScraper = None


def _scraper_class():
    global HeadlessScraper
    if HeadlessScraper is None:
        from ..webview.scraper import HeadlessScraper as _resolved

        HeadlessScraper = _resolved
    return HeadlessScraper


class ScrapeRunner:
    """Drives a HeadlessScraper and feeds the payload to a builder.

    Retries the whole scrape when the builder returns an ERROR snapshot, up
    to ``build_max_attempts`` times. Holds the active scraper reference so
    the caller (a Provider) only needs to hold the runner.

    Closures over locals are used for the signal handler rather than a bound
    method on ``self`` — PyQt6's frozen Windows build was observed to drop
    bound-method temporaries on non-QObject receivers, silently breaking the
    ``done`` signal.
    """

    def __init__(
        self,
        *,
        account_id: str,
        url: str,
        extractor_js: str,
        build: Callable[[dict[str, Any]], UsageSnapshot],
        log: logging.Logger,
        wait_ms: int = 5000,
        transport_max_attempts: int = 1,
        build_max_attempts: int = 2,
        parent: QObject | None = None,
    ):
        self._account_id = account_id
        self._url = url
        self._extractor_js = extractor_js
        self._build = build
        self._log = log
        self._wait_ms = wait_ms
        self._transport_max_attempts = max(1, transport_max_attempts)
        self._build_max_attempts = max(1, build_max_attempts)
        self._parent = parent
        self._scraper: Any | None = None

    def run(self, on_done: Callable[[UsageSnapshot], None]) -> None:
        if not webengine.is_available():
            # No GL context in this session, so Chromium can't start. Report it
            # on the tile instead of taking the whole app down (issue #7).
            self._log.warning(
                "provider needs a browser but WebEngine is unavailable "
                "provider=%s reason=%s",
                self._account_id,
                webengine.unavailable_reason(),
            )
            on_done(
                UsageSnapshot(
                    provider=self._account_id,
                    status=SnapshotStatus.ERROR,
                    error=(
                        "needs an embedded browser, which this session can't "
                        f"start — {webengine.unavailable_reason()} "
                        # The probe reports whether *Qt* can get a context.
                        # Chromium sometimes finds one where Qt cannot, so
                        # point at the override rather than leaving a dead end.
                        "If this machine does have working OpenGL, set "
                        "AIGAUGE_FORCE_WEBENGINE=1 to skip this check."
                    ),
                )
            )
            return

        scraper_class = _scraper_class()
        attempts = [0]

        def _handle(result: Any, error: str) -> None:
            self._scraper = None
            if error or not isinstance(result, dict):
                snapshot = UsageSnapshot(
                    provider=self._account_id,
                    status=SnapshotStatus.ERROR,
                    error=error or "no data extracted",
                )
                self._log.warning(
                    "provider snapshot error provider=%s reason=%s",
                    self._account_id,
                    snapshot.error,
                )
                on_done(snapshot)
                return

            snapshot = self._build(result)
            if (
                snapshot.status == SnapshotStatus.ERROR
                and attempts[0] < self._build_max_attempts
            ):
                self._log.warning(
                    "provider transient error provider=%s attempt=%s reason=%s — retrying",
                    self._account_id,
                    attempts[0],
                    snapshot.error,
                )
                _start_scrape()
                return

            if snapshot.status == SnapshotStatus.ERROR:
                self._log.warning(
                    "provider snapshot error provider=%s reason=%s",
                    self._account_id,
                    snapshot.error,
                )
            on_done(snapshot)

        def _start_scrape() -> None:
            attempts[0] += 1
            self._scraper = scraper_class(
                provider=self._account_id,
                url=self._url,
                extractor_js=self._extractor_js,
                wait_ms=self._wait_ms,
                max_attempts=self._transport_max_attempts,
                parent=self._parent,
            )
            self._scraper.done.connect(_handle)

        _start_scrape()
