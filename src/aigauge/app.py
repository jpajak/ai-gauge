from __future__ import annotations

import atexit
import json
import logging
import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta

from PyQt6.QtCore import QObject, QLockFile, QPoint, Qt, QTimer
from PyQt6.QtGui import QAction, QColor, QCursor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QDialog, QMenu, QSystemTrayIcon

from . import __version__
from .config import (
    Config,
    account_kind,
    app_data_dir,
    browser_accounts,
    display_name_for_account,
)
from .cookie_dialog import CookieDialog
from .error_dialog import ErrorDetailsDialog
from .history import HistoryStore
from .logging_setup import setup_logging
from .menubar import render_menubar_pixmap
from .models import SnapshotStatus, UsageSnapshot
from .platforms import get_platform
from .providers.base import Provider, ProviderSignals
from .providers.claude import ClaudeProvider
from .providers.codex import CodexProvider
from .providers.copilot import CopilotProvider
from .providers.openrouter import OpenRouterProvider
from .ratio import RatioStore, sessions_per_week
from .ratio_dialog import RatioHistoryDialog
from .settings_dialog import SettingsDialog
from .webview.cookies import hydrate_all_from_keyring
from .webview.login_window import LoginWindow
from .widget import UsageWidget

log = logging.getLogger("aigauge.app")

LOGIN_URLS = {
    "claude": ("https://claude.ai/login", "Sign in to Claude"),
    "codex": ("https://chatgpt.com/auth/login", "Sign in to ChatGPT"),
}

_ACTIVE_MODE_MINUTES = 30
_STALE_ERROR_RETRY_MINUTES = 1
_HEARTBEAT_INTERVAL_MS = 5 * 60 * 1000
_LOG_VALUE_LIMIT = 300


def _make_dot_tray_icon(percent: float | None = None) -> QIcon:
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


def _enabled_providers(config: Config) -> tuple[str, ...]:
    out: list[str] = [
        account.id
        for account in browser_accounts(config)
        if getattr(config.providers, account.kind, False)
    ]
    if not out:
        providers = getattr(config, "providers", None)
        if getattr(providers, "claude", False):
            out.append("claude")
        if getattr(providers, "codex", False):
            out.append("codex")
    if config.providers.copilot:
        out.append("copilot")
    if config.providers.openrouter:
        out.append("openrouter")
    return tuple(out)


def _refresh_provider_order(providers: dict[str, Provider]) -> list[str]:
    names = list(providers)
    positions = {name: index for index, name in enumerate(names)}
    return sorted(
        names,
        key=lambda name: (0 if name == "openrouter" else 1, positions[name]),
    )


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


def _preserve_error_metrics(
    snapshot: UsageSnapshot,
    previous: UsageSnapshot | None,
) -> UsageSnapshot:
    if (
        snapshot.status == SnapshotStatus.ERROR
        and not snapshot.metrics
        and previous is not None
        and previous.status in (SnapshotStatus.OK, SnapshotStatus.ERROR)
        and previous.metrics
    ):
        return replace(snapshot, metrics=list(previous.metrics))
    return snapshot


def _acquire_instance_lock() -> QLockFile | None:
    lock_path = app_data_dir() / "ai-gauge.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(lock_path))
    if not lock.tryLock(100):
        return None
    return lock


def _flush_log_handlers() -> None:
    for handler in logging.getLogger("aigauge").handlers:
        try:
            handler.flush()
        except Exception:  # noqa: BLE001
            pass


