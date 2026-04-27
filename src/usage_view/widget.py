from __future__ import annotations

from datetime import datetime
from typing import Callable

from PyQt6.QtCore import QPoint, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QMouseEvent, QPainter, QPen
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .config import Config
from .models import SnapshotStatus, UsageSnapshot

ROW_BAR_HEIGHT = 8
PROVIDER_ORDER = ("claude", "codex", "copilot")


def _provider_sort_key(provider: str) -> tuple[int, str]:
    try:
        return (PROVIDER_ORDER.index(provider), provider)
    except ValueError:
        return (len(PROVIDER_ORDER), provider)


def _format_relative(dt: datetime | None) -> str:
    if dt is None:
        return ""
    delta = dt - datetime.now()
    secs = int(delta.total_seconds())
    if secs <= 0:
        return "now"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        h, m = divmod(secs // 60, 60)
        return f"{h}h {m:02d}m" if m else f"{h}h"
    days = secs / 86400
    return f"{days:.1f}d"


def _format_age(dt: datetime) -> str:
    secs = int((datetime.now() - dt).total_seconds())
    if secs < 5:
        return "just now"
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    h = secs // 3600
    return f"{h}h ago"


def _color_for_percent(p: float | None) -> str:
    if p is None:
        return "#6b7280"
    if p >= 90:
        return "#ef4444"
    if p >= 75:
        return "#f59e0b"
    if p >= 50:
        return "#eab308"
    return "#22c55e"


def _short_error_reason(error: str | None) -> str:
    """One-word tag for the most common failure modes, appended to the 'error' label.

    Matches against substrings of the error string returned by providers and the
    scraper. Falls back to plain "error" when nothing matches.
    """
    if not error:
        return "error"
    e = error.lower()
    if "timeout" in e:
        return "error · timeout"
    if "failed to load" in e or "load failed" in e:
        return "error · load failed"
    if "layout" in e:
        return "error · layout changed"
    if "extractor returned null" in e or "no data extracted" in e:
        return "error · no data"
    if "github" in e or "api" in e:
        return "error · api"
    if "not signed in" in e or "auth" in e:
        return "error · signed out"
    return "error"


class _MetricRow(QWidget):
    """A single label / bar / pct / reset row."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.label = QLabel()
        self.label.setStyleSheet("color: #d1d5db; font-size: 11px;")
        self.label.setMinimumWidth(70)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(ROW_BAR_HEIGHT)
        self.bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.pct = QLabel("--")
        self.pct.setStyleSheet("color: #f3f4f6; font-size: 11px; font-weight: 600;")
        self.pct.setFixedWidth(34)
        self.pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.reset = QLabel("")
        self.reset.setStyleSheet("color: #9ca3af; font-size: 10px;")
        self.reset.setFixedWidth(58)
        self.reset.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 1, 0, 1)
        layout.setSpacing(6)
        layout.addWidget(self.label)
        layout.addWidget(self.bar, 1)
        layout.addWidget(self.pct)
        layout.addWidget(self.reset)

    def set_metric(
        self,
        label: str,
        percent: float | None,
        resets_at: datetime | None,
        reset_label: str | None = None,
        note: str | None = None,
    ) -> None:
        self.label.setText(label)
        self.setToolTip(note or "")
        if percent is None:
            self.bar.setValue(0)
            self.pct.setText("--")
        else:
            self.bar.setValue(int(round(percent)))
            self.pct.setText(f"{percent:.0f}%")
        color = _color_for_percent(percent)
        self.bar.setStyleSheet(
            f"QProgressBar {{ background:#374151; border:none; border-radius:3px; }}"
            f"QProgressBar::chunk {{ background:{color}; border-radius:3px; }}"
        )
        rel = reset_label if reset_label is not None else _format_relative(resets_at)
        self.reset.setText(rel)
        if reset_label:
            self.reset.setToolTip(note or reset_label)
        elif resets_at:
            self.reset.setToolTip(resets_at.strftime("%Y-%m-%d %H:%M"))
        else:
            self.reset.setToolTip("")


class _ProviderTile(QFrame):
    """A provider section: header line + N metric rows."""

    sign_in_requested = pyqtSignal(str)  # provider name
    details_requested = pyqtSignal(str)  # provider name (when error label is clicked)

    def __init__(self, provider: str, display_name: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.provider = provider
        self.setFrameShape(QFrame.Shape.NoFrame)

        self.header = QLabel(display_name)
        self.header.setStyleSheet("color: #e5e7eb; font-size: 12px; font-weight: 700;")

        self.status = QLabel("loading…")
        self.status.setStyleSheet("color: #6b7280; font-size: 10px; font-style: italic;")
        self.status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.linkActivated.connect(
            lambda _href: self.details_requested.emit(self.provider)
        )

        self.action_btn = QPushButton("Sign in")
        self.action_btn.setVisible(False)
        self.action_btn.setFixedHeight(20)
        self.action_btn.setStyleSheet(
            "QPushButton { background:#4b5563; color:#f3f4f6; border:none; "
            "border-radius:3px; padding:0 8px; font-size:10px; }"
            "QPushButton:hover { background:#6b7280; }"
        )
        self.action_btn.clicked.connect(lambda: self.sign_in_requested.emit(self.provider))

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.addWidget(self.header)
        header_row.addStretch(1)
        header_row.addWidget(self.action_btn)
        header_row.addWidget(self.status)

        self._rows: list[_MetricRow] = []

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(6, 4, 6, 4)
        self._layout.setSpacing(2)
        self._layout.addLayout(header_row)

    def set_snapshot(self, snapshot: UsageSnapshot | None) -> None:
        if snapshot is None:
            self.status.setText("loading…")
            self.status.setStyleSheet(
                "color: #6b7280; font-size: 10px; font-style: italic;"
            )
            self.status.setToolTip("")
            self.status.setCursor(Qt.CursorShape.ArrowCursor)
            self.action_btn.setVisible(False)
            self._set_rows([])
            return

        if snapshot.status == SnapshotStatus.AUTH_REQUIRED:
            self.status.setText("not signed in")
            self.status.setStyleSheet(
                "color: #f59e0b; font-size: 10px; font-style: normal;"
            )
            self.status.setToolTip(snapshot.error or "")
            self.status.setCursor(Qt.CursorShape.ArrowCursor)
            self.action_btn.setVisible(self.provider in ("claude", "codex"))
            self._set_rows([])
            return

        if snapshot.status == SnapshotStatus.ERROR:
            label = _short_error_reason(snapshot.error)
            self.status.setText(
                f'<a href="details" style="color:#ef4444; text-decoration:none;">{label}</a>'
            )
            self.status.setStyleSheet(
                "color: #ef4444; font-size: 10px; font-style: normal;"
            )
            self.status.setToolTip(
                (snapshot.error or "unknown error") + "\n\nClick for details."
            )
            self.status.setCursor(Qt.CursorShape.PointingHandCursor)
            self.action_btn.setVisible(False)
            self._set_rows([])
            return

        # OK
        self.status.setText("")
        self.status.setStyleSheet(
            "color: #9ca3af; font-size: 10px; font-style: normal;"
        )
        self.status.setToolTip("")
        self.status.setCursor(Qt.CursorShape.ArrowCursor)
        self.action_btn.setVisible(False)
        self._set_rows(
            [
                (m.label, m.percent_used, m.resets_at, m.reset_label, m.note)
                for m in snapshot.metrics
            ]
        )

    def _set_rows(
        self,
        rows: list[tuple[str, float | None, datetime | None, str | None, str | None]],
    ) -> None:
        # Grow / shrink the row pool to match
        while len(self._rows) < len(rows):
            r = _MetricRow(self)
            self._rows.append(r)
            self._layout.addWidget(r)
        while len(self._rows) > len(rows):
            r = self._rows.pop()
            self._layout.removeWidget(r)
            r.deleteLater()
        for row, (label, pct, reset, reset_label, note) in zip(self._rows, rows):
            row.set_metric(label, pct, reset, reset_label, note)


class UsageWidget(QWidget):
    """The compact always-on-top window."""

    refresh_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    sign_in_requested = pyqtSignal(str)
    details_requested = pyqtSignal(str)
    closed = pyqtSignal()

    def __init__(self, config: Config, parent: QWidget | None = None):
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool,
        )
        self._config = config
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        # Background is drawn in paintEvent; no widget-level stylesheet — that
        # would cascade into child dialogs (Settings) and break their layout.
        self.setWindowOpacity(config.window.opacity)

        self._apply_always_on_top(config.window.always_on_top)

        self._tiles: dict[str, _ProviderTile] = {}
        self._last_fetch_at: datetime | None = None

        # Header bar
        title = QLabel(f"usage view {__version__}")
        title.setToolTip(f"usage-view {__version__}")
        title.setStyleSheet("color:#9ca3af; font-size:10px; font-weight:600;")

        self.cadence_label = QLabel("")
        self.cadence_label.setStyleSheet("color:#6b7280; font-size:10px;")
        self.cadence_label.setToolTip("")

        self.refresh_btn = self._mini_button("↻", "Refresh now")
        self.refresh_btn.clicked.connect(self.refresh_requested.emit)

        self.settings_btn = self._mini_button("⚙", "Settings")
        self.settings_btn.clicked.connect(self.settings_requested.emit)

        self.close_btn = self._mini_button("✕", "Hide window")
        self.close_btn.clicked.connect(self.hide)

        self.age_label = QLabel("")
        self.age_label.setStyleSheet("color:#6b7280; font-size:10px;")

        header = QHBoxLayout()
        header.setContentsMargins(8, 4, 4, 2)
        header.setSpacing(4)
        header.addWidget(title)
        header.addWidget(self.cadence_label)
        header.addStretch(1)
        header.addWidget(self.age_label)
        header.addWidget(self.refresh_btn)
        header.addWidget(self.settings_btn)
        header.addWidget(self.close_btn)

        self._tile_layout = QVBoxLayout()
        self._tile_layout.setContentsMargins(2, 0, 2, 4)
        self._tile_layout.setSpacing(2)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addLayout(header)
        outer.addLayout(self._tile_layout)

        # Height is layout-driven (refit on tile/snapshot changes); width is
        # user-controlled via the saved config.
        self.resize(QSize(config.window.width, config.window.height))
        if config.window.x is not None and config.window.y is not None:
            self.move(QPoint(config.window.x, config.window.y))

        # Drag-by-anywhere
        self._drag_offset: QPoint | None = None

        # Update "Xs ago" label every second
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._refresh_age_label)
        self._tick.start(1000)

    def _mini_button(self, glyph: str, tooltip: str) -> QPushButton:
        btn = QPushButton(glyph)
        btn.setToolTip(tooltip)
        btn.setFixedSize(20, 20)
        btn.setStyleSheet(
            "QPushButton { background:transparent; color:#9ca3af; border:none; "
            "font-size:13px; }"
            "QPushButton:hover { color:#f3f4f6; }"
        )
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        return btn

    def ensure_tile(self, provider: str, display_name: str) -> _ProviderTile:
        if provider not in self._tiles:
            tile = _ProviderTile(provider, display_name, self)
            tile.sign_in_requested.connect(self.sign_in_requested.emit)
            tile.details_requested.connect(self.details_requested.emit)
            self._tiles[provider] = tile
            self._insert_tile_in_provider_order(provider, tile)
            self._refit_height()
        return self._tiles[provider]

    def _insert_tile_in_provider_order(self, provider: str, tile: _ProviderTile) -> None:
        provider_rank = _provider_sort_key(provider)
        index = self._tile_layout.count()
        for i in range(self._tile_layout.count()):
            existing = self._tile_layout.itemAt(i).widget()
            if not isinstance(existing, _ProviderTile):
                continue
            if _provider_sort_key(existing.provider) > provider_rank:
                index = i
                break
        self._tile_layout.insertWidget(index, tile)

    def remove_tile(self, provider: str) -> None:
        tile = self._tiles.pop(provider, None)
        if tile is None:
            return
        self._tile_layout.removeWidget(tile)
        tile.deleteLater()
        self._refit_height()

    def update_snapshot(self, snapshot: UsageSnapshot, display_name: str) -> None:
        tile = self.ensure_tile(snapshot.provider, display_name)
        tile.set_snapshot(snapshot)
        self._last_fetch_at = max(snapshot.fetched_at, self._last_fetch_at or snapshot.fetched_at)
        self._refresh_age_label()
        self._refit_height()

    def mark_loading(self, providers: dict[str, str]) -> None:
        for provider, display_name in providers.items():
            self.ensure_tile(provider, display_name).set_snapshot(None)
        self._refit_height()

    def _refit_height(self) -> None:
        """Resize the window vertically to match the layout's preferred height.

        Width stays user-controlled. Deferred to the next event-loop tick so
        Qt has flushed any pending tile add/remove or stylesheet updates first.
        """
        QTimer.singleShot(0, self._do_refit_height)

    def _do_refit_height(self) -> None:
        target = self.sizeHint().height()
        if target > 0 and target != self.height():
            self.resize(self.width(), target)

    def set_refreshing(self, refreshing: bool) -> None:
        self.refresh_btn.setEnabled(not refreshing)
        if refreshing:
            self.age_label.setText("refreshing…")

    def set_refresh_state(self, active: bool, minutes: int) -> None:
        """Show the next-refresh cadence in the header (e.g. 'active · 5m')."""
        mode = "active" if active else "idle"
        self.cadence_label.setText(f"· {mode} {minutes}m")
        self.cadence_label.setToolTip(
            f"In {mode} mode — next auto-refresh in ~{minutes} min."
        )
        # Brighter when actively polling, dimmer when slowed down.
        color = "#9ca3af" if active else "#6b7280"
        self.cadence_label.setStyleSheet(f"color:{color}; font-size:10px;")

    def _refresh_age_label(self) -> None:
        if self._last_fetch_at is None:
            self.age_label.setText("")
            return
        self.age_label.setText(_format_age(self._last_fetch_at))

    def _apply_always_on_top(self, on: bool) -> None:
        flags = self.windowFlags()
        if on:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    def apply_window_settings(self) -> None:
        """Re-read window-related fields from config and apply."""
        was_visible = self.isVisible()
        self.setWindowOpacity(self._config.window.opacity)
        self._apply_always_on_top(self._config.window.always_on_top)
        if was_visible:
            self.show()  # re-applying flags hides the window

    # ----- drag-to-move -----

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._drag_offset = None
        # Persist position
        self._config.window.x = self.x()
        self._config.window.y = self.y()
        self._config.save()

    def closeEvent(self, event):  # noqa: N802
        self._config.window.width = self.width()
        self._config.window.height = self.height()
        self._config.save()
        self.closed.emit()
        super().closeEvent(event)

    # Subtle rounded background
    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#111827"))
        painter.setPen(QPen(QColor("#1f2937"), 1))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 8, 8)
