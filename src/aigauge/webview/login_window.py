from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

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


class _AllowlistedPage(QuietWebEnginePage):
    """QuietWebEnginePage that blocks main-frame navigation off the auth allowlist."""

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
                    url.toString(),
                )
                return False
            if not _host_allowed(url.host()):
                log.warning(
                    "login_window: blocking off-allowlist navigation host=%s url=%s",
                    url.host(),
                    url.toString(),
                )
                return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


def _styled_page(profile, parent) -> QWebEnginePage:
    page = _AllowlistedPage(profile, parent)
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

    def __init__(self, profile, parent):
        super().__init__(profile, parent)
        self._popup_view: QWebEngineView | None = None
        self._google_warned = False

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

    def acceptNavigationRequest(  # noqa: N802 — Qt override
        self,
        url: QUrl,
        nav_type: QWebEnginePage.NavigationType,
        is_main_frame: bool,
    ) -> bool:
        # Google sign-in is going to fail anyway (Google blocks embedded
        # browsers), and the host is also off-allowlist below — surface a
        # friendlier message before the generic block kicks in.
        if is_main_frame and not self._google_warned:
            host = url.host().lower()
            if "google.com" in host or "accounts.google" in host:
                self._google_warned = True
                if self._popup_view is not None:
                    QMessageBox.warning(
                        self._popup_view,
                        "Google sign-in not supported",
                        "Google blocks sign-in from embedded browsers. Cancel "
                        "this window and use the <b>Paste cookie</b> button "
                        "in Settings instead — that path works with "
                        "Google-authed accounts.",
                    )
                return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class LoginWindow(QDialog):
    """Modal embedded-Chromium window for signing in to Claude or ChatGPT.

    The user signs in normally. When they click "I'm signed in", we navigate to
    the provider's actual usage URL and verify via JS that the page loaded for a
    signed-in user. If verification fails, we tell the user and stay open.
    """

    def __init__(self, provider: str, login_url: str, title: str, parent=None):
        # Don't pass parent — avoids style cascade from main widget.
        super().__init__(None)
        # Intentionally NOT WindowStaysOnTopHint: an always-on-top sign-in
        # dialog can sit over OAuth popups (Apple, Microsoft, magic-link
        # email confirmation pages) the user opens in their real browser.
        self._provider = provider
        self.setWindowTitle(title)
        self.resize(960, 760)

        profile = get_profile(provider)
        self._profile = profile
        self._page = _styled_page(profile, self)
        self._view = QWebEngineView(self)
        self._view.setPage(self._page)
        self._view.load(QUrl(login_url))

        # Allow popup OAuth windows (some sign-in flows use them).
        self._page.newWindowRequested.connect(self._handle_popup)
        self._popup_pages: list[_PopupPage] = []  # keep refs

        instructions = QLabel(
            "<b>Do not click \u201cContinue with Google\u201d</b> \u2014 Google blocks "
            "embedded browsers. If you normally sign in with Google, just type "
            "that same email address into the <b>Enter your email</b> box and "
            "use the <b>magic link</b> sent to your inbox. "
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
        popup_page = _PopupPage(self._profile, self)
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

        self._verify_timeout = QTimer(self)
        self._verify_timeout.setSingleShot(True)
        self._verify_timeout.timeout.connect(self._on_verify_timeout)
        self._verify_timeout.start(20000)

        self._view.load(QUrl(url))

    def _on_verify_load_finished(self, ok: bool) -> None:
        try:
            self._page.loadFinished.disconnect(self._on_verify_load_finished)
        except (TypeError, RuntimeError):
            pass
        if not ok:
            self._verify_finish(False, "page failed to load")
            return
        # SPA hydration is async — poll the JS check rather than sampling
        # once. Claude's usage page renders skeleton first, then fills in
        # "Plan usage limits" a beat later.
        self._verify_attempts = 0
        QTimer.singleShot(1500, self._run_verify_check)

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
