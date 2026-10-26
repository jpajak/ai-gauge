from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..providers.registry import provider_spec, sign_in_hosts
from .cookies import import_browser_cookies
from .external_login import (
    BROWSER_LABELS,
    ExternalLoginWorker,
    _domain_matches,
    _has_auth_cookie,
    installed_browsers,
)
from .page import QuietWebEnginePage
from .profile import get_profile
from .verify import (
    VERIFY_TARGETS,
    verify_session,
)  # noqa: F401 - VERIFY_TARGETS re-exported for callers

log = logging.getLogger("aigauge.webview.login")

_MAX_WIDGET_SIZE = 16777215

_LOGIN_STYLESHEET = """
QDialog { background:#1f2937; color:#e5e7eb; }
QWidget#loginPage { background:#1f2937; }
QLabel { color:#e5e7eb; background:transparent; }
QLabel#heading { font-size:15px; font-weight:600; }
QLabel#secondary { color:#9ca3af; font-size:11px; }
QLabel#status { color:#9ca3af; font-size:11px; }
QLabel#error { color:#ef4444; font-size:11px; }
QFrame#browserCard {
    background:#111827; border:1px solid #374151; border-radius:6px;
}
QLabel#embeddedNotice {
    color:#374151; background:#fef3c7;
    border:1px solid #f59e0b; border-radius:5px;
    padding:8px;
}
QComboBox {
    background:#111827; color:#f3f4f6;
    border:1px solid #4b5563; border-radius:4px;
    padding:5px 8px; min-height:22px;
}
QComboBox:disabled { color:#6b7280; }
QComboBox QAbstractItemView {
    background:#111827; color:#f3f4f6; selection-background-color:#2563eb;
}
QPushButton {
    background:#374151; color:#f3f4f6;
    border:1px solid #4b5563; border-radius:4px;
    padding:5px 12px; min-height:22px;
}
QPushButton:hover { background:#4b5563; }
QPushButton:disabled { color:#6b7280; background:#273244; }
QPushButton:default { background:#2563eb; border-color:#1d4ed8; }
QPushButton:default:hover { background:#1d4ed8; }
QPushButton#linkButton {
    color:#60a5fa; background:transparent; border:none;
    padding:2px 0; text-align:left;
}
QPushButton#linkButton:hover { color:#93c5fd; text-decoration:underline; }
"""

