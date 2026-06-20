from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

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
    # Identity providers used by the above for SSO popups.
    "auth0.com",
    "google.com",
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
    """Modal embedded-Chromium window for signing in to Claude or ChatGPT.

    The user signs in normally. When they click "I'm signed in", we navigate to
    the provider's actual usage URL and verify via JS that the page loaded for a
    signed-in user. If verification fails, we tell the user and stay open.
    """

    def __init__(
        self,
        provider: str,
        login_url: str,
        title: str,
        parent=None,
        *,
        account_id: str | None = None,
    ):
        # Don't pass parent — avoids style cascade from main widget.
        super().__init__(None)
        # Intentionally NOT WindowStaysOnTopHint: an always-on-top sign-in
        # dialog can sit over OAuth popups (Apple, Microsoft, magic-link
        # email confirmation pages) the user opens in their real browser.
        self._provider = provider
        self._account_id = account_id or provider
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
        self._view.load(QUrl(login_url))

        # Allow popup OAuth windows (some sign-in flows use them).
        self._page.newWindowRequested.connect(self._handle_popup)
        self._popup_pages: list[_PopupPage] = []  # keep refs

        instructions = QLabel(
            "<b>Do not click \u201cContinue with Google\u201d</b> \u2014 Google blocks "
            "embedded browsers, and Google passkeys usually fail here too. If "
            "you normally sign in with Google, try typing that same email "
            "address into the <b>Enter your email</b> box and use the "
            "<b>magic link</b> sent to your inbox. If OpenAI sends you back to "
            "Google or asks for a passkey, close this window and use "
            "<b>Paste cookie</b> in Settings. "
            "Click <b>I'm signed in</b> when you reach your account."
        )
        instructions.setWordWrap(True)
        instructions.setStyleSheet(
            "color:#374151; background:#fef3c7; padding:8px; border-radius:4px;"
        )

        self._status = QLabel("")
        self._status.setStyleSheet("color:#dc2626;")

        verify_btn = QPushButton("I'm signed in")
        verify_btn.setDefault(True)
        verify_btn.clicked.connect(self._verify)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        button_row = QHBoxLayout()
        button_row.addWidget(self._status, 1)
        button_row.addWidget(verify_btn)
        button_row.addWidget(cancel_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(instructions)
        layout.addWidget(self._view, 1)
        layout.addLayout(button_row)

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
            "Continuing with Google. If Google refuses this embedded browser "
            "or a passkey fails, use Paste cookie in Settings."
        )
        self._status.setStyleSheet("color:#6b7280;")

    def closeEvent(self, event) -> None:
        self._close_popups()
        super().closeEvent(event)

    def done(self, result: int) -> None:
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
        self._verify_url = url
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

        self._view.load(QUrl(url))
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
