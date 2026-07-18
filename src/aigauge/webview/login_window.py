from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .cookies import import_browser_cookies
from .external_login import ExternalLoginWorker
from .page import QuietWebEnginePage
from .profile import get_profile
from .verify import (
    VERIFY_TARGETS,
    verify_session,
)  # noqa: F401 - VERIFY_TARGETS re-exported for callers

log = logging.getLogger("aigauge.webview.login")

# Top-frame navigation in the embedded sign-in browser is restricted to these
# host suffixes. The goal is defense in depth against an open-redirect bug on
# either provider redirecting the embedded browser to an arbitrary URL.
# Subresources (iframes, fonts, analytics, captchas) are not filtered — only
# main-frame loads. If a real sign-in flow needs another host, add it here.
AUTH_HOST_ALLOWLIST: tuple[str, ...] = (
    # Anthropic / Claude
    "claude.ai",
    "anthropic.com",
    # OpenAI / ChatGPT / Codex
    "chatgpt.com",
    "openai.com",
    "oaistatic.com",
    "oaiusercontent.com",
    # OpenCode
    "opencode.ai",
    # Identity providers used by the above for SSO popups.
    "auth0.com",
    "google.com",
    "github.com",
    "youtube.com",
    "appleid.apple.com",
    "apple.com",
    "icloud.com",
    "microsoftonline.com",
    "microsoft.com",
    "live.com",
)


def _host_allowed(host: str) -> bool:
    host = host.lower().strip()
    if not host:
        return False
    for suffix in AUTH_HOST_ALLOWLIST:
        if host == suffix or host.endswith("." + suffix):
            return True
    return False


def _is_google_host(host: str) -> bool:
    host = host.lower().strip()
    return host == "google.com" or host.endswith(".google.com")


def _safe_url_for_log(url: QUrl) -> str:
    if url.scheme() in ("http", "https"):
        return f"{url.scheme()}://{url.host()}{url.path()}"
    return f"{url.scheme()}:{url.path()}"


class _AllowlistedPage(QuietWebEnginePage):
    """QuietWebEnginePage that blocks main-frame navigation off the auth allowlist."""

    def __init__(
        self,
        profile,
        parent=None,
        *,
        provider: str = "unknown",
        on_google_started=None,
    ):
        super().__init__(profile, parent, provider=provider)
        self._on_google_started = on_google_started
        self._google_noted = False

    def acceptNavigationRequest(  # noqa: N802 — Qt override
        self,
        url: QUrl,
        nav_type: QWebEnginePage.NavigationType,
        is_main_frame: bool,
    ) -> bool:
        if is_main_frame:
            scheme = url.scheme().lower()
            if scheme in ("about", "data", "blob"):
                return True
            if scheme not in ("http", "https"):
                log.warning(
                    "login_window: blocking non-http navigation scheme=%s url=%s",
                    scheme,
                    _safe_url_for_log(url),
                )
                return False
            if _is_google_host(url.host()):
                if not self._google_noted and self._on_google_started is not None:
                    self._google_noted = True
                    QTimer.singleShot(0, self._on_google_started)
                log.info(
                    "login_window: Google sign-in navigation host=%s url=%s",
                    url.host(),
                    _safe_url_for_log(url),
                )
            if not _host_allowed(url.host()):
                log.warning(
                    "login_window: blocking off-allowlist navigation host=%s url=%s",
                    url.host(),
                    _safe_url_for_log(url),
                )
                return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


def _styled_page(profile, parent, *, provider: str, on_google_started) -> QWebEnginePage:
    page = _AllowlistedPage(
        profile,
        parent,
        provider=provider,
        on_google_started=on_google_started,
    )
    s = page.settings()
    s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
    s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, True)
    s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
    s.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
    s.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, True)
    s.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, False)
    s.setAttribute(
        QWebEngineSettings.WebAttribute.AllowGeolocationOnInsecureOrigins, False
    )
    s.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
    s.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
    s.setAttribute(QWebEngineSettings.WebAttribute.HyperlinkAuditingEnabled, False)
    return page


class _PopupPage(_AllowlistedPage):
    """Page used for popup OAuth windows opened from the main login view."""

    def __init__(self, profile, parent, *, provider: str, on_google_started):
        super().__init__(
            profile,
            parent,
            provider=provider,
            on_google_started=on_google_started,
        )
        self._popup_view: QWebEngineView | None = None

    def attach_view(self) -> QWebEngineView:
        view = QWebEngineView()
        view.setPage(self)
        view.setWindowFlag(Qt.WindowType.Window, True)
        view.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        view.resize(560, 720)
        view.setWindowTitle("Sign in")
        view.show()
        view.raise_()
        view.activateWindow()
        # Force keyboard focus into the embedded chromium widget.
        view.setFocus(Qt.FocusReason.OtherFocusReason)
        self._popup_view = view
        return view


