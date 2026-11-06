from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable
from urllib.parse import urlparse, urlunparse

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtWebEngineWidgets import QWebEngineView

from ..providers.registry import account_family
from .page import QuietWebEnginePage
from .profile import get_profile

log = logging.getLogger("aigauge.scraper")

# Linger-mode scrapers outlive every caller's reference: the runner drops its
# own as soon as `done` fires, and the Qt parent may be None. Hold them here
# until `_cleanup` runs.
_LINGERING: set[Any] = set()

# Time to let pagehide / visibilitychange handlers and queued beacons run after
# navigating away, before the page is hidden and discarded.
_NAVIGATE_AWAY_MS = 3000
# Give up waiting for the end-of-hold probe if the renderer never answers.
_PROBE_TIMEOUT_MS = 2000

_VISIBILITY_JS = "document.visibilityState"
_END_PROBE_JS = (
    "({visibility: document.visibilityState, event_logging_requests: "
    "performance.getEntriesByType('resource')"
    ".filter(e => e.name.includes('/api/event_logging')).length})"
)


def _enum_name(value: Any) -> str:
    return str(getattr(value, "name", value))


def _call_or_empty(obj: Any, name: str) -> Any:
    func = getattr(obj, name, None)
    if not callable(func):
        return ""
    try:
        return func()
    except RuntimeError:
        return ""


def _url_to_string(value: Any) -> str:
    to_string = getattr(value, "toString", None)
    if callable(to_string):
        return str(to_string())
    return str(value or "")


def _safe_url(value: Any) -> str:
    raw = _url_to_string(value)
    if not raw:
        return ""
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw.split("?", maxsplit=1)[0].split("#", maxsplit=1)[0][:300]
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))[:300]


def _claude_auth_redirect(
    value: Any, provider: str, title: str = ""
) -> dict[str, Any] | None:
    """Classify Claude's auth redirect even when the page load/JS failed."""
    if account_family(provider) != "claude":
        return None
    url = _safe_url(value)
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "claude.ai":
        return None
    auth_route = parsed.path.rstrip("/") in ("/login", "/logout")
    sign_in_title = re.fullmatch(r"Sign in\s*[-–—]\s*Claude", title, re.IGNORECASE)
    if not auth_route and sign_in_title is None:
        return None
    return {"logged_out": True, "url": url}


