from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtWebEngineCore import QWebEngineSettings
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

# Verification: load the actual usage / dashboard page after the user clicks
# "I'm signed in" and check via JS whether we're authenticated.
VERIFY_TARGETS = {
    "claude": (
        "https://claude.ai/settings/usage",
        # If we see "Plan usage limits" anywhere in the body, we're signed in.
        # Otherwise, the page redirects to /login.
        "(() => document.body && document.body.innerText.includes('Plan usage limits'))()",
    ),
    "codex": (
        "https://chatgpt.com/codex/cloud/settings/analytics",
        # The analytics page contains 'Balance' / 'usage limit' once signed in.
        "(() => document.body && /usage limit/i.test(document.body.innerText))()",
    ),
}


def _styled_page(profile, parent) -> QWebEnginePage:
    page = QuietWebEnginePage(profile, parent)
    s = page.settings()
    s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
    s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, True)
    s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
    s.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
    s.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, True)
    s.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, False)
    s.setAttribute(QWebEngineSettings.WebAttribute.AllowGeolocationOnInsecureOrigins, False)
    s.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
    s.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
    s.setAttribute(QWebEngineSettings.WebAttribute.HyperlinkAuditingEnabled, False)
    return page


class _PopupPage(QuietWebEnginePage):
    """Page used for popup OAuth windows opened from the main login view."""

    def __init__(self, profile, parent):
        super().__init__(profile, parent)
        self._popup_view: QWebEngineView | None = None
        # Intercept Google popups — they're going to fail anyway and confuse the user.
        self.urlChanged.connect(self._on_url_changed)
        self._google_warned = False

    def attach_view(self) -> QWebEngineView:
        view = QWebEngineView()
        view.setPage(self)
        view.setWindowFlag(Qt.WindowType.Window, True)
        view.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        view.resize(560, 720)
        view.setWindowTitle("Sign in")
        view.show()
        view.raise_()
        view.activateWindow()
        # Force keyboard focus into the embedded chromium widget.
        view.setFocus(Qt.FocusReason.OtherFocusReason)
        self._popup_view = view
        return view

    def _on_url_changed(self, url: QUrl) -> None:
        host = url.host()
        if "google.com" in host or "accounts.google" in host:
            if self._google_warned or self._popup_view is None:
                return
            self._google_warned = True
            QMessageBox.warning(
                self._popup_view,
                "Google sign-in not supported",
                "Google blocks sign-in from embedded browsers. Cancel this "
                "window and use the <b>Paste cookie</b> button in Settings "
                "instead — that path works with Google-authed accounts.",
            )


class LoginWindow(QDialog):
    """Modal embedded-Chromium window for signing in to Claude or ChatGPT.

    The user signs in normally. When they click "I'm signed in", we navigate to
    the provider's actual usage URL and verify via JS that the page loaded for a
    signed-in user. If verification fails, we tell the user and stay open.
    """

    def __init__(self, provider: str, login_url: str, title: str, parent=None):
        # Don't pass parent — avoids style cascade from main widget.
        super().__init__(None)
        # Stays-on-top so it renders above the main always-on-top widget.
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
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
            "Sign in normally. <b>Use email/password</b> if shown — "
            "Google sign-in often blocks embedded browsers. "
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
        view.destroyed.connect(lambda _=None: self._popup_pages.remove(popup_page)
                               if popup_page in self._popup_pages else None)
        self._popup_pages.append(popup_page)

    def _verify(self) -> None:
        target = VERIFY_TARGETS.get(self._provider)
        if not target:
            self.accept()
            return
        url, check_js = target
        self._status.setText("Verifying session…")
        self._status.setStyleSheet("color:#6b7280;")

        verify_page = _styled_page(self._profile, self)

        def _after_load(ok: bool):
            if not ok:
                self._status.setText("Could not load verification page. Try again.")
                self._status.setStyleSheet("color:#dc2626;")
                return
            verify_page.runJavaScript(check_js, _after_check)

        def _after_check(result: Any):
            if result is True:
                self.accept()
            else:
                self._status.setText(
                    "Not signed in yet — please complete sign-in in the window above."
                )
                self._status.setStyleSheet("color:#dc2626;")

        verify_page.loadFinished.connect(_after_load)
        verify_page.load(QUrl(url))
        # Hold a ref so it doesn't get GC'd before callbacks fire
        self._verify_page = verify_page
