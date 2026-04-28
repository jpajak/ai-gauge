from __future__ import annotations

import logging
import time
from typing import Any, Callable

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtWebEngineWidgets import QWebEngineView

from .page import QuietWebEnginePage
from .profile import get_profile

log = logging.getLogger("usage_view.scraper")


class HeadlessScraper(QObject):
    """Load a page in an offscreen QWebEngineView, then evaluate JS to extract data.

    Lives on the GUI thread (QtWebEngine is GUI-thread-only). The owner keeps a
    reference until `done` fires; the callback delivers either the JS result or
    an error string.
    """

    done = pyqtSignal(object, str)  # (result_or_None, error_or_empty_string)

    def __init__(
        self,
        provider: str,
        url: str,
        extractor_js: str,
        wait_ms: int = 4000,
        timeout_ms: int = 25000,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._provider = provider
        self._url = url
        self._extractor_js = extractor_js
        self._wait_ms = wait_ms
        self._finished = False
        self._started_at = time.monotonic()

        profile = get_profile(provider)
        self._page = QuietWebEnginePage(profile, self)
        self._view = QWebEngineView()
        self._view.setPage(self._page)
        # Offscreen — never .show()
        self._view.resize(1280, 900)

        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(lambda: self._finish(None, "timeout"))
        self._timeout.start(timeout_ms)

        self._page.loadFinished.connect(self._on_load_finished)
        self._page.load(QUrl(url))
        log.info("scrape start provider=%s url=%s", provider, url)

    def _on_load_finished(self, ok: bool) -> None:
        if self._finished:
            return
        if not ok:
            self._finish(None, "page failed to load")
            return
        # Page DOM may render asynchronously — give React a moment, then evaluate.
        QTimer.singleShot(self._wait_ms, self._run_extractor)

    def _run_extractor(self) -> None:
        if self._finished:
            return
        self._page.runJavaScript(self._extractor_js, self._on_js_result)

    def _on_js_result(self, result: Any) -> None:
        if self._finished:
            return
        if result is None:
            self._finish(None, "extractor returned null")
            return
        self._finish(result, "")

    def _finish(self, result: Any, error: str) -> None:
        if self._finished:
            return
        self._finished = True
        self._timeout.stop()
        elapsed = time.monotonic() - self._started_at
        if error:
            log.warning(
                "scrape fail provider=%s url=%s elapsed=%.1fs error=%s",
                self._provider, self._url, elapsed, error,
            )
        else:
            log.info(
                "scrape ok provider=%s elapsed=%.1fs result_keys=%s",
                self._provider,
                elapsed,
                sorted(result.keys()) if isinstance(result, dict) else type(result).__name__,
            )
        self.done.emit(result, error)
        # Release Chromium resources after connected callbacks have had a
        # chance to clear their Python references.
        QTimer.singleShot(0, self._cleanup)

    def _cleanup(self) -> None:
        try:
            self._page.loadFinished.disconnect(self._on_load_finished)
        except (TypeError, RuntimeError):
            pass
        self._view.stop()
        self._view.setPage(None)
        try:
            self._page.setLifecycleState(self._page.LifecycleState.Discarded)
        except RuntimeError:
            pass
        self._view.deleteLater()
        self._page.deleteLater()
        self.deleteLater()


def scrape(
    provider: str,
    url: str,
    extractor_js: str,
    on_done: Callable[[Any, str], None],
    parent: QObject | None = None,
    wait_ms: int = 4000,
) -> HeadlessScraper:
    """Convenience wrapper. Returns the scraper so the caller can keep a reference."""
    scraper = HeadlessScraper(provider, url, extractor_js, wait_ms=wait_ms, parent=parent)
    scraper.done.connect(on_done)
    return scraper