class HeadlessScraper(QObject):
    """Load a page in an offscreen QWebEngineView, then evaluate JS to extract data.

    Lives on the GUI thread (QtWebEngine is GUI-thread-only). The owner keeps a
    reference until `done` fires; the callback delivers either the JS result or
    an error string.

    ``linger_ms > 0`` turns the check into a keep-alive visit: the page is made
    visible to the Page Visibility API, kept until ``linger_ms`` has passed
    since the scrape started, then navigated away so pagehide work runs. The
    result is still delivered as soon as it is extracted.
    """

    done = pyqtSignal(object, str)  # (result_or_None, error_or_empty_string)

    _RETRYABLE_ERRORS = (
        "timeout",
        "page failed to load",
        "extractor returned null",
        "extractor retry limit exceeded",
    )

    def __init__(
        self,
        provider: str,
        url: str,
        extractor_js: str,
        wait_ms: int = 4000,
        timeout_ms: int = 25000,
        max_attempts: int = 1,
        parent: QObject | None = None,
        linger_ms: int = 0,
    ):
        super().__init__(parent)
        self._provider = provider
        self._url = url
        self._extractor_js = extractor_js
        self._wait_ms = wait_ms
        self._timeout_ms = timeout_ms
        self._max_attempts = max(1, max_attempts)
        self._attempt = 0
        self._finished = False
        self._started_at = time.monotonic()
        self._last_load_status = ""
        self._last_load_url = ""
        self._last_load_error_code = ""
        self._last_load_error_domain = ""
        self._last_load_error_string = ""
        self._last_load_is_error_page = ""
        self._last_url = url
        self._url_change_count = 0
        self._max_progress = 0
        self._render_terminated = False
        self._extractor_reruns = 0
        self._linger_ms = max(0, linger_ms)
        self._made_visible = False
        self._navigated_away = False

        profile = get_profile(provider)
        self._page = QuietWebEnginePage(profile, self, provider=provider)
        self._view = QWebEngineView()
        self._view.setPage(self._page)
        # Offscreen — never .show()
        self._view.resize(1280, 900)
        if self._linger_ms > 0:
            # A page that has a view can have its visibility forwarded to the
            # view widget, which could pop the offscreen view up as a window.
            # Detach it so `setVisible` on the page never reaches the widget.
            self._view.setPage(None)
            _LINGERING.add(self)

        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(lambda: self._finish(None, "timeout"))

        self._page.loadFinished.connect(self._on_load_finished)
        self._page.loadProgress.connect(self._on_load_progress)
        self._page.urlChanged.connect(self._on_url_changed)
        self._page.renderProcessTerminated.connect(self._on_render_process_terminated)
        loading_changed = getattr(self._page, "loadingChanged", None)
        if loading_changed is not None:
            loading_changed.connect(self._on_loading_changed)

        log.info(
            "scrape start provider=%s url=%s profile=%s viewport=%sx%s "
            "wait_ms=%s timeout_ms=%s max_attempts=%s user_agent=%r",
            provider,
            _safe_url(url),
            profile.persistentStoragePath(),
            self._view.width(),
            self._view.height(),
            wait_ms,
            timeout_ms,
            self._max_attempts,
            profile.httpUserAgent(),
        )
        self._begin_attempt()

    def _begin_attempt(self) -> None:
        self._attempt += 1
        self._last_load_status = ""
        self._last_load_url = ""
        self._last_load_error_code = ""
        self._last_load_error_domain = ""
        self._last_load_error_string = ""
        self._last_load_is_error_page = ""
        self._last_url = self._url
        self._url_change_count = 0
        self._max_progress = 0
        self._render_terminated = False
        self._extractor_reruns = 0
        self._timeout.stop()
        self._timeout.start(self._timeout_ms)
        self._page.load(QUrl(self._url))
        if self._linger_ms > 0 and not self._made_visible:
            self._make_page_visible()

    def _make_page_visible(self) -> None:
        # Qt ignores setVisible on a page whose web contents does not exist
        # yet (isVisible stays False and the document reads "hidden"), and the
        # first load() creates them. So this is the earliest call that works.
        self._made_visible = True
        self._page.setVisible(True)
        self._page.setLifecycleState(self._page.LifecycleState.Active)

    def _stop_loading(self) -> None:
        if self._linger_ms > 0:
            # The view no longer holds the page, so stopping the view is a no-op.
            self._page.triggerAction(self._page.WebAction.Stop)
        else:
            self._view.stop()

    def _on_load_progress(self, progress: int) -> None:
        self._max_progress = max(self._max_progress, progress)

    def _on_url_changed(self, url: QUrl) -> None:
        safe = _safe_url(url)
        if safe and safe != _safe_url(self._last_url):
            self._last_url = safe
            self._url_change_count += 1
            log.info(
                "scrape url changed provider=%s url=%s changes=%s",
                self._provider,
                safe,
                self._url_change_count,
            )

    def _on_render_process_terminated(self, status: Any, exit_code: int) -> None:
        self._render_terminated = True
        log.warning(
            "scrape render process terminated provider=%s status=%s exit_code=%s "
            "url=%s title=%r progress=%s",
            self._provider,
            _enum_name(status),
            exit_code,
            _safe_url(self._page.url()),
            self._page.title(),
            self._max_progress,
        )

    def _on_loading_changed(self, info: Any) -> None:
        self._last_load_status = _enum_name(_call_or_empty(info, "status"))
        self._last_load_url = _safe_url(_call_or_empty(info, "url"))
        self._last_load_error_code = _call_or_empty(info, "errorCode")
        self._last_load_error_domain = _enum_name(_call_or_empty(info, "errorDomain"))
        self._last_load_error_string = str(_call_or_empty(info, "errorString") or "")
        self._last_load_is_error_page = _call_or_empty(info, "isErrorPage")
        if "fail" in self._last_load_status.lower() or self._last_load_error_string:
            log.warning(
                "scrape load event provider=%s status=%s url=%s error_code=%s "
                "error_domain=%s error_string=%r is_error_page=%s",
                self._provider,
                self._last_load_status,
                self._last_load_url,
                self._last_load_error_code,
                self._last_load_error_domain,
                self._last_load_error_string,
                self._last_load_is_error_page,
            )

    def _on_load_finished(self, ok: bool) -> None:
        if self._finished:
            return
        log.info(
            "scrape load finished provider=%s ok=%s url=%s title=%r progress=%s "
            "load_status=%s is_error_page=%s",
            self._provider,
            ok,
            _safe_url(self._page.url()),
            self._page.title(),
            self._max_progress,
            self._last_load_status,
            self._last_load_is_error_page,
        )
        if not ok:
            auth = _claude_auth_redirect(self._page.url(), self._provider, self._page.title())
            if auth is not None:
                self._finish(auth, "")
            else:
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
        if isinstance(result, dict) and "__retry_after_ms" in result:
            auth = _claude_auth_redirect(self._page.url(), self._provider, self._page.title())
            if auth is not None:
                self._finish(auth, "")
                return
            self._extractor_reruns += 1
            if self._extractor_reruns > 5:
                self._finish(None, "extractor retry limit exceeded")
                return
            try:
                delay_ms = int(result.get("__retry_after_ms") or 0)
            except (TypeError, ValueError):
                delay_ms = 0
            delay_ms = max(0, min(delay_ms, 5000))
            log.info(
                "scrape extractor rerun provider=%s rerun=%s delay_ms=%s reason=%s",
                self._provider,
                self._extractor_reruns,
                delay_ms,
                result.get("__retry_reason", ""),
            )
            QTimer.singleShot(delay_ms, self._run_extractor)
            return
        self._finish(result, "")

    def _finish(self, result: Any, error: str) -> None:
        if self._finished:
            return
        elapsed = time.monotonic() - self._started_at
        if error and error in self._RETRYABLE_ERRORS and self._attempt < self._max_attempts:
            log.warning(
                "scrape retry provider=%s attempt=%s/%s elapsed=%.1fs error=%s "
                "progress=%s load_status=%s",
                self._provider,
                self._attempt,
                self._max_attempts,
                elapsed,
                error,
                self._max_progress,
                self._last_load_status,
            )
            self._stop_loading()
            self._begin_attempt()
            return
        self._finished = True
        self._timeout.stop()
        if error:
            log.warning(
                "scrape fail provider=%s url=%s page_url=%s elapsed=%.1fs "
                "error=%s load_status=%s load_url=%s load_error_code=%s "
                "load_error_domain=%s load_error_string=%r load_is_error_page=%s "
                "title=%r progress=%s url_changes=%s render_terminated=%s "
                "attempts=%s",
                self._provider,
                _safe_url(self._url),
                _safe_url(self._page.url()),
                elapsed,
                error,
                self._last_load_status,
                self._last_load_url,
                self._last_load_error_code,
                self._last_load_error_domain,
                self._last_load_error_string,
                self._last_load_is_error_page,
                self._page.title(),
                self._max_progress,
                self._url_change_count,
                self._render_terminated,
                self._attempt,
            )
        else:
            log.info(
                "scrape ok provider=%s elapsed=%.1fs page_url=%s load_status=%s "
                "load_url=%s load_is_error_page=%s title=%r progress=%s "
                "url_changes=%s render_terminated=%s attempts=%s result_keys=%s",
                self._provider,
                elapsed,
                _safe_url(self._page.url()),
                self._last_load_status,
                self._last_load_url,
                self._last_load_is_error_page,
                self._page.title(),
                self._max_progress,
                self._url_change_count,
                self._render_terminated,
                self._attempt,
                sorted(result.keys()) if isinstance(result, dict) else type(result).__name__,
            )
        self.done.emit(result, error)
        # Release Chromium resources after connected callbacks have had a
        # chance to clear their Python references.
        if self._linger_ms > 0 and not error:
            QTimer.singleShot(0, self._begin_hold)
        else:
            QTimer.singleShot(0, self._cleanup)

    def _begin_hold(self) -> None:
        elapsed_ms = (time.monotonic() - self._started_at) * 1000
        remaining_ms = max(0, int(self._linger_ms - elapsed_ms))
        self._page.runJavaScript(_VISIBILITY_JS, self._on_hold_start_probe)
        QTimer.singleShot(remaining_ms, self._end_hold)

    def _on_hold_start_probe(self, visibility: Any) -> None:
        log.info(
            "keep-alive start provider=%s visibility=%s linger_s=%s",
            self._provider,
            visibility,
            self._linger_ms / 1000,
        )

    def _end_hold(self) -> None:
        # The probe answers from the renderer; if it never does, move on.
        self._page.runJavaScript(_END_PROBE_JS, self._on_hold_end_probe)
        QTimer.singleShot(_PROBE_TIMEOUT_MS, lambda: self._on_hold_end_probe(None))

    def _on_hold_end_probe(self, probe: Any) -> None:
        if self._navigated_away:
            return
        self._navigated_away = True
        probe = probe if isinstance(probe, dict) else {}
        log.info(
            "keep-alive end provider=%s visibility=%s event_logging_requests=%s "
            "elapsed=%.1fs",
            self._provider,
            probe.get("visibility"),
            probe.get("event_logging_requests"),
            time.monotonic() - self._started_at,
        )
        # Navigating away runs pagehide / visibilitychange handlers, which is
        # when a page flushes queued activity events.
        self._page.load(QUrl("about:blank"))
        QTimer.singleShot(_NAVIGATE_AWAY_MS, self._cleanup)

    def _cleanup(self) -> None:
        try:
            self._page.loadFinished.disconnect(self._on_load_finished)
            self._page.loadProgress.disconnect(self._on_load_progress)
            self._page.urlChanged.disconnect(self._on_url_changed)
            self._page.renderProcessTerminated.disconnect(
                self._on_render_process_terminated
            )
        except (TypeError, RuntimeError):
            pass
        loading_changed = getattr(self._page, "loadingChanged", None)
        if loading_changed is not None:
            try:
                loading_changed.disconnect(self._on_loading_changed)
            except (TypeError, RuntimeError):
                pass
        if self._linger_ms > 0:
            # Qt refuses to discard a visible page.
            try:
                self._page.setVisible(False)
            except RuntimeError:
                pass
            self._stop_loading()
        else:
            self._view.stop()
            self._view.setPage(None)
        try:
            self._page.setLifecycleState(self._page.LifecycleState.Discarded)
        except RuntimeError:
            pass
        self._view.deleteLater()
        self._page.deleteLater()
        self.deleteLater()
        _LINGERING.discard(self)


def scrape(
    provider: str,
    url: str,
    extractor_js: str,
    on_done: Callable[[Any, str], None],
    parent: QObject | None = None,
    wait_ms: int = 4000,
    max_attempts: int = 1,
    linger_ms: int = 0,
) -> HeadlessScraper:
    """Convenience wrapper. Returns the scraper so the caller can keep a reference."""
    scraper = HeadlessScraper(
        provider,
        url,
        extractor_js,
        wait_ms=wait_ms,
        max_attempts=max_attempts,
        parent=parent,
        linger_ms=linger_ms,
    )
    scraper.done.connect(on_done)
    return scraper
