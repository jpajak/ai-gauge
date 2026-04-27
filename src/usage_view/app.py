from __future__ import annotations

import sys
from datetime import datetime, timedelta

from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import __version__
from .config import Config, get_github_pat, get_provider_cookie
from .cookie_dialog import CookieDialog
from .models import SnapshotStatus, UsageSnapshot
from .providers.base import Provider, ProviderSignals
from .providers.claude import ClaudeProvider
from .providers.codex import CodexProvider
from .providers.copilot import CopilotProvider
from .settings_dialog import SettingsDialog
from .webview.cookies import hydrate_all_from_keyring
from .webview.login_window import LoginWindow
from .widget import UsageWidget

LOGIN_URLS = {
    "claude": ("https://claude.ai/login", "Sign in to Claude"),
    "codex": ("https://chatgpt.com/auth/login", "Sign in to ChatGPT"),
}

_ACTIVE_MODE_MINUTES = 30


def _make_tray_icon(percent: float | None = None) -> QIcon:
    pix = QPixmap(32, 32)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if percent is None:
        painter.setBrush(QColor("#6b7280"))
    elif percent >= 90:
        painter.setBrush(QColor("#ef4444"))
    elif percent >= 75:
        painter.setBrush(QColor("#f59e0b"))
    else:
        painter.setBrush(QColor("#22c55e"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(4, 4, 24, 24)
    painter.end()
    return QIcon(pix)


def _adaptive_refresh_minutes(
    *,
    active: bool,
    active_minutes: int,
    unchanged_cycles: int,
    max_minutes: int,
) -> int:
    active_minutes = max(1, min(active_minutes, max_minutes))
    if active:
        return active_minutes
    backoff = active_minutes * (2 ** max(0, unchanged_cycles))
    return min(max_minutes, backoff)


def _snapshot_signature(snapshot: UsageSnapshot) -> tuple:
    return (
        snapshot.status.value,
        snapshot.error,
        tuple(
            (
                metric.label,
                round(metric.percent_used or 0, 1)
                if metric.percent_used is not None
                else None,
                metric.reset_label,
            )
            for metric in snapshot.metrics
        ),
    )


class App(QObject):
    """Main application controller — owns the widget, providers, refresh timer, tray."""

    def __init__(self):
        super().__init__()
        self._config = Config.load()
        self._snapshots: dict[str, UsageSnapshot] = {}
        self._signals = ProviderSignals()
        self._signals.snapshot_ready.connect(self._on_snapshot)
        self._inflight: set[str] = set()
        self._refresh_queue: list[str] = []
        self._cycle_signatures: dict[str, tuple] = {}
        self._last_cycle_signatures: dict[str, tuple] | None = None
        self._unchanged_cycles = 0
        self._active_until = datetime.now() + timedelta(minutes=_ACTIVE_MODE_MINUTES)
        self._current_refresh_manual = False

        # Push any saved session cookies into the WebEngine profiles before any
        # scrape runs, so the headless page loads as signed-in.
        hydrate_all_from_keyring()

        self._widget = UsageWidget(self._config)
        self._widget.refresh_requested.connect(lambda: self.refresh_now(manual=True))
        self._widget.settings_requested.connect(self.open_settings)
        self._widget.sign_in_requested.connect(self.open_login)
        self._widget.closed.connect(self._on_widget_closed)

        # Pre-populate provider tiles in stable order so they don't pop in.
        self._providers: dict[str, Provider] = {}
        self._build_providers()

        # System tray
        self._tray = QSystemTrayIcon(_make_tray_icon())
        self._tray.setToolTip(f"usage view {__version__}")
        menu = QMenu()
        menu.addAction("Show / Hide", self._toggle_widget)
        refresh_act = menu.addAction("Refresh now")
        refresh_act.triggered.connect(lambda: self.refresh_now(manual=True))
        menu.addAction("Settings…", self.open_settings)
        menu.addSeparator()
        quit_act = QAction("Quit", menu)
        quit_act.triggered.connect(QApplication.instance().quit)
        menu.addAction(quit_act)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

        # Auto-refresh timer
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self.refresh_now(manual=False))
        self._restart_timer()

        # First-run: open settings if nothing is configured
        if not self._has_any_credentials():
            QTimer.singleShot(200, self.open_settings)
        else:
            QTimer.singleShot(500, lambda: self.refresh_now(manual=True))

        self._widget.show()

    # ----- Lifecycle helpers -----

    def _build_providers(self) -> None:
        # Tear down any existing providers (no shared state to clean up beyond refs)
        self._providers.clear()
        if self._config.providers.claude:
            self._providers["claude"] = ClaudeProvider(parent=self)
            self._widget.ensure_tile("claude", "Claude")
        else:
            self._widget.remove_tile("claude")
        if self._config.providers.codex:
            self._providers["codex"] = CodexProvider(parent=self)
            self._widget.ensure_tile("codex", "Codex")
        else:
            self._widget.remove_tile("codex")
        if self._config.providers.copilot:
            self._providers["copilot"] = CopilotProvider(self._config)
            self._widget.ensure_tile("copilot", "Copilot")
        else:
            self._widget.remove_tile("copilot")

    def _has_any_credentials(self) -> bool:
        if get_github_pat() and self._config.providers.copilot:
            return True
        # Profile dirs exist & non-empty hint at past sign-ins
        from .config import webview_profile_dir
        for p in ("claude", "codex"):
            if not getattr(self._config.providers, p):
                continue
            d = webview_profile_dir(p)
            if d.exists() and any(d.iterdir()):
                return True
        return False

    def _restart_timer(self) -> None:
        self._timer.stop()
        self._schedule_next_refresh()

    def _schedule_next_refresh(self) -> None:
        if self._inflight or self._refresh_queue:
            return
        max_minutes = max(1, self._config.refresh_interval_minutes)
        minutes = _adaptive_refresh_minutes(
            active=datetime.now() < self._active_until,
            active_minutes=self._config.active_refresh_interval_minutes,
            unchanged_cycles=self._unchanged_cycles,
            max_minutes=max_minutes,
        )
        self._timer.start(minutes * 60_000)

    # ----- Refresh -----

    def refresh_now(self, manual: bool = True) -> None:
        if not self._providers:
            return
        if self._inflight or self._refresh_queue:
            return
        if manual:
            self._active_until = datetime.now() + timedelta(minutes=_ACTIVE_MODE_MINUTES)
            self._unchanged_cycles = 0
        self._timer.stop()
        self._current_refresh_manual = manual
        self._cycle_signatures = {}
        self._widget.set_refreshing(True)
        self._refresh_queue = list(self._providers)
        self._start_next_refresh()

    def _start_next_refresh(self) -> None:
        if self._inflight or not self._refresh_queue:
            return
        name = self._refresh_queue.pop(0)
        provider = self._providers.get(name)
        if provider is None:
            QTimer.singleShot(0, self._start_next_refresh)
            return
        self._inflight.add(name)

        def _emit(snap: UsageSnapshot, _name=name):
            self._signals.snapshot_ready.emit(snap)

        try:
            provider.refresh(_emit)
        except Exception as exc:  # noqa: BLE001
            self._signals.snapshot_ready.emit(
                UsageSnapshot(
                    provider=name,
                    status=SnapshotStatus.ERROR,
                    error=str(exc),
                )
            )

    def _on_snapshot(self, snapshot: UsageSnapshot) -> None:
        self._snapshots[snapshot.provider] = snapshot
        self._cycle_signatures[snapshot.provider] = _snapshot_signature(snapshot)
        self._inflight.discard(snapshot.provider)
        display_name = {
            "claude": "Claude",
            "codex": "Codex",
            "copilot": "Copilot",
        }.get(snapshot.provider, snapshot.provider)
        self._widget.update_snapshot(snapshot, display_name)

        if self._refresh_queue:
            QTimer.singleShot(0, self._start_next_refresh)
        else:
            changed = self._cycle_changed()
            if changed:
                self._active_until = datetime.now() + timedelta(minutes=_ACTIVE_MODE_MINUTES)
                self._unchanged_cycles = 0
            elif not self._current_refresh_manual:
                self._unchanged_cycles += 1
            self._last_cycle_signatures = dict(self._cycle_signatures)
            self._current_refresh_manual = False
            self._widget.set_refreshing(False)
            self._update_tray()
            self._schedule_next_refresh()

    def _cycle_changed(self) -> bool:
        if self._last_cycle_signatures is None:
            return True
        return self._cycle_signatures != self._last_cycle_signatures

    def _update_tray(self) -> None:
        max_pct: float | None = None
        lines: list[str] = []
        for name in ("claude", "codex", "copilot"):
            snap = self._snapshots.get(name)
            if not snap or snap.status != SnapshotStatus.OK:
                continue
            for m in snap.metrics:
                if m.percent_used is None:
                    continue
                if max_pct is None or m.percent_used > max_pct:
                    max_pct = m.percent_used
                lines.append(f"{name} {m.label}: {m.percent_used:.0f}%")
        self._tray.setIcon(_make_tray_icon(max_pct))
        self._tray.setToolTip(
            f"usage view {__version__}\n" + "\n".join(lines)
            if lines
            else f"usage view {__version__}"
        )

    # ----- Login -----

    def open_login(self, provider: str) -> None:
        if provider not in LOGIN_URLS:
            return
        url, title = LOGIN_URLS[provider]
        dlg = LoginWindow(provider, url, title)
        if dlg.exec():
            self.refresh_now(manual=True)

    def open_cookie_paste(self, provider: str) -> None:
        try:
            dlg = CookieDialog(provider)
        except ValueError:
            return
        if dlg.exec():
            # QWebEngineCookieStore commits asynchronously; give the freshly
            # injected cookie a short beat before loading the scrape page.
            QTimer.singleShot(1000, lambda: self.refresh_now(manual=True))

    # ----- Settings -----

    def open_settings(self) -> None:
        dlg = SettingsDialog(self._config, parent=self._widget)
        dlg.sign_in_clicked.connect(self.open_login)
        dlg.paste_cookie_clicked.connect(self.open_cookie_paste)
        if dlg.exec():
            dlg.apply_to(self._config)
            self._build_providers()
            self._widget.apply_window_settings()
            self._widget.show()
            self._restart_timer()
            self.refresh_now(manual=True)

    # ----- Tray -----

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_widget()

    def _toggle_widget(self) -> None:
        if self._widget.isVisible():
            self._widget.hide()
        else:
            self._widget.show()
            self._widget.raise_()
            self._widget.activateWindow()

    def _on_widget_closed(self) -> None:
        # Closing the widget hides to tray rather than quitting
        pass


def main() -> int:
    # QtWebEngine requires this attribute set before QApplication is constructed.
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    # Importing QtWebEngineWidgets before the QApplication forces its OpenGL
    # initialisation to happen at the right time.
    from PyQt6 import QtWebEngineWidgets  # noqa: F401

    QApplication.setQuitOnLastWindowClosed(False)
    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("usage-view")
    qt_app.setOrganizationName("usage-view")
    qt_app.setApplicationVersion(__version__)
    _app = App()  # noqa: F841 - keeps refs alive
    return qt_app.exec()


if __name__ == "__main__":
    sys.exit(main())