class App(QObject):
    """Main application controller — owns the widget, providers, refresh timer, tray."""

    def __init__(self):
        super().__init__()
        setup_logging()
        log.info(
            "ai-gauge %s starting platform=%s frozen=%s executable=%s cwd=%s app_data=%s",
            __version__,
            get_platform().name,
            bool(getattr(sys, "frozen", False)),
            sys.executable,
            os.getcwd(),
            app_data_dir(),
        )
        self._started_at = datetime.now()
        self._config = Config.load()
        self._snapshots: dict[str, UsageSnapshot] = {}
        self._history = HistoryStore()
        self._ratio = RatioStore()
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
        self._install_lifecycle_logging()

        # Push any saved session cookies into the WebEngine profiles before any
        # scrape runs, so the headless page loads as signed-in.
        loaded = hydrate_all_from_keyring(self._config)
        log.info("hydrated cookies for: %s", loaded or "none")

        self._widget = UsageWidget(self._config)
        self._widget.refresh_requested.connect(lambda: self.refresh_now(manual=True))
        self._widget.settings_requested.connect(self.open_settings)
        self._widget.sign_in_requested.connect(self.open_login)
        self._widget.details_requested.connect(self.open_error_details)
        self._widget.ratio_history_requested.connect(self.open_ratio_history)
        self._widget.tile_expanded_changed.connect(self._on_tile_expanded_changed)
        self._widget.activated_requested.connect(self._on_widget_activated)
        self._widget.closed.connect(self._on_widget_closed)

        # Pre-populate provider tiles in stable order so they don't pop in.
        self._providers: dict[str, Provider] = {}
        self._build_providers()

        # System tray (or menu-bar item on macOS, or no-tray fallback on Linux)
        self._ui_mode = get_platform().default_ui_mode()
        tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        if not tray_available:
            # Stock GNOME has no system tray. Force the floating widget to be
            # the only UI and serve the same menu via right-click on it.
            log.info("system tray not available; falling back to widget-only UI")
            self._ui_mode = "floating_widget"

        self._app_menu = self._build_app_menu()
        self._native_status = None

        if self._ui_mode == "menubar":
            try:
                from .macos_status_item import NativeMacStatusItem

                self._native_status = NativeMacStatusItem(
                    on_activate=self._toggle_widget,
                    on_context=self._show_tray_menu,
                )
                self._native_status.update(
                    self._snapshots,
                    _enabled_providers(self._config),
                )
                self._native_status.set_tooltip(f"AI Gauge {__version__}")
                self._tray = None
                log.info("using native macOS status item")
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "native macOS status item unavailable; falling back to Qt tray: %s",
                    exc,
                )
                self._native_status = None

        if self._native_status is not None:
            pass
        elif tray_available:
            self._tray = QSystemTrayIcon(self._render_tray_icon())
            self._tray.setToolTip(f"AI Gauge {__version__}")
            if self._ui_mode != "menubar":
                self._tray.setContextMenu(self._app_menu)
            self._tray.activated.connect(self._on_tray_activated)
            self._tray.show()
        else:
            self._tray = None
            self._widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self._widget.customContextMenuRequested.connect(
                lambda pos: self._app_menu.exec(self._widget.mapToGlobal(pos))
            )

        # Auto-refresh timer
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self.refresh_now(manual=False))
        self._restart_timer()

        # Always start with a refresh — fresh installs see provider tiles in
        # their auth-required state with a Sign in button instead of a
        # surprise modal popup.
        QTimer.singleShot(500, lambda: self.refresh_now(manual=True))

        # On Windows/Linux the floating widget is the headline UI; on macOS
        # the menu-bar item is, and the widget appears as a popover only
        # when the user clicks the menu bar.
        if self._ui_mode == "floating_widget":
            self._widget.show()

    # ----- Lifecycle helpers -----

    def _install_lifecycle_logging(self) -> None:
        qt_app = QApplication.instance()
        if qt_app is not None:
            qt_app.aboutToQuit.connect(self._log_about_to_quit)
        atexit.register(self._log_atexit)

        self._heartbeat = QTimer(self)
        self._heartbeat.setInterval(_HEARTBEAT_INTERVAL_MS)
        self._heartbeat.timeout.connect(self._log_heartbeat)
        self._heartbeat.start()
        log.info("heartbeat enabled interval_s=%s", _HEARTBEAT_INTERVAL_MS // 1000)

    def _uptime_seconds(self) -> int:
        return max(0, int((datetime.now() - self._started_at).total_seconds()))

    def _next_refresh_seconds(self) -> int | None:
        try:
            if not hasattr(self, "_timer"):
                return None
            if not self._timer.isActive():
                return None
            return max(0, int(self._timer.remainingTime() / 1000))
        except RuntimeError:
            return None

    def _widget_visible_for_log(self) -> bool | None:
        try:
            if not hasattr(self, "_widget"):
                return None
            return self._widget.isVisible()
        except RuntimeError:
            return None

    def _lifecycle_context(self) -> dict[str, object]:
        return {
            "uptime_s": self._uptime_seconds(),
            "ui_mode": getattr(self, "_ui_mode", None),
            "widget_visible": self._widget_visible_for_log(),
            "providers": ",".join(_enabled_providers(self._config)),
            "inflight": ",".join(sorted(self._inflight)),
            "queue": ",".join(self._refresh_queue),
            "next_refresh_s": self._next_refresh_seconds(),
            "unchanged_cycles": self._unchanged_cycles,
        }

    def _log_lifecycle_event(self, event: str) -> None:
        try:
            context = self._lifecycle_context()
            log.info(
                "%s uptime_s=%s ui_mode=%s widget_visible=%s providers=%s "
                "inflight=%s queue=%s next_refresh_s=%s unchanged_cycles=%s",
                event,
                context["uptime_s"],
                context["ui_mode"],
                context["widget_visible"],
                context["providers"],
                context["inflight"],
                context["queue"],
                context["next_refresh_s"],
                context["unchanged_cycles"],
            )
        except Exception:  # noqa: BLE001
            log.exception("%s logging failed", event)
        finally:
            _flush_log_handlers()

    def _log_heartbeat(self) -> None:
        self._log_lifecycle_event("heartbeat")

    def _log_about_to_quit(self) -> None:
        self._log_lifecycle_event("qt aboutToQuit")

    def _log_atexit(self) -> None:
        self._log_lifecycle_event("python atexit")

    def _build_providers(self) -> None:
        # Tear down any existing providers (no shared state to clean up beyond refs)
        self._providers.clear()
        desired_tiles: set[str] = set()
        for account in browser_accounts(self._config):
            if not getattr(self._config.providers, account.kind, False):
                continue
            desired_tiles.add(account.id)
            if account.kind == "claude":
                self._providers[account.id] = ClaudeProvider(
                    parent=self,
                    account_id=account.id,
                )
            elif account.kind == "codex":
                self._providers[account.id] = CodexProvider(
                    parent=self,
                    account_id=account.id,
                )
            self._widget.ensure_tile(account.id, display_name_for_account(self._config, account.id))
        if self._config.providers.copilot:
            self._providers["copilot"] = CopilotProvider(self._config)
            desired_tiles.add("copilot")
            self._widget.ensure_tile("copilot", "Copilot")
        if self._config.providers.openrouter:
            self._providers["openrouter"] = OpenRouterProvider(self._config)
            desired_tiles.add("openrouter")
            self._widget.ensure_tile("openrouter", "OpenRouter")
        for tile_id in list(self._widget._tiles):  # noqa: SLF001
            if tile_id not in desired_tiles:
                self._widget.remove_tile(tile_id)
                self._snapshots.pop(tile_id, None)

    def _restart_timer(self) -> None:
        self._timer.stop()
        self._schedule_next_refresh()

    def _schedule_next_refresh(self) -> None:
        if self._inflight or self._refresh_queue:
            return
        now = datetime.now()
        max_minutes = max(1, self._config.refresh_interval_minutes)
        active = now < self._active_until
        minutes = _adaptive_refresh_minutes(
            active=active,
            active_minutes=self._config.active_refresh_interval_minutes,
            unchanged_cycles=self._unchanged_cycles,
            max_minutes=max_minutes,
        )
        next_refresh_at = now + timedelta(minutes=minutes)
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
        stale_retry = self._stale_error_retry_time(now)
        if stale_retry is not None and stale_retry < next_refresh_at:
            next_refresh_at = stale_retry
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

    def _stale_error_retry_time(self, now: datetime | None = None) -> datetime | None:
        """Soonest recovery refresh for errors that still have stale metrics."""
        if not any(
            snap.status == SnapshotStatus.ERROR and bool(snap.metrics)
            for snap in self._snapshots.values()
        ):
            return None
        return (now or datetime.now()) + timedelta(minutes=_STALE_ERROR_RETRY_MINUTES)

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
        self._refresh_queue = _refresh_provider_order(self._providers)
        if manual:
            self._widget.mark_loading(
                {
                    name: {
                        "copilot": "Copilot",
                        "openrouter": "OpenRouter",
                    }.get(name, display_name_for_account(self._config, name))
                    for name in self._refresh_queue
                }
            )
        self._start_next_refresh()

    def refresh_provider(self, provider: str) -> None:
        if provider not in self._providers:
            return
        if self._inflight or self._refresh_queue:
            return
        self._active_until = datetime.now() + timedelta(minutes=_ACTIVE_MODE_MINUTES)
        self._unchanged_cycles = 0
        self._timer.stop()
        self._current_refresh_manual = True
        self._cycle_signatures = {}
        self._widget.set_refreshing(True)
        self._refresh_queue = [provider]
        self._widget.mark_loading(
            {provider: display_name_for_account(self._config, provider)}
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
        snapshot = _preserve_error_metrics(
            snapshot,
            self._snapshots.get(snapshot.provider),
        )
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
        try:
            self._ratio.record_snapshot(snapshot)
        except Exception:  # noqa: BLE001
            log.exception("ratio.record_snapshot failed")
        display_name = display_name_for_account(self._config, snapshot.provider)
        self._widget.update_snapshot(snapshot, display_name)
        try:
            self._widget.set_ratio(
                snapshot.provider,
                self._ratio.display_estimate(snapshot.provider),
                self._ratio_recent(snapshot.provider),
                self._ratio.current_estimate(snapshot.provider),
            )
        except Exception:  # noqa: BLE001
            log.exception("widget.set_ratio failed")

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
        lines: list[str] = []
        for name in _enabled_providers(self._config):
            snap = self._snapshots.get(name)
            if not snap:
                continue
            display_name = display_name_for_account(self._config, name)
            if snap.status == SnapshotStatus.AUTH_REQUIRED:
                lines.append(f"{display_name}: setup needed")
                continue
            if snap.status == SnapshotStatus.ERROR:
                lines.append(f"{display_name}: error")
                continue
            for m in snap.metrics:
                if m.percent_used is None:
                    continue
                if m.tag:
                    continue
                lines.append(f"{display_name} {m.label}: {m.percent_used:.0f}%")
        tooltip = (
            f"AI Gauge {__version__}\n" + "\n".join(lines)
            if lines
            else f"AI Gauge {__version__}"
        )
        if self._native_status is not None:
            self._native_status.update(
                self._snapshots,
                _enabled_providers(self._config),
            )
            self._native_status.set_tooltip(tooltip)
            return
        if self._tray is None:
            return
        self._tray.setIcon(self._render_tray_icon())
        self._tray.setToolTip(tooltip)

    def _build_app_menu(self) -> QMenu:
        menu = QMenu()
        menu.addAction("Show / Hide", self._toggle_widget)
        refresh_act = menu.addAction("Refresh now")
        refresh_act.triggered.connect(lambda: self.refresh_now(manual=True))
        menu.addAction("Settings…", self.open_settings)
        menu.addSeparator()
        quit_act = QAction("Quit", menu)
        quit_act.triggered.connect(QApplication.instance().quit)
        menu.addAction(quit_act)
        return menu

    def _render_tray_icon(self) -> QIcon:
        if self._ui_mode == "menubar":
            providers = _enabled_providers(self._config)
            pixmap = render_menubar_pixmap(self._snapshots, providers)
            return QIcon(pixmap)
        max_pct: float | None = None
        for snap in self._snapshots.values():
            if snap.status != SnapshotStatus.OK:
                continue
            for m in snap.metrics:
                if m.percent_used is None:
                    continue
                if max_pct is None or m.percent_used > max_pct:
                    max_pct = m.percent_used
        return _make_dot_tray_icon(max_pct)

    # ----- Login / cookie paste -----

    def open_login(self, provider: str) -> None:
        kind = account_kind(self._config, provider)
        if kind not in LOGIN_URLS:
            return
        url, _title = LOGIN_URLS[kind]
        display_name = display_name_for_account(self._config, provider)
        dlg = LoginWindow(kind, url, f"Sign in to {display_name}", account_id=provider)
        self._widget.suspend_always_on_top()
        try:
            accepted = bool(dlg.exec())
        finally:
            self._widget.restore_always_on_top()
        if accepted:
            self.refresh_provider(provider)

    def open_cookie_paste(self, provider: str) -> None:
        kind = account_kind(self._config, provider)
        if kind is None:
            return
        try:
            dlg = CookieDialog(
                kind,
                account_id=provider,
                display_name=display_name_for_account(self._config, provider),
            )
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
            QTimer.singleShot(1000, lambda: self.refresh_provider(provider))

    def open_error_details(self, provider: str) -> None:
        snapshot = self._snapshots.get(provider)
        if snapshot is None or snapshot.status != SnapshotStatus.ERROR:
            return
        display_name = display_name_for_account(self._config, provider)
        dlg = ErrorDetailsDialog(provider, display_name, snapshot, parent=self._widget)
        dlg.exec()

    def _ratio_recent(self, provider: str) -> list[float]:
        """Recent finalized sessions/week values (oldest -> newest) for tooltips."""
        out: list[float] = []
        for record in self._ratio.history(provider)[-4:]:
            value = sessions_per_week(
                record.sum_session_delta, record.sum_weekly_delta
            )
            if value is not None:
                out.append(value)
        return out

    def _current_weekly_pct(self, provider: str) -> float | None:
        snapshot = self._snapshots.get(provider)
        if snapshot is None or snapshot.status != SnapshotStatus.OK:
            return None
        for metric in snapshot.metrics:
            if metric.label.lower() == "weekly" and metric.percent_used is not None:
                return metric.percent_used
        return None

    def open_ratio_history(self, provider: str) -> None:
        display_name = display_name_for_account(self._config, provider)
        dlg = RatioHistoryDialog(
            provider,
            display_name,
            self._ratio.history(provider),
            self._ratio.current_estimate(provider),
            weekly_pct_used=self._current_weekly_pct(provider),
            parent=self._widget,
        )
        dlg.exec()

    # ----- Settings -----

    def open_settings(self) -> None:
        if self._settings_dialog is not None:
            self._raise_settings_dialog()
            return
        old_copilot_quota = self._config.copilot.monthly_quota
        old_openrouter_budget = self._config.openrouter.daily_budget
        dlg = SettingsDialog(self._config, parent=self._widget)
        dlg.setModal(False)
        dlg.setWindowModality(Qt.WindowModality.NonModal)
        dlg.sign_in_clicked.connect(self.open_login)
        dlg.paste_cookie_clicked.connect(self.open_cookie_paste)
        dlg.finished.connect(
            lambda result, dialog=dlg, old_quota=old_copilot_quota, old_budget=old_openrouter_budget: (
                self._on_settings_finished(dialog, result, old_quota, old_budget)
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
        old_openrouter_budget: float | None,
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
            new_openrouter_budget = self._config.openrouter.daily_budget
            if old_openrouter_budget != new_openrouter_budget:
                self._rerender_openrouter(new_openrouter_budget)
            self._restart_timer()
            self.refresh_now(manual=True)
        dlg.deleteLater()

    def _on_widget_activated(self) -> None:
        if self._settings_dialog is not None:
            self._raise_settings_dialog()

    def _on_tile_expanded_changed(self, provider: str, expanded: bool) -> None:
        current = list(self._config.expanded_tiles or [])
        if expanded and provider not in current:
            current.append(provider)
        elif not expanded and provider in current:
            current.remove(provider)
        else:
            return
        self._config.expanded_tiles = current
        try:
            self._config.save()
        except Exception:  # noqa: BLE001
            log.exception("failed to persist expanded_tiles")

    def _rerender_copilot(self, quota: int) -> None:
        cached = self._snapshots.get("copilot")
        if cached is None or cached.status != SnapshotStatus.OK or not cached.raw:
            return
        from .providers.copilot import _build_snapshot

        try:
            self._on_snapshot(_build_snapshot(cached.raw, quota))
        except Exception:  # noqa: BLE001
            log.exception("failed to re-render copilot snapshot with new quota")

    def _rerender_openrouter(self, daily_budget: float | None) -> None:
        cached = self._snapshots.get("openrouter")
        if cached is None or cached.status != SnapshotStatus.OK or not cached.raw:
            return
        from .providers.openrouter import _build_snapshot

        raw = cached.raw
        top_models = [
            (str(name), float(cost))
            for name, cost in (raw.get("top_models") or [])
        ]
        try:
            self._on_snapshot(
                _build_snapshot(
                    raw.get("credits"),
                    raw.get("key", {}) or {},
                    top_models,
                    daily_budget,
                    mgmt_key_configured=bool(raw.get("mgmt_key_configured")),
                    activity_error=raw.get("activity_error"),
                    activity_date=raw.get("activity_date"),
                )
            )
        except Exception:  # noqa: BLE001
            log.exception(
                "failed to re-render openrouter snapshot with new daily budget"
            )

    # ----- Tray -----

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_widget()
        elif (
            self._ui_mode == "menubar"
            and reason == QSystemTrayIcon.ActivationReason.Context
        ):
            self._show_tray_menu()

    def _tray_anchor(self) -> QPoint:
        if self._native_status is not None:
            return self._native_status.anchor_point()
        if self._tray is not None:
            geo = self._tray.geometry()
            if not geo.isEmpty():
                return geo.bottomLeft()
        screen_geo = QApplication.primaryScreen().availableGeometry()
        return QPoint(screen_geo.right() - 220, screen_geo.top() + 22)

    def _show_tray_menu(self) -> None:
        anchor = self._tray_anchor()
        if anchor.isNull():
            anchor = QCursor.pos()
        self._app_menu.exec(anchor)

    def _toggle_widget(self) -> None:
        if self._widget.isVisible():
            self._widget.hide()
            return
        if self._ui_mode == "menubar":
            anchor = self._tray_anchor()
            anchor_x = anchor.x()
            anchor_y = anchor.y()
            self._widget.show_as_popover(anchor_x, anchor_y)
            return
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
    qt_app.setApplicationName("ai-gauge")
    qt_app.setOrganizationName("ai-gauge")
    qt_app.setApplicationVersion(__version__)
    _app = App()  # noqa: F841 - keeps refs alive
    _instance_lock = instance_lock  # noqa: F841 - keep the single-instance lock alive
    return qt_app.exec()


if __name__ == "__main__":
    sys.exit(main())
