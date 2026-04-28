from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta

from PyQt6.QtCore import QObject, QLockFile, Qt, QTimer
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QDialog, QMenu, QSystemTrayIcon

from . import __version__
from .config import Config, app_data_dir
from .cookie_dialog import CookieDialog
from .error_dialog import ErrorDetailsDialog
from .history import HistoryStore
from .logging_setup import setup_logging
from .models import SnapshotStatus, UsageSnapshot
from .providers.base import Provider, ProviderSignals
from .providers.claude import ClaudeProvider
from .providers.codex import CodexProvider
from .providers.copilot import CopilotProvider
from .settings_dialog import SettingsDialog
from .webview.cookies import hydrate_all_from_keyring
from .webview.login_window import LoginWindow
from .widget import UsageWidget

log = logging.getLogger("usage_view.app")

LOGIN_URLS = {
    "claude": ("https://claude.ai/login", "Sign in to Claude"),
    "codex": ("https://chatgpt.com/auth/login", "Sign in to ChatGPT"),
}

_ACTIVE_MODE_MINUTES = 30
_LOG_VALUE_LIMIT = 300


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
                (
                    round(metric.percent_used or 0, 1)
                    if metric.percent_used is not None
                    else None
                ),
                metric.reset_label,
            )
            for metric in snapshot.metrics
        ),
    )