# Top-frame navigation in the embedded sign-in browser is restricted to these
# host suffixes. The goal is defense in depth against an open-redirect bug on
# a provider redirecting the embedded browser to an arbitrary URL.
# Subresources (iframes, fonts, analytics, captchas) are not filtered — only
# main-frame loads. A provider's own hosts come from its registry spec
# (LoginSpec.hosts); if a real sign-in flow needs another identity provider,
# add it to _IDENTITY_PROVIDER_HOSTS.
_IDENTITY_PROVIDER_HOSTS: tuple[str, ...] = (
    # Identity providers the providers' sign-in pages use for SSO popups.
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
AUTH_HOST_ALLOWLIST: tuple[str, ...] = sign_in_hosts() + _IDENTITY_PROVIDER_HOSTS


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

    browser_preference_changed = pyqtSignal(str)

    def __init__(
        self,
        provider: str,
        login_url: str,
        title: str,
        parent=None,
        *,
        account_id: str | None = None,
        verify_url: str | None = None,
        browser_preference: str = "ask",
    ):
        # Don't pass parent — avoids style cascade from main widget.
        super().__init__(None)
        # Intentionally NOT WindowStaysOnTopHint: an always-on-top sign-in
        # dialog can sit over OAuth popups (Apple, Microsoft, magic-link
        # email confirmation pages) the user opens in their real browser.
        self._provider = provider
        self._account_id = account_id or provider
        self._verify_url_override = verify_url
        self._preferred_browser = browser_preference
        self.setWindowTitle(title)
        self.setStyleSheet(_LOGIN_STYLESHEET)

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
        self._login_url = login_url
        self._cookie_store = profile.cookieStore()
        self._cookie_store.cookieAdded.connect(self._on_embedded_cookie_added)

        # Allow popup OAuth windows (some sign-in flows use them).
        self._page.newWindowRequested.connect(self._handle_popup)
        self._page.loadFinished.connect(self._on_embedded_page_loaded)
        self._popup_pages: list[_PopupPage] = []  # keep refs

        self._browser_combo = QComboBox()
        self._installed_browsers = installed_browsers()
        for browser_id in BROWSER_LABELS:
            if browser_id in self._installed_browsers:
                self._browser_combo.addItem(BROWSER_LABELS[browser_id], browser_id)
        self._browser_combo.currentIndexChanged.connect(self._update_continue_label)

        self._continue_btn = QPushButton()
        self._continue_btn.clicked.connect(self._choose_external_browser)
        self._update_continue_label()

        self._choice_embedded_btn = QPushButton("Use embedded browser instead")
        self._choice_embedded_btn.setObjectName("linkButton")
        self._choice_embedded_btn.clicked.connect(self._use_embedded_browser)

        self._embedded_btn = QPushButton("Use embedded browser instead")
        self._embedded_btn.setObjectName("linkButton")
        self._embedded_btn.clicked.connect(self._use_embedded_browser)

        self._installed_btn = QPushButton("Use an installed browser")
        self._installed_btn.clicked.connect(lambda: self._show_browser_choice())

        self._verify_btn = QPushButton("I'm signed in")
        self._verify_btn.clicked.connect(self._verify)

        self._choice_page = self._build_choice_page()
        self._waiting_page = self._build_waiting_page()
        self._embedded_page = self._build_embedded_page()

        self._pages = QStackedWidget()
        self._pages.addWidget(self._choice_page)
        self._pages.addWidget(self._waiting_page)
        self._pages.addWidget(self._embedded_page)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._pages)

        self._closing = False
        self._embedded_active = False
        self._embedded_auth_seen = False
        self._embedded_verify_scheduled = False
        self._verifying = False
        self._verify_timeout: QTimer | None = None
        self._session_may_have_changed = False
        self._external_worker: ExternalLoginWorker | None = None
        self._show_browser_choice()
        if browser_preference == "embedded":
            QTimer.singleShot(0, self._use_embedded_browser)
        elif browser_preference in self._installed_browsers:
            QTimer.singleShot(
                0, lambda selected=browser_preference: self._start_external_login(selected)
            )
        elif browser_preference not in ("ask", "embedded"):
            label = BROWSER_LABELS.get(browser_preference, "Selected browser")
            self._show_browser_choice(
                f"{label} is not installed. Choose another sign-in method."
            )

    @staticmethod
    def _label(text: str, *, object_name: str = "", wrap: bool = True) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(wrap)
        if object_name:
            label.setObjectName(object_name)
        return label

    def _cancel_button(self) -> QPushButton:
        button = QPushButton("Cancel")
        button.clicked.connect(self.reject)
        return button

    def _build_choice_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("loginPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)

        layout.addWidget(
            self._label("Use an installed browser (recommended)", object_name="heading")
        )
        layout.addWidget(
            self._label(
                "AI Gauge opens a temporary browser profile and connects the "
                "session after you finish signing in.",
                object_name="secondary",
            )
        )

        card = QFrame()
        card.setObjectName("browserCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(6)

        browser_row = QHBoxLayout()
        browser_row.setSpacing(10)
        browser_label = self._label("Browser", wrap=False)
        browser_label.setMinimumWidth(62)
        browser_row.addWidget(browser_label)
        browser_row.addWidget(self._browser_combo, 1)
        card_layout.addLayout(browser_row)

        self._browser_hint = self._label(
            "Supports Google, passkeys, and magic links.", object_name="secondary"
        )
        card_layout.addWidget(self._browser_hint)
        layout.addWidget(card)

        self._choice_error = self._label("", object_name="error")
        self._choice_error.setVisible(False)
        layout.addWidget(self._choice_error)

        layout.addWidget(self._choice_embedded_btn, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(
            self._label(
                "Best for email or magic-link sign-in. Google and passkeys may not work.",
                object_name="secondary",
            )
        )

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        action_row.addWidget(self._continue_btn)
        action_row.addWidget(self._cancel_button())
        layout.addLayout(action_row)
        return page

    def _build_waiting_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("loginPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        self._waiting_heading = self._label("Continue in your browser", object_name="heading")
        layout.addWidget(self._waiting_heading)
        self._waiting_instructions = self._label("", object_name="secondary")
        layout.addWidget(self._waiting_instructions)
        layout.addStretch(1)
        self._waiting_status = self._label("", object_name="status")
        layout.addWidget(self._waiting_status)

        action_row = QHBoxLayout()
        action_row.addWidget(self._embedded_btn)
        action_row.addStretch(1)
        action_row.addWidget(self._cancel_button())
        layout.addLayout(action_row)
        return page

    def _build_embedded_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("loginPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        notice = self._label(
            "Embedded browser — best for email or magic-link sign-in. "
            "Google and passkeys may not work here.",
            object_name="embeddedNotice",
        )
        notice.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout.addWidget(notice)
        layout.addWidget(self._view, 1)

        self._embedded_status = self._label("", object_name="status")
        action_row = QHBoxLayout()
        action_row.addWidget(self._embedded_status, 1)
        action_row.addWidget(self._installed_btn)
        action_row.addWidget(self._verify_btn)
        action_row.addWidget(self._cancel_button())
        layout.addLayout(action_row)
        return page

    def _show_compact_page(self, page: QWidget, width: int, height: int) -> None:
        self._pages.setCurrentWidget(page)
        self.setMinimumSize(0, 0)
        self.setMaximumSize(_MAX_WIDGET_SIZE, _MAX_WIDGET_SIZE)
        self.setFixedSize(width, height)

    def _show_resizable_page(self, page: QWidget, width: int, height: int) -> None:
        self._pages.setCurrentWidget(page)
        self.setMinimumSize(0, 0)
        self.setMaximumSize(_MAX_WIDGET_SIZE, _MAX_WIDGET_SIZE)
        self.setMinimumSize(720, 560)
        self.resize(width, height)

    def _update_continue_label(self, _index: int | None = None) -> None:
        browser_id = self._browser_combo.currentData()
        label = BROWSER_LABELS.get(browser_id, "browser")
        self._continue_btn.setText(f"Continue with {label}")

    @staticmethod
    def _set_label_status(label: QLabel, text: str, color: str) -> None:
        label.setText(text)
        label.setStyleSheet(f"color:{color}; font-size:11px;")

    def _set_active_status(self, text: str, color: str = "#9ca3af") -> None:
        label = self._embedded_status if self._embedded_active else self._waiting_status
        self._set_label_status(label, text, color)

    def _set_default_action(self, button: QPushButton | None) -> None:
        # QDialog has one default button across all stacked pages. Set it on
        # every state transition so Enter cannot activate a hidden action from
        # a different page merely because that button was constructed later.
        for candidate in (self._continue_btn, self._verify_btn):
            candidate.setDefault(candidate is button)

    @property
    def session_may_have_changed(self) -> bool:
        return self._session_may_have_changed

    def _show_browser_choice(self, message: str = "") -> None:
        if self._closing:
            return
        self._stop_external_login()
        self._cancel_verification()
        self._embedded_active = False
        self._view.stop()
        self._continue_btn.setEnabled(self._browser_combo.count() > 0)
        self._browser_combo.setEnabled(self._browser_combo.count() > 0)
        self._browser_hint.setText(
            "Supports Google, passkeys, and magic links."
            if self._browser_combo.count() > 0
            else "No supported installed browser was found."
        )
        self._choice_error.setText(message)
        self._choice_error.setVisible(bool(message))
        if self._preferred_browser in self._installed_browsers:
            index = self._browser_combo.findData(self._preferred_browser)
            if index >= 0:
                self._browser_combo.setCurrentIndex(index)
        self._update_continue_label()
        self._show_compact_page(self._choice_page, 660, 320)
        self._set_default_action(self._continue_btn)

    def _choose_external_browser(self) -> None:
        browser_id = self._browser_combo.currentData()
        if not isinstance(browser_id, str):
            return
        if browser_id != self._preferred_browser:
            self._preferred_browser = browser_id
            self.browser_preference_changed.emit(browser_id)
        self._start_external_login(browser_id)

    def _start_external_login(self, browser_id: str | None = None) -> None:
        if self._closing or self._external_worker is not None:
            return
        selected = browser_id or self._preferred_browser
        if selected not in self._installed_browsers:
            self._show_browser_choice(
                "The selected browser is not installed. Choose another sign-in method."
            )
            return
        self._embedded_active = False
        self._embedded_auth_seen = False
        browser_label = BROWSER_LABELS[selected]
        self._waiting_heading.setText(f"Continue in {browser_label}")
        self._waiting_instructions.setText(
            f"Finish signing in to {self._provider_name()} in the {browser_label} "
            "window. AI Gauge will connect the session and close this dialog "
            "automatically."
        )
        self._set_label_status(
            self._waiting_status, f"Opening {browser_label}…", "#9ca3af"
        )
        self._show_compact_page(self._waiting_page, 620, 250)
        self._set_default_action(None)
        worker = ExternalLoginWorker(
            self._provider,
            self._login_url,
            self._account_id,
            self,
            browser_id=selected,
        )
        worker.status_changed.connect(self._on_external_status)
        worker.session_ready.connect(self._on_external_session_ready)
        worker.failed.connect(self._on_external_failed)
        self._external_worker = worker
        worker.start()

    def _provider_name(self) -> str:
        spec = provider_spec(self._provider)
        if spec is None or spec.login is None:
            return self._provider
        return spec.login.prompt_name

    def _on_external_status(self, message: str) -> None:
        if self._closing:
            return
        self._set_label_status(self._waiting_status, message, "#60a5fa")

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
        self._session_may_have_changed = True
        self._set_label_status(
            self._waiting_status, "Signed in. Verifying the session…", "#22c55e"
        )
        QTimer.singleShot(1200, self._verify)

    def _on_external_failed(self, message: str) -> None:
        if self._closing:
            return
        self._show_browser_choice(message)

    def _use_embedded_browser(self) -> None:
        if self._closing:
            return
        self._stop_external_login()
        if self._preferred_browser != "embedded":
            self._preferred_browser = "embedded"
            self.browser_preference_changed.emit("embedded")
        self._embedded_active = True
        self._embedded_auth_seen = False
        self._verify_btn.setEnabled(True)
        self._set_label_status(self._embedded_status, "Waiting for sign-in…", "#9ca3af")
        self._show_resizable_page(self._embedded_page, 960, 760)
        self._set_default_action(self._verify_btn)
        self._view.load(QUrl(self._login_url))

    def _on_embedded_page_loaded(self, ok: bool) -> None:
        """Recognize an authenticated shell, including a pre-existing session."""
        if (
            not ok
            or self._closing
            or not self._embedded_active
            or self._verifying
            or self._embedded_verify_scheduled
        ):
            return
        target = VERIFY_TARGETS.get(self._provider)
        if target is None:
            return
        landed = self._page.url()
        if not _domain_matches(landed.host(), self._provider):
            return
        if "/login" in landed.path().lower():
            return
        self._page.runJavaScript(target[1], self._on_embedded_shell_result)

    def _on_embedded_shell_result(self, result) -> None:
        if (
            result is not True
            or self._closing
            or not self._embedded_active
            or self._verifying
            or self._embedded_verify_scheduled
        ):
            return
        self._embedded_auth_seen = True
        self._session_may_have_changed = True
        self._embedded_verify_scheduled = True
        self._set_label_status(
            self._embedded_status, "Signed in. Finishing…", "#22c55e"
        )
        QTimer.singleShot(250, self._accept_embedded_session)

    def _accept_embedded_session(self) -> None:
        self._embedded_verify_scheduled = False
        if not self._closing and self._embedded_active:
            self.accept()

    def _on_embedded_cookie_added(self, cookie) -> None:
        if self._closing or not self._embedded_active:
            return
        try:
            name = bytes(cookie.name()).decode("utf-8", errors="replace")
            domain = str(cookie.domain())
        except (AttributeError, TypeError, ValueError):
            return
        if not _domain_matches(domain, self._provider):
            return
        if not _has_auth_cookie(self._provider, [{"name": name}]):
            return
        self._embedded_auth_seen = True
        self._session_may_have_changed = True
        if self._embedded_verify_scheduled or self._verifying:
            return
        self._embedded_verify_scheduled = True
        self._set_label_status(
            self._embedded_status, "Signed in. Verifying the session…", "#22c55e"
        )
        QTimer.singleShot(1000, self._verify_after_embedded_cookie)

    def _verify_after_embedded_cookie(self) -> None:
        self._embedded_verify_scheduled = False
        if not self._closing and self._embedded_active:
            self._verify()

    def _cancel_verification(self) -> None:
        timer = getattr(self, "_verify_timeout", None)
        if timer is not None:
            timer.stop()
            self._verify_timeout = None
        try:
            self._page.loadFinished.disconnect(self._on_verify_load_finished)
        except (TypeError, RuntimeError):
            pass
        self._verifying = False
        self._embedded_verify_scheduled = False

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
        self._set_label_status(
            self._embedded_status,
            "Google may refuse embedded sign-in. Cancel and click Sign in "
            "again to use the installed-browser flow.",
            "#f59e0b",
        )

    def closeEvent(self, event) -> None:
        self._closing = True
        self._cancel_verification()
        self._stop_external_login()
        self._close_popups()
        super().closeEvent(event)

    def done(self, result: int) -> None:
        self._closing = True
        self._cancel_verification()
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
        if self._verifying:
            return
        self._verifying = True
        self._verify_btn.setEnabled(False)
        if self._embedded_active:
            # The manual button is a useful fallback when a provider changes
            # its auth cookie name before AI Gauge learns about it.
            self._session_may_have_changed = True
        if self._provider not in VERIFY_TARGETS:
            self.accept()
            return
        url, check_js = VERIFY_TARGETS[self._provider]
        self._verify_url = self._verify_url_override or url
        self._verify_check_js = check_js
        self._set_active_status("Verifying session…")

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
        self._verifying = False
        if ok:
            self.accept()
            return
        self._verify_btn.setEnabled(True)
        if error:
            self._set_active_status(
                f"Could not load verification page ({error}). Try again.",
                "#ef4444",
            )
        else:
            if self._embedded_auth_seen:
                self._set_active_status(
                    "The session is signed in but still loading. Wait a moment and "
                    "try again.",
                    "#ef4444",
                )
            else:
                self._set_active_status(
                    "Not signed in yet — please complete sign-in in the window above.",
                    "#ef4444",
                )
