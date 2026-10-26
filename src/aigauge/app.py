from __future__ import annotations

import atexit
import json
import logging
import os
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta

from PyQt6.QtCore import QObject, QLockFile, QPoint, Qt, QTimer
from PyQt6.QtGui import QAction, QCursor, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
)

from . import __version__
from .config import (
    BrowserAccount,
    Config,
    account_display_name,
    account_kind,
    app_data_dir,
    browser_accounts,
    display_name_for_account,
    provider_order,
    qt_scale_factor_env,
)
from .cookie_dialog import CookieDialog
from .error_dialog import ErrorDetailsDialog
from .history import HistoryStore
from .icons import app_icon
from .logging_setup import setup_logging
from .menubar import render_menubar_pixmap
from .models import SnapshotStatus, UsageSnapshot
from .platforms import autostart_command, get_platform
from .providers.base import Provider, ProviderSignals
from .providers.registry import (
    PROVIDER_SPECS,
    account_family,
    account_kinds,
    api_key_kinds,
    browser_session_kinds,
    provider_spec,
    single_specs,
)
from .ratio import RatioStore, sessions_per_week
from .ratio_dialog import RatioHistoryDialog
from .settings_dialog import SettingsDialog
from .webview import runtime as webengine
from .webview.cookies import clear_browser_session, hydrate_all_from_keyring
from .widget import UsageWidget
from .usage_cache import invalidate_usage_cache, write_usage_cache

# Resolved when the user first opens a sign-in window rather than imported at
# module scope: .webview.login_window pulls in QtWebEngine, and importing
# Chromium aborts on a session with no GL context (issue #7). Kept as a module
# attribute so it remains patchable.
LoginWindow = None


def _login_window_class():
    global LoginWindow
    if LoginWindow is None:
        from .webview.login_window import LoginWindow as _resolved

        LoginWindow = _resolved
    return LoginWindow

log = logging.getLogger("aigauge.app")

LOGIN_URLS = {
    spec.id: (spec.login.url, spec.login.window_title)
    for spec in PROVIDER_SPECS
    if spec.login is not None
}

_ACTIVE_MODE_MINUTES = 30
_STALE_ERROR_RETRY_MINUTES = 1
_HEARTBEAT_INTERVAL_MS = 5 * 60 * 1000
_LOG_VALUE_LIMIT = 300