class LoginWindow(QDialog):
    """Sign in with a real browser, then verify in AI Gauge's profile.

    Google rejects embedded OAuth user-agents, so the primary flow launches an
    installed Chrome-family browser in an isolated temporary profile and copies
    the resulting provider cookies over a loopback-only DevTools connection.
    The embedded view remains available as a fallback.
    """

    def __init__(
        self,
        provider: str,
        login_url: str,
        title: str,
        parent=None,
        *,
        account_id: str | None = None,
        verify_url: str | None = None,
    ):
        # Don't pass parent — avoids style cascade from main widget.
        super().__init__(None)
        # Intentionally NOT WindowStaysOnTopHint: an always-on-top sign-in
        # dialog can sit over OAuth popups (Apple, Microsoft, magic-link
        # email confirmation pages) the user opens in their real browser.
        self._provider = provider
        self._account_id = account_id or provider
        self._verify_url_override = verify_url
        self.setWindowTitle(title)
        self.resize(960, 760)

        profile = get_profile(self._account_id)
        self._profile = profile
        self._page = _styled_page(
            profile,
            self,
            provider=self._account_id,
            on_google_started=self._on_google_started,
        )
        self._view = QWebEngineView(self)
        self._view.setPage(self._page)
        self._view.setVisible(False)
        self._login_url = login_url

        # Allow popup OAuth windows (some sign-in flows use them).
        self._page.newWindowRequested.connect(self._handle_popup)
        self._popup_pages: list[_PopupPage] = []  # keep refs

        self._instructions = QLabel(
            "AI Gauge is opening a real Chrome, Edge, Brave, or Chromium window. "
            "Sign in there normally, including with <b>Google</b> or a "
            "<b>passkey</b>. This window will finish automatically; there is "
            "nothing to copy or paste."
        )
        self._instructions.setWordWrap(True)
        self._instructions.setStyleSheet(
            "color:#374151; background:#fef3c7; padding:8px; border-radius:4px;"
        )

        self._status = QLabel("")
        self._status.setStyleSheet("color:#dc2626;")

        self._embedded_btn = QPushButton("Use embedded browser instead")
        self._embedded_btn.clicked.connect(self._use_embedded_browser)
        self._verify_btn = QPushButton("I'm signed in")
        self._verify_btn.setDefault(True)
        self._verify_btn.setVisible(False)
        self._verify_btn.clicked.connect(self._verify)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        button_row = QHBoxLayout()
        button_row.addWidget(self._status, 1)
        button_row.addWidget(self._embedded_btn)
        button_row.addWidget(self._verify_btn)
        button_row.addWidget(cancel_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(self._instructions)
        layout.addWidget(self._view, 1)
        layout.addLayout(button_row)

        self.resize(680, 220)
        self._closing = False
        self._external_worker: ExternalLoginWorker | None = None
        QTimer.singleShot(0, self._start_external_login)

    def _start_external_login(self) -> None:
        if self._closing or self._external_worker is not None:
            return
        self._status.setText("Opening your browser…")
        self._status.setStyleSheet("color:#6b7280;")
        worker = ExternalLoginWorker(
            self._provider,
            self._login_url,
            self._account_id,
            self,
        )
        worker.status_changed.connect(self._on_external_status)
        worker.session_ready.connect(self._on_external_session_ready)
        worker.failed.connect(self._on_external_failed)
        self._external_worker = worker
        worker.start()

    def _on_external_status(self, message: str) -> None:
        if self._closing:
            return
        self._status.setText(message)
        self._status.setStyleSheet("color:#2563eb;")

    def _on_external_session_ready(self, cookies: list[dict]) -> None:
        if self._closing:
            return
        try:
            imported = import_browser_cookies(
                self._provider,
                self._account_id,
                cookies,
            )
        except Exception as exc:  # noqa: BLE001 - surface save failures
            log.exception("external sign-in cookie import failed")
            self._on_external_failed(f"Could not save the signed-in session: {exc}")
            return
        if not imported:
            self._on_external_failed("The browser signed in, but no session was found.")
            return
        self._status.setText("Signed in. Verifying the session…")
        self._status.setStyleSheet("color:#16a34a;")
        QTimer.singleShot(1200, self._verify)

    def _on_external_failed(self, message: str) -> None:
        if self._closing:
            return
        self._status.setText(message)
        self._status.setStyleSheet("color:#dc2626;")
        self._use_embedded_browser()

    def _use_embedded_browser(self) -> None:
        if self._closing:
            return
        self._stop_external_login()
        self._instructions.setText(
            "Using AI Gauge's embedded browser. Email and magic-link sign-in "
            "usually work here, but Google may reject this window by policy. "
            "Use the real-browser flow for Google or passkeys."
        )
        self._view.setVisible(True)
        self.resize(960, 760)
        self._embedded_btn.setVisible(False)
        self._verify_btn.setVisible(True)
        self._view.load(QUrl(self._login_url))

    def _stop_external_login(self) -> None:
        worker = self._external_worker
        if worker is None:
            return
        worker.stop()
        if worker.isRunning():
            # Every blocking operation in ExternalLoginWorker has a finite
            # timeout. Wait for its finally block so the QThread cannot outlive
            # this dialog and its temporary browser profile is cleaned up.
            worker.wait()
        self._external_worker = None

    def _handle_popup(self, request) -> None:
        """Spawn a new window for popup-based OAuth flows."""
        popup_page = _PopupPage(
            self._profile,
            self,
            provider=self._account_id,
            on_google_started=self._on_google_started,
        )
        request.openIn(popup_page)
        view = popup_page.attach_view()
        # When the popup closes, drop the reference.
        view.destroyed.connect(
            lambda _=None: (
                self._popup_pages.remove(popup_page)
                if popup_page in self._popup_pages
                else None
            )
        )
        self._popup_pages.append(popup_page)

    def _on_google_started(self) -> None:
        self._status.setText(
            "Google may refuse embedded sign-in. Cancel and click Sign in "
            "again to use the real-browser flow."
        )
        self._status.setStyleSheet("color:#6b7280;")

    def closeEvent(self, event) -> None:
        self._closing = True
        self._stop_external_login()
        self._close_popups()
        super().closeEvent(event)

    def done(self, result: int) -> None:
        self._closing = True
        self._stop_external_login()
        self._close_popups()
        super().done(result)

    def _close_popups(self) -> None:
        for popup_page in list(self._popup_pages):
            view = popup_page._popup_view
            if view is not None:
                view.close()
            popup_page.deleteLater()
        self._popup_pages.clear()

    def _verify(self) -> None:
        if self._provider not in VERIFY_TARGETS:
            self.accept()
            return
        url, check_js = VERIFY_TARGETS[self._provider]
        self._verify_url = self._verify_url_override or url
        self._verify_check_js = check_js
        self._status.setText("Verifying session…")
        self._status.setStyleSheet("color:#6b7280;")

        # Verify by navigating the *existing* signed-in view, not a fresh
        # page. A fresh QWebEnginePage racing against the cookie store's
        # async commit was landing on /login?from=logout right after a
        # successful sign-in. The user's view already holds the live
        # session, so navigating it to the usage URL is the most
        # reliable way to prove the cookies stick.
        try:
            self._page.loadFinished.disconnect(self._on_verify_load_finished)
        except (TypeError, RuntimeError):
            pass
        self._page.loadFinished.connect(self._on_verify_load_finished)

        self._verify_attempts = 0
        self._verify_polling = False
        self._verify_timeout = QTimer(self)
        self._verify_timeout.setSingleShot(True)
        self._verify_timeout.timeout.connect(self._on_verify_timeout)
        self._verify_timeout.start(20000)

        self._view.load(QUrl(self._verify_url))
        # loadFinished does NOT fire for a same-document (fragment-only)
        # navigation. After a fresh sign-in the view is already sitting on
        # https://claude.ai/new, so loading .../new#settings/usage only changes
        # the hash — loadFinished never fires and verification would hang until
        # the 20s timeout ("Could not load verification page (timeout)"). Drive
        # polling from a timer so the check runs regardless; loadFinished, when
        # it does fire (full cross-document load), only fast-fails real errors.
        QTimer.singleShot(1500, self._begin_verify_polling)

    def _on_verify_load_finished(self, ok: bool) -> None:
        try:
            self._page.loadFinished.disconnect(self._on_verify_load_finished)
        except (TypeError, RuntimeError):
            pass
        if not ok:
            if not self._verify_polling:
                self._verify_finish(False, "page failed to load")
            return
        # A real cross-document load just completed. Reset the budget so the
        # freshly loaded page gets the full polling window from here, in case
        # it loaded slowly, then ensure polling is running.
        self._verify_attempts = 0
        self._begin_verify_polling()

    def _begin_verify_polling(self) -> None:
        # SPA hydration is async — poll the JS check rather than sampling
        # once. Claude's usage page renders skeleton first, then fills in
        # "Plan usage limits" a beat later.
        if getattr(self, "_verify_timeout", None) is None:
            return  # already finished (timeout or success)
        if self._verify_polling:
            return  # already polling
        self._verify_polling = True
        self._run_verify_check()

    def _run_verify_check(self) -> None:
        if getattr(self, "_verify_timeout", None) is None:
            return  # already finished
        landed = self._page.url().toString()
        if "/login" in landed.lower():
            self._verify_finish(False, "")
            return
        self._page.runJavaScript(self._verify_check_js, self._on_verify_js_result)

    def _on_verify_js_result(self, result) -> None:
        if result is True:
            self._verify_finish(True, "")
            return
        self._verify_attempts = getattr(self, "_verify_attempts", 0) + 1
        if self._verify_attempts >= 12:
            self._verify_finish(False, "")
            return
        QTimer.singleShot(1000, self._run_verify_check)

    def _on_verify_timeout(self) -> None:
        self._verify_finish(False, "timeout")

    def _verify_finish(self, ok: bool, error: str) -> None:
        timer = getattr(self, "_verify_timeout", None)
        if timer is not None:
            timer.stop()
            self._verify_timeout = None
        if ok:
            self.accept()
            return
        if error:
            self._status.setText(
                f"Could not load verification page ({error}). Try again."
            )
        else:
            self._status.setText(
                "Not signed in yet — please complete sign-in in the window above."
            )
        self._status.setStyleSheet("color:#dc2626;")
