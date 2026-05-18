from __future__ import annotations

import logging

from PyQt6.QtCore import QUrl
from PyQt6.QtWebEngineCore import QWebEnginePage

log = logging.getLogger("aigauge.webview.page")


_NOISY_CONSOLE_FRAGMENTS = (
    "Error with Permissions-Policy header: Unrecognized feature:",
    "[GSI_LOGGER]:",
    "[Intercom] The App ID in your code snippet has not been set.",
    "preloaded using link preload in Early Hints but not used",
)


def _shorten(value: str, limit: int = 300) -> str:
    return value if len(value) <= limit else value[:limit] + "..."


def _safe_source_id(source_id: str) -> str:
    url = QUrl(source_id)
    if url.isValid() and url.scheme() in ("http", "https") and url.host():
        return f"{url.scheme()}://{url.host()}{url.path()}"
    return source_id


class QuietWebEnginePage(QWebEnginePage):
    """QWebEnginePage that suppresses noisy third-party console chatter."""

    def __init__(self, profile, parent=None, *, provider: str = "unknown"):
        super().__init__(profile, parent)
        self._diagnostic_provider = provider

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):  # noqa: N802
        if any(fragment in message for fragment in _NOISY_CONSOLE_FRAGMENTS):
            return
        log.info(
            "webengine console provider=%s level=%s source=%s line=%s message=%r",
            self._diagnostic_provider,
            getattr(level, "name", str(level)),
            _shorten(_safe_source_id(source_id or "")),
            line_number,
            _shorten(message),
        )
        super().javaScriptConsoleMessage(level, message, line_number, source_id)