def _enabled_providers(config: Config) -> tuple[str, ...]:
    configured_accounts = getattr(config, "browser_accounts", None)
    # (family, id) pairs: the order below groups by family, and sorted() is
    # stable so accounts of one kind keep their account-list order.
    entries: list[tuple[str, str]] = [
        (account.kind, account.id)
        for account in browser_accounts(config)
        if getattr(config.providers, account.kind, False)
    ]
    # Compatibility for pre-multi-account config-like objects. An explicit
    # empty list is authoritative and must not resurrect removed accounts.
    if configured_accounts is None:
        entries.extend(
            (kind, kind)
            for kind in account_kinds()
            if getattr(config.providers, kind, False)
        )
    entries.extend(
        (spec.id, spec.id)
        for spec in single_specs()
        if getattr(config.providers, spec.id, False)
    )
    order = provider_order(config)
    rank = {kind: index for index, kind in enumerate(order)}
    entries.sort(key=lambda entry: rank.get(entry[0], len(order)))
    return tuple(provider_id for _kind, provider_id in entries)


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
        # A configuration error (a mis-set CLI path) will not heal on its own,
        # so old numbers would sit there as "stale" for as long as it lasts.
        and not snapshot.raw.get("config_error")
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

    # Class-level default so App stubs built without __init__ read None.
    _local_usage = None

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
        self._instance_lock: QLockFile | None = None
        self._config = Config.load()
        self._snapshots: dict[str, UsageSnapshot] = {}
        self._cleared_sessions: set[str] = set()
        self._history = HistoryStore()
        self._ratio = RatioStore()
        # Created only while local usage tracking is on; see _sync_local_usage.
        self._local_usage = None
        self._sync_local_usage()
        self._signals = ProviderSignals()
        self._signals.snapshot_ready.connect(self._on_snapshot)
        self._inflight: set[str] = set()
        self._refresh_queue: list[str] = []
        self._cycle_signatures: dict[str, tuple] = {}
        self._last_cycle_signatures: dict[str, tuple] | None = None
        self._unchanged_cycles = 0
        self._active_until = datetime.now() + timedelta(minutes=_ACTIVE_MODE_MINUTES)
        self._current_refresh_manual = False
        self._pending_manual_refresh = False
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
        self._widget.quit_requested.connect(QApplication.instance().quit)
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
        self._install_widget_context_menu()
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
                    self._config,
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
            qt_app.aboutToQuit.connect(self._invalidate_usage_cache)
            qt_app.aboutToQuit.connect(self._shutdown_local_usage)
        atexit.register(self._log_atexit)
        atexit.register(self._invalidate_usage_cache)

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

    def _invalidate_usage_cache(self) -> None:
        try:
            invalidate_usage_cache()
        except OSError:
            log.exception("failed to invalidate MCP usage cache")

    def _sync_usage_cache(self) -> None:
        if not getattr(self._config, "mcp_enabled", False):
            self._invalidate_usage_cache()
            return
        try:
            write_usage_cache(self._snapshots)
        except (OSError, ValueError):
            log.exception("failed to publish MCP usage cache")

    def _build_providers(self) -> None:
        # Tear down any existing providers (no shared state to clean up beyond refs)
        self._providers.clear()
        desired_tiles: set[str] = set()
        for account in browser_accounts(self._config):
            if not getattr(self._config.providers, account.kind, False):
                continue
            desired_tiles.add(account.id)
            # browser_accounts() only returns kinds the registry knows.
            spec = provider_spec(account.kind)
            self._providers[account.id] = spec.make(self._config, account, self)
            self._widget.ensure_tile(
                account.id,
                display_name_for_account(self._config, account.id),
            )
        for spec in single_specs():
            if not getattr(self._config.providers, spec.id, False):
                continue
            self._providers[spec.id] = spec.make(self._config, None, self)
            desired_tiles.add(spec.id)
            self._widget.ensure_tile(spec.id, spec.display_name)
        for tile_id in list(self._widget._tiles):  # noqa: SLF001
            if tile_id not in desired_tiles:
                self._widget.remove_tile(tile_id)
                self._snapshots.pop(tile_id, None)
        self._widget.apply_provider_order()
        self._sync_usage_cache()

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
                    name: display_name_for_account(self._config, name)
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
        if (
            snapshot.provider in self._cleared_sessions
            and snapshot.status != SnapshotStatus.AUTH_REQUIRED
        ):
            # A refresh that began before the user cleared the session may
            # still finish with authenticated data. Keep the account signed
            # out until the user explicitly signs in again.
            snapshot = UsageSnapshot(
                provider=snapshot.provider,
                status=SnapshotStatus.AUTH_REQUIRED,
                error="Sign-in cleared from AI Gauge.",
            )
        snapshot = _preserve_error_metrics(
            snapshot,
            self._snapshots.get(snapshot.provider),
        )
        self._snapshots[snapshot.provider] = snapshot
        if getattr(self._config, "mcp_enabled", False):
            self._sync_usage_cache()
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
        closed_periods = []
        try:
            closed_periods = self._history.record_snapshot(snapshot)
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
        try:
            self._local_usage_on_snapshot(snapshot, closed_periods)
        except Exception:  # noqa: BLE001
            log.exception("local usage snapshot hook failed")

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
                self._config,
            )
            self._native_status.set_tooltip(tooltip)
            return
        if self._tray is None:
            return
        self._tray.setIcon(self._render_tray_icon())
        self._tray.setToolTip(tooltip)

    def _build_app_menu(self) -> QMenu:
        menu = QMenu()

        self._compact_view_action = QAction("Compact view", menu)
        self._compact_view_action.setCheckable(True)
        self._compact_view_action.triggered.connect(self._widget.set_collapsed)
        menu.addAction(self._compact_view_action)

        self._show_header_action = QAction("Show header", menu)
        self._show_header_action.setCheckable(True)
        self._show_header_action.triggered.connect(self._widget.set_header_visible)
        menu.addAction(self._show_header_action)

        self._always_on_top_action = QAction("Always on top", menu)
        self._always_on_top_action.setCheckable(True)
        self._always_on_top_action.triggered.connect(self._widget.set_always_on_top)
        menu.addAction(self._always_on_top_action)

        self._snap_to_corners_action = QAction("Snap to corners", menu)
        self._snap_to_corners_action.setCheckable(True)
        self._snap_to_corners_action.triggered.connect(
            self._widget.set_snap_to_corners
        )
        menu.addAction(self._snap_to_corners_action)

        menu.addSeparator()
        self._show_hide_action = menu.addAction("Hide window", self._toggle_widget)
        refresh_act = menu.addAction("Refresh now")
        refresh_act.triggered.connect(lambda: self.refresh_now(manual=True))
        self._details_menu = menu.addMenu("Usage details")
        self._details_menu.aboutToShow.connect(self._populate_details_menu)
        menu.addAction("Settings…", self.open_settings)
        menu.addSeparator()
        quit_act = QAction("Quit", menu)
        quit_act.triggered.connect(QApplication.instance().quit)
        menu.addAction(quit_act)
        menu.aboutToShow.connect(self._sync_app_menu_actions)
        self._sync_app_menu_actions()
        return menu

    def _install_widget_context_menu(self) -> None:
        """Expose the app menu from the widget even when a tray is available."""
        self._widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._widget.customContextMenuRequested.connect(
            self._show_widget_context_menu
        )

    def _show_widget_context_menu(self, pos: QPoint) -> None:
        self._app_menu.exec(self._widget.mapToGlobal(pos))

    def _sync_app_menu_actions(self) -> None:
        self._compact_view_action.setChecked(self._config.window.collapsed)
        self._show_header_action.setChecked(self._config.window.show_header)
        self._always_on_top_action.setChecked(self._config.window.always_on_top)
        self._snap_to_corners_action.setChecked(
            self._config.window.snap_to_corners
        )
        self._snap_to_corners_action.setVisible(
            getattr(self, "_ui_mode", "floating_widget") == "floating_widget"
        )
        self._show_hide_action.setText(
            "Hide window" if self._widget.isVisible() else "Show window"
        )

    def _render_tray_icon(self) -> QIcon:
        if self._ui_mode == "menubar":
            providers = _enabled_providers(self._config)
            pixmap = render_menubar_pixmap(
                self._snapshots, providers, config=self._config
            )
            return QIcon(pixmap)
        return app_icon()

    # ----- Login / cookie paste -----

    def _draft_browser_account(self, provider: str) -> BrowserAccount | None:
        dialog = self._settings_dialog
        get_draft = getattr(dialog, "draft_browser_account", None)
        return get_draft(provider) if callable(get_draft) else None

    def _show_webengine_unavailable(self, display_name: str) -> None:
        QMessageBox.warning(
            self._widget,
            "Browser sign-in unavailable",
            f"{display_name} signs in through an embedded browser, which "
            "needs hardware or software OpenGL.\n\n"
            f"{webengine.unavailable_reason()}\n\n"
            "Providers that use an API key or token (GitHub Copilot, "
            "OpenRouter) work without it. If you believe this is wrong, set "
            "AIGAUGE_FORCE_WEBENGINE=1 to skip this check.",
        )

    def _remember_sign_in_browser(self, browser_id: str) -> None:
        if browser_id not in {
            "ask",
            "chrome",
            "edge",
            "brave",
            "chromium",
            "embedded",
        }:
            return
        self._config.sign_in_browser = browser_id
        self._config.save()
        update_draft = getattr(self._settings_dialog, "set_sign_in_browser", None)
        if callable(update_draft):
            update_draft(browser_id)

    def open_login(self, provider: str) -> None:
        draft = self._draft_browser_account(provider)
        kind = draft.kind if draft is not None else account_kind(self._config, provider)
        if kind in api_key_kinds():
            # API-key accounts have no sign-in; take the user to the key field.
            self.open_settings()
            if self._settings_dialog is not None:
                self._settings_dialog.show_provider(kind, provider)
            return
        if kind in LOGIN_URLS:
            url, _title = LOGIN_URLS[kind]
        else:
            return
        display_name = (
            account_display_name(draft)
            if draft is not None
            else display_name_for_account(self._config, provider)
        )
        if not webengine.is_available():
            self._show_webengine_unavailable(display_name)
            return
        dlg = _login_window_class()(
            kind,
            url,
            f"Sign in to {display_name}",
            account_id=provider,
            browser_preference=getattr(self._config, "sign_in_browser", "ask"),
        )
        preference_changed = getattr(dlg, "browser_preference_changed", None)
        if preference_changed is not None:
            preference_changed.connect(self._remember_sign_in_browser)
        self._widget.suspend_always_on_top()
        try:
            accepted = bool(dlg.exec())
        finally:
            self._widget.restore_always_on_top()
        session_may_have_changed = bool(
            getattr(dlg, "session_may_have_changed", False)
        )
        if accepted or session_may_have_changed:
            self._cleared_sessions.discard(provider)
            # Embedded sessions already live in this profile; imported external
            # cookies commit asynchronously. Either way, refresh now instead of
            # leaving a valid sign-in stale until the five-minute active timer.
            QTimer.singleShot(750, lambda: self.refresh_provider(provider))

    def open_cookie_paste(self, provider: str) -> None:
        draft = self._draft_browser_account(provider)
        kind = draft.kind if draft is not None else account_kind(self._config, provider)
        spec = provider_spec(kind)
        if spec is None or not spec.paste_cookie:
            return
        display_name = (
            account_display_name(draft)
            if draft is not None
            else display_name_for_account(self._config, provider)
        )
        try:
            dlg = CookieDialog(
                kind,
                account_id=provider,
                display_name=display_name,
            )
        except ValueError:
            return
        self._widget.suspend_always_on_top()
        try:
            accepted = bool(dlg.exec())
        finally:
            self._widget.restore_always_on_top()
        if accepted:
            self._cleared_sessions.discard(provider)
            # QWebEngineCookieStore commits asynchronously; give the freshly
            # injected cookie a short beat before loading the scrape page.
            QTimer.singleShot(1000, lambda: self.refresh_provider(provider))

    def clear_sign_in(self, provider: str) -> None:
        draft = self._draft_browser_account(provider)
        kind = draft.kind if draft is not None else account_kind(self._config, provider)
        if kind is None:
            return
        display_name = (
            account_display_name(draft)
            if draft is not None
            else display_name_for_account(self._config, provider)
        )
        parent = self._settings_dialog or self._widget
        answer = QMessageBox.question(
            parent,
            "Clear sign-in",
            f"Remove {display_name}'s saved sign-in from AI Gauge?\n\n"
            "This signs the account out of AI Gauge only. It does not revoke "
            "sessions in other browsers or devices.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            clear_browser_session(provider)
        except RuntimeError as exc:
            log.exception("failed to fully clear browser session provider=%s", provider)
            QMessageBox.warning(
                parent,
                "Sign-in was not fully cleared",
                f"AI Gauge could not remove every copy of the saved session:\n\n{exc}",
            )
            return

        self._cleared_sessions.add(provider)
        snapshot = UsageSnapshot(
            provider=provider,
            status=SnapshotStatus.AUTH_REQUIRED,
            error="Sign-in cleared from AI Gauge.",
        )
        self._snapshots[provider] = snapshot
        if getattr(self._config, "mcp_enabled", False):
            self._sync_usage_cache()
        if provider in self._providers:
            self._widget.update_snapshot(snapshot, display_name)
        self._update_tray()

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

    def open_ratio_history(self, provider: str, show_usage: bool = True) -> None:
        display_name = display_name_for_account(self._config, provider)
        usage_tab = None
        service = self._local_usage
        if service is not None and service.provider_for_account(provider) is not None:
            from .local_usage.usage_tab import UsageCostTab

            try:
                usage_tab = UsageCostTab(
                    service, provider, display_name, self._snapshots.get(provider)
                )
                service.maybe_import()
            except Exception:  # noqa: BLE001
                log.exception("usage and cost tab failed to build")
                usage_tab = None
        dlg = RatioHistoryDialog(
            provider,
            display_name,
            self._ratio.history(provider),
            self._ratio.current_estimate(provider),
            weekly_pct_used=self._current_weekly_pct(provider),
            parent=self._widget,
            usage_tab=usage_tab,
            open_usage_tab=show_usage,
        )
        dlg.exec()
        if usage_tab is not None:
            usage_tab.detach()

    def _populate_details_menu(self) -> None:
        menu = self._details_menu
        menu.clear()
        accounts = [
            account
            for account in self._config.browser_accounts
            if account.enabled and account.kind in browser_session_kinds()
        ]
        if not accounts:
            empty = menu.addAction("No Claude or Codex accounts")
            empty.setEnabled(False)
            return
        for account in accounts:
            menu.addAction(
                display_name_for_account(self._config, account.id),
                # triggered passes `checked` first; keep it off account_id.
                lambda _checked=False, account_id=account.id: self.open_ratio_history(
                    account_id, show_usage=True
                ),
            )

    # ----- Local usage -----

    def _sync_local_usage(self, old_local_usage=None) -> None:
        """Start or stop local usage tracking to match the saved settings.

        While tracking is off nothing is created: no database, no worker, and
        no log file is opened.
        """
        if not self._config.local_usage.enabled:
            if self._local_usage is not None:
                self._local_usage.shutdown()
                self._local_usage.deleteLater()
                self._local_usage = None
            return
        from .local_usage.service import (
            BACKFILL,
            LocalUsageService,
            assigned_account,
        )
        from .local_usage.store import PROVIDERS

        if self._local_usage is None:
            try:
                self._local_usage = LocalUsageService(self._config)
            except Exception:  # noqa: BLE001
                log.exception("local usage tracking could not start")
                return
            self._local_usage.history_records = lambda: list(self._history.iter_history())
        if old_local_usage is None or old_local_usage == self._config.local_usage:
            return
        before = self._config.model_copy(update={"local_usage": old_local_usage})
        newly_tracked = tuple(
            provider
            for provider in PROVIDERS
            if assigned_account(self._config, provider) is not None
            and assigned_account(before, provider) is None
        )
        service = self._local_usage
        fresh = tuple(
            provider
            for provider in newly_tracked
            if service.store.source_file_count(provider) == 0
            and getattr(self._config.local_usage, provider).start_from is None
        )
        if not fresh:
            service.request_import()
            return
        from .local_usage import enable_dialog
        from .local_usage.importer import provider_roots, scan_provider

        scans = [
            scan_provider(
                provider,
                provider_roots(provider, getattr(self._config.local_usage, provider).log_root),
            )
            for provider in fresh
        ]
        choice = enable_dialog.ask_backfill(self._widget, scans)
        if choice == enable_dialog.IMPORT_HISTORY:
            service.request_import(BACKFILL)
        else:
            service.start_from_now(fresh)
            service.request_import()

    def _import_local_usage_from_settings(self, panel) -> None:
        """Import history from the open Settings dialog, without waiting for OK.

        Saves the tab's local usage choices and starts tracking first, so the
        import runs and shows progress while Settings stays open.
        """
        panel.apply_to(self._config)
        try:
            self._config.save()
        except OSError:
            log.exception("failed to save local usage settings")
        self._sync_local_usage()
        service = self._local_usage
        if service is None:
            return
        panel.set_service(service)
        service.import_history()

    def _clear_local_usage_from_settings(self, panel) -> None:
        service = self._local_usage
        if service is not None:
            service.clear_data()
            # Keep tracking without silently re-reading every log.
            service.start_from_now()
        else:
            from .local_usage.store import DB_FILENAME

            database = app_data_dir() / DB_FILENAME
            for suffix in ("", "-wal", "-shm"):
                try:
                    database.with_name(database.name + suffix).unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    log.exception("could not delete local usage database")
        panel.after_clear()

    def _local_usage_on_snapshot(self, snapshot: UsageSnapshot, closed_periods) -> None:
        service = self._local_usage
        if service is None or snapshot.status != SnapshotStatus.OK:
            return
        provider = service.provider_for_account(snapshot.provider)
        if provider is None:
            return
        if provider == "claude":
            service.note_claude_periods(
                snapshot.provider,
                [r for r in self._history.current_records() if r.provider == snapshot.provider],
                [r for r in closed_periods if r.provider == snapshot.provider],
            )
        # The import runs in the background; window summaries update when it ends.
        service.request_import()

    def _shutdown_local_usage(self) -> None:
        if self._local_usage is not None:
            self._local_usage.shutdown()

    # ----- Settings -----

    def set_instance_lock(self, lock: QLockFile | None) -> None:
        """Hold the single-instance lock so restart() can release it cleanly."""
        self._instance_lock = lock

    def restart(self) -> None:
        """Relaunch the app so a new UI scale (QT_SCALE_FACTOR) takes effect.

        QT_SCALE_FACTOR is latched when QApplication is constructed, so a scale
        change only applies on a fresh process.
        """
        log.info("restarting to apply new UI scale")
        # Release the single-instance lock first so the relaunched process can
        # acquire it without racing this one's shutdown.
        if self._instance_lock is not None:
            try:
                self._instance_lock.unlock()
            except Exception:  # noqa: BLE001
                pass
        _flush_log_handlers()
        # Drop any inherited QT_SCALE_FACTOR so the child recomputes it from the
        # freshly saved config rather than reusing this process's value.
        child_env = dict(os.environ)
        child_env.pop("QT_SCALE_FACTOR", None)
        try:
            subprocess.Popen(autostart_command(), env=child_env, close_fds=True)
        except Exception:  # noqa: BLE001
            log.exception("failed to relaunch for UI scale change")
        QApplication.instance().quit()

    def open_settings(self) -> None:
        if self._settings_dialog is not None:
            self._raise_settings_dialog()
            return
        old_copilot_quota = self._config.copilot.monthly_quota
        old_openrouter_budget = self._config.openrouter.daily_budget
        dlg = SettingsDialog(
            self._config,
            parent=self._widget,
            local_usage_service=self._local_usage,
        )
        dlg.setModal(False)
        dlg.setWindowModality(Qt.WindowModality.NonModal)
        dlg.sign_in_clicked.connect(self.open_login)
        dlg.paste_cookie_clicked.connect(self.open_cookie_paste)
        dlg.clear_sign_in_clicked.connect(self.clear_sign_in)
        panel = getattr(dlg, "local_usage_panel", None)
        if panel is not None:
            panel.import_history_requested.connect(
                lambda panel=panel: self._import_local_usage_from_settings(panel)
            )
            panel.clear_data_requested.connect(
                lambda panel=panel: self._clear_local_usage_from_settings(panel)
            )
        dlg.finished.connect(
            lambda result, dialog=dlg, old_quota=old_copilot_quota, old_budget=old_openrouter_budget: (
                self._on_settings_finished(dialog, result, old_quota, old_budget)
            )
        )
        self._settings_dialog = dlg
        self._settings_old_copilot_quota = old_copilot_quota
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
        accepted = result == QDialog.DialogCode.Accepted.value
        if accepted:
            old_local_usage = self._config.local_usage.model_copy(deep=True)
            dlg.apply_to(self._config)
            self._build_providers()
            try:
                self._sync_local_usage(old_local_usage)
            except Exception:  # noqa: BLE001
                log.exception("local usage settings change failed")
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
            if getattr(dlg, "browser_session_clear_errors", None):
                account_ids = [
                    account_id
                    for account_id, _error in dlg.browser_session_clear_errors
                ]
                QMessageBox.warning(
                    self._widget,
                    "Some sign-ins were not fully cleared",
                    "AI Gauge removed the account settings, but could not clear "
                    "every saved session for: " + ", ".join(account_ids) + ".",
                )
            if getattr(dlg, "start_at_login_error", False):
                QMessageBox.warning(
                    self._widget,
                    "Start at login",
                    "AI Gauge couldn't update the Windows start-at-login task. "
                    "Your other settings were saved.",
                )
            if getattr(dlg, "ui_scale_changed", False):
                answer = QMessageBox.question(
                    self._widget,
                    "Restart to apply scale",
                    "AI Gauge needs to restart to apply the new UI scale.\n\n"
                    "Restart now?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if answer == QMessageBox.StandardButton.Yes:
                    self.restart()
                    return
        dlg.deleteLater()

    def _on_widget_activated(self) -> None:
        if self._settings_dialog is not None:
            self._raise_settings_dialog()

    def _on_tile_expanded_changed(self, provider: str, expanded: bool) -> None:
        family = provider_spec(account_family(provider))
        compact_collapsible = family is not None and family.compact_collapse
        if compact_collapsible:
            current = list(getattr(self._config, "collapsed_tiles", []) or [])
            if not expanded and provider not in current:
                current.append(provider)
            elif expanded and provider in current:
                current.remove(provider)
            else:
                return
            self._config.collapsed_tiles = current
            log_name = "collapsed_tiles"
        else:
            current = list(self._config.expanded_tiles or [])
            if expanded and provider not in current:
                current.append(provider)
            elif not expanded and provider in current:
                current.remove(provider)
            else:
                return
            self._config.expanded_tiles = current
            log_name = "expanded_tiles"
        try:
            self._config.save()
        except Exception:  # noqa: BLE001
            log.exception("failed to persist %s", log_name)

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


def _install_excepthook() -> None:
    """Log otherwise-fatal uncaught exceptions.

    PyQt aborts the process when an exception escapes a slot, and a windowed
    build has nowhere to print the traceback — so it vanishes silently. Routing
    it through the file log leaves a diagnosable trace.
    """
    previous = sys.excepthook

    def _hook(exc_type, exc, tb):
        log.critical("unhandled exception", exc_info=(exc_type, exc, tb))
        previous(exc_type, exc, tb)

    sys.excepthook = _hook


def _apply_ui_scale_env() -> None:
    """Translate the saved UI scale into QT_SCALE_FACTOR before QApplication.

    Qt latches the factor when QApplication is constructed, so a change only
    takes effect on the next launch — the Settings dialog tells the user as
    much. An explicit QT_SCALE_FACTOR in the environment always wins.
    """
    if "QT_SCALE_FACTOR" in os.environ:
        return
    try:
        factor = qt_scale_factor_env(Config.load())
    except Exception:  # noqa: BLE001 - never block startup over a scale read
        return
    if factor is not None:
        os.environ["QT_SCALE_FACTOR"] = factor


def main() -> int:
    instance_lock = _acquire_instance_lock()
    if instance_lock is None:
        return 0
    setup_logging()
    _install_excepthook()
    # QtWebEngine requires this attribute set before QApplication is
    # constructed. Setting it is just a flag — it initialises nothing — so it
    # is safe on machines with no GL at all.
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    # QtWebEngine itself is deliberately NOT imported here. It is Chromium, and
    # importing it initialises a GL context, which aborts on a session with no
    # GLX/EGL — an XRDP desktop, a GPU-less VM (issue #7). The gauge is plain
    # QtWidgets and needs no GL, so the import is deferred to the first
    # provider that actually opens a browser. See webview/runtime.py.

    _apply_ui_scale_env()

    qt_app = QApplication(sys.argv)
    # Must be set on the live instance: a tray-resident app keeps running with
    # no visible windows, and closing a transient dialog/message box must not
    # quit it. (Called pre-construction this silently no-ops.)
    qt_app.setQuitOnLastWindowClosed(False)
    qt_app.setApplicationName("ai-gauge")
    qt_app.setOrganizationName("ai-gauge")
    qt_app.setApplicationVersion(__version__)
    qt_app.setWindowIcon(app_icon())
    _app = App()  # noqa: F841 - keeps refs alive
    _app.set_instance_lock(instance_lock)
    return qt_app.exec()


if __name__ == "__main__":
    sys.exit(main())
