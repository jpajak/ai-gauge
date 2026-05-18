from __future__ import annotations

import logging

from PyQt6.QtCore import QUrl
from PyQt6.QtWebEngineCore import QWebEnginePage

log = logging.getLogger("aigauge.webview.page")


# JS console fragments that are pure third-party telemetry/analytics chatter
# and never useful when diagnosing AI Gauge issues. Matched as substrings.
_NOISY_CONSOLE_FRAGMENTS = (
    "Error with Permissions-Policy header: Unrecognized feature:",
    "[GSI_LOGGER]:",
    "[Intercom] The App ID in your code snippet has not been set.",
    "preloaded using link preload in Early Hints but not used",
    # claude.ai loads an isolated analytics iframe that posts a long stream of
    # info-level messages on every page load (~25 lines per scrape).
    "[IsolatedSegment]",
    # Datadog RUM init banner from claude.ai's bundle.
    "[O11Y] [DatadogRUM]",
    "DatadogRUM",
)


def _shorten(value: str, limit: int = 300) -> str:
    return value if len(value) <= limit else value[:limit] + "..."


def _safe_source_id(source_id: str) -> str:
    url = QUrl(source_id)
    if url.isValid() and url.scheme() in ("http", "https") and url.host():
        return f"{url.scheme()}://{url.host()}{url.path()}"
    return source_id


def _python_level_for(js_level: object) -> int:
    """Map a QWebEnginePage console level enum to a Python logging level.

    JS Info-level messages from the embedded pages are overwhelmingly routine
    analytics chatter, so they're routed to DEBUG (suppressed by the default
    INFO file logger). Warnings and errors still surface in the log.
    """
    name = getattr(js_level, "name", str(js_level))
    if name == "ErrorMessageLevel":
        return logging.WARNING
    if name == "WarningMessageLevel":
        return logging.INFO
    return logging.DEBUG


class QuietWebEnginePage(QWebEnginePage):
    """QWebEnginePage that suppresses noisy third-party console chatter."""

    def __init__(self, profile, parent=None, *, provider: str = "unknown"):
        super().__init__(profile, parent)
        self._diagnostic_provider = provider

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):  # noqa: N802
        if any(fragment in message for fragment in _NOISY_CONSOLE_FRAGMENTS):
            return
        python_level = _python_level_for(level)
        log.log(
            python_level,
            "webengine console provider=%s level=%s source=%s line=%s message=%r",
            self._diagnostic_provider,
            getattr(level, "name", str(level)),
            _shorten(_safe_source_id(source_id or "")),
            line_number,
            _shorten(message),
        )
        super().javaScriptConsoleMessage(level, message, line_number, source_id)