def _summarize_for_log(value, *, depth: int = 0):
    if depth > 3:
        return "..."
    if isinstance(value, str):
        return (
            value
            if len(value) <= _LOG_VALUE_LIMIT
            else value[:_LOG_VALUE_LIMIT] + "..."
        )
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {
            str(k): _summarize_for_log(v, depth=depth + 1)
            for k, v in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        summarized = [_summarize_for_log(v, depth=depth + 1) for v in value[:5]]
        if len(value) > 5:
            summarized.append(f"... {len(value) - 5} more")
        return summarized
    return repr(value)


def _raw_summary(raw: dict) -> str:
    try:
        return json.dumps(_summarize_for_log(raw), sort_keys=True, default=str)
    except TypeError:
        return repr(raw)


def _acquire_instance_lock() -> QLockFile | None:
    lock_path = app_data_dir() / "usage-view.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(lock_path))
    if not lock.tryLock(100):
        return None
    return lock


class App(QObject):
    """Main application controller — owns the widget, providers, refresh timer, tray."""

    def __init__(self):
        super().__init__()
        setup_logging()
        log.info(
            "usage-view %s starting frozen=%s executable=%s cwd=%s appdata=%s",
            __version__,
            bool(getattr(sys, "frozen", False)),
            sys.executable,
            os.getcwd(),
            os.environ.get("APPDATA", ""),
        )
        self._config = Config.load()
        self._snapshots: dict[str, UsageSnapshot] = {}
        self._history = HistoryStore()
        self._signals = ProviderSignals()
        self._signals.snapshot_ready.connect(self._on_snapshot)
        self._inflight: set[str] = set()
        self._refresh_queue: list[str] = []
        self._cycle_signatures: dict[str, tuple] = {}
        self._last_cycle_signatures: dict[str, tuple] | None = None
        self._unchanged_cycles = 0
        self._active_until = datetime.now() + timedelta(minutes=_ACTIVE_MODE_MINUTES)
        self._current_refresh_manual = False
        self._settings_dialog: SettingsDialog | None = None
        self._settings_old_copilot_quota: int | None = None

        # Push any saved session cookies into the WebEngine profiles before any
        # scrape runs, so the headless page loads as signed-in.
        loaded = hydrate_all_from_keyring()
        log.info("hydrated cookies for: %s", loaded or "none")

        self._widget = UsageWidget(self._config)
        self._widget.refresh_requested.connect(lambda: self.refresh_now(manual=True))
        self._widget.settings_requested.connect(self.open_settings)
        self._widget.sign_in_requested.connect(self.open_login)
        self._widget.details_requested.connect(self.open_error_details)
        self._widget.activated_requested.connect(self._on_widget_activated)
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

        # Always start with a refresh — fresh installs see provider tiles in
        # their auth-required state with a Sign in button instead of a
        # surprise modal popup.
        QTimer.singleShot(500, lambda: self.refresh_now(manual=True))

        self._widget.show()

    # ----- Lifecycle helpers -----

    def _build_providers(self) -> None:
        # Tear down any existing providers (no shared state to clean up beyond refs)
        self._providers.clear()
        if self._config.providers.claude:
            self._providers["claude"] = ClaudeProvider(
                parent=self,
                show_design=self._config.providers.claude_design,
            )
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

    def _restart_timer(self) -> None:
        self._timer.stop()
        self._schedule_next_refresh()

    def _schedule_next_refresh(self) -> None:
        if self._inflight or self._refresh_queue:
            return
        max_minutes = max(1, self._config.refresh_interval_minutes)
        active = datetime.now() < self._active_until
        minutes = _adaptive_refresh_minutes(
            active=active,
            active_minutes=self._config.active_refresh_interval_minutes,
            unchanged_cycles=self._unchanged_cycles,
            max_minutes=max_minutes,
        )
        next_refresh_at = datetime.now() + timedelta(minutes=minutes)
        # Don't let an idle backoff stretch past a known reset — otherwise the
        # panel keeps showing 100% for tens of minutes after the limit has
        # actually rolled over. Pull the refresh forward so we re-read shortly
        # after the predicted reset.
        soon_after_reset = self._earliest_reset_refresh_time()
        if soon_after_reset is not None and soon_after_reset < next_refresh_at:
            next_refresh_at = soon_after_reset
            minutes = max(
                1,
                int((next_refresh_at - datetime.now()).total_seconds() // 60) or 1,
            )
        delay_ms = max(
            1000,
            int((next_refresh_at - datetime.now()).total_seconds() * 1000),
        )
        self._timer.start(delay_ms)
        self._widget.set_refresh_state(
            active=active,
            minutes=minutes,
            next_at=next_refresh_at,
        )

    def _earliest_reset_refresh_time(self) -> datetime | None:
        """Earliest moment the next scheduled refresh should run because some
        provider's metric is predicted to reset soon.

        Returns ``None`` when there is nothing useful to anticipate.
        """
        now = datetime.now()
        # Give the upstream a minute to commit the reset before we re-read.
        grace = timedelta(minutes=1)
        earliest: datetime | None = None
        for snap in self._snapshots.values():
            if snap.status != SnapshotStatus.OK:
                continue
            for metric in snap.metrics:
                if metric.resets_at is None:
                    continue
                if (metric.percent_used or 0) <= 0:
                    # An unused metric resetting changes nothing visible.
                    continue
                target = metric.resets_at + grace
                if target <= now:
                    continue
                if earliest is None or target < earliest:
                    earliest = target
        return earliest

    # ----- Refresh -----

    def refresh_now(self, manual: bool = True) -> None:
        if not self._providers:
            return
        if self._inflight or self._refresh_queue:
            return
        if manual:
            self._active_until = datetime.now() + timedelta(
                minutes=_ACTIVE_MODE_MINUTES
            )
            self._unchanged_cycles = 0
        self._timer.stop()
        self._current_refresh_manual = manual
        self._cycle_signatures = {}
        self._widget.set_refreshing(True)
        self._refresh_queue = list(self._providers)
        if manual:
            self._widget.mark_loading(
                {
                    name: {
                        "claude": "Claude",
                        "codex": "Codex",
                        "copilot": "Copilot",
                    }.get(name, name)
                    for name in self._refresh_queue
                }
            )
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
        if snapshot.status == SnapshotStatus.ERROR:
            log.warning(
                "snapshot error provider=%s error=%s raw_keys=%s raw_summary=%s",
                snapshot.provider,
                snapshot.error,
                sorted(snapshot.raw.keys()) if snapshot.raw else [],
                _raw_summary(snapshot.raw) if snapshot.raw else "{}",
            )
        elif snapshot.status == SnapshotStatus.AUTH_REQUIRED:
            log.info(
                "snapshot auth_required provider=%s error=%s raw_keys=%s raw_summary=%s",
                snapshot.provider,
                snapshot.error,
                sorted(snapshot.raw.keys()) if snapshot.raw else [],
                _raw_summary(snapshot.raw) if snapshot.raw else "{}",
            )
        try:
            self._history.record_snapshot(snapshot)
        except Exception:  # noqa: BLE001
            log.exception("history.record_snapshot failed")
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
                self._active_until = datetime.now() + timedelta(
                    minutes=_ACTIVE_MODE_MINUTES
                )
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

    # ----- Login / cookie paste -----

    def open_login(self, provider: str) -> None:
        if provider not in LOGIN_URLS:
            return
        url, title = LOGIN_URLS[provider]
        dlg = LoginWindow(provider, url, title)
        self._widget.suspend_always_on_top()
        try:
            accepted = bool(dlg.exec())
        finally:
            self._widget.restore_always_on_top()
        if accepted:
            self.refresh_now(manual=True)

    def open_cookie_paste(self, provider: str) -> None:
        try:
            dlg = CookieDialog(provider)
        except ValueError:
            return
        self._widget.suspend_always_on_top()
        try:
            accepted = bool(dlg.exec())
        finally:
            self._widget.restore_always_on_top()
        if accepted:
            # QWebEngineCookieStore commits asynchronously; give the freshly
            # injected cookie a short beat before loading the scrape page.
            QTimer.singleShot(1000, lambda: self.refresh_now(manual=True))

    def open_error_details(self, provider: str) -> None:
        snapshot = self._snapshots.get(provider)
        if snapshot is None or snapshot.status != SnapshotStatus.ERROR:
            return
        display_name = {
            "claude": "Claude",
            "codex": "Codex",
            "copilot": "Copilot",
        }.get(provider, provider)
        dlg = ErrorDetailsDialog(provider, display_name, snapshot, parent=self._widget)
        dlg.exec()

    # ----- Settings -----

    def open_settings(self) -> None:
        if self._settings_dialog is not None:
            self._raise_settings_dialog()
            return
        old_copilot_quota = self._config.copilot.monthly_quota
        dlg = SettingsDialog(self._config, parent=self._widget)
        dlg.setModal(False)
        dlg.setWindowModality(Qt.WindowModality.NonModal)
        dlg.sign_in_clicked.connect(self.open_login)
        dlg.paste_cookie_clicked.connect(self.open_cookie_paste)
        dlg.finished.connect(
            lambda result, dialog=dlg, old_quota=old_copilot_quota: (
                self._on_settings_finished(dialog, result, old_quota)
            )
        )
        self._settings_dialog = dlg
        self._settings_old_copilot_quota = old_copilot_quota
        self._widget.suspend_always_on_top()
        dlg.show()
        self._raise_settings_dialog()

    def _raise_settings_dialog(self) -> None:
        dlg = self._settings_dialog
        if dlg is None:
            return
        if dlg.isMinimized():
            dlg.showNormal()
        else:
            dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_settings_finished(
        self,
        dlg: SettingsDialog,
        result: int,
        old_copilot_quota: int,
    ) -> None:
        if self._settings_dialog is not dlg:
            return
        self._settings_dialog = None
        self._settings_old_copilot_quota = None
        self._widget.restore_always_on_top()
        accepted = result == QDialog.DialogCode.Accepted.value
        if accepted:
            dlg.apply_to(self._config)
            self._build_providers()
            self._widget.apply_window_settings()
            self._widget.show()
            # Copilot's metric label bakes the quota into the displayed string.
            # If the user just changed it, re-render the cached snapshot now so
            # the new denominator shows immediately rather than after a refresh.
            new_copilot_quota = self._config.copilot.monthly_quota
            if old_copilot_quota != new_copilot_quota:
                self._rerender_copilot(new_copilot_quota)
            self._restart_timer()
            self.refresh_now(manual=True)
        dlg.deleteLater()

    def _on_widget_activated(self) -> None:
        if self._settings_dialog is not None:
            self._raise_settings_dialog()

    def _rerender_copilot(self, quota: int) -> None:
        cached = self._snapshots.get("copilot")
        if cached is None or cached.status != SnapshotStatus.OK or not cached.raw:
            return
        from .providers.copilot import _build_snapshot

        try:
            self._on_snapshot(_build_snapshot(cached.raw, quota))
        except Exception:  # noqa: BLE001
            log.exception("failed to re-render copilot snapshot with new quota")

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
    instance_lock = _acquire_instance_lock()
    if instance_lock is None:
        return 0
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
    _instance_lock = instance_lock  # noqa: F841 - keep the single-instance lock alive
    return qt_app.exec()


if __name__ == "__main__":
    sys.exit(main())
