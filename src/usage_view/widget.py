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

from .config import Config
from .models import SnapshotStatus, UsageSnapshot

ROW_BAR_HEIGHT = 8


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
        rel = _format_relative(resets_at)
        self.reset.setText(rel)
        if resets_at:
            self.reset.setToolTip(resets_at.strftime("%Y-%m-%d %H:%M"))


class _ProviderTile(QFrame):
    """A provider section: header line + N metric rows."""

    sign_in_requested = pyqtSignal(str)  # provider name

    def __init__(self, provider: str, display_name: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.provider = provider
        self.setFrameShape(QFrame.Shape.NoFrame)

        self.header = QLabel(display_name)
        self.header.setStyleSheet("color: #e5e7eb; font-size: 12px; font-weight: 700;")

        self.status = QLabel("")
        self.status.setStyleSheet("color: #9ca3af; font-size: 10px;")
        self.status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

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
            self.status.setText("--")
            self.action_btn.setVisible(False)
            self._set_rows([])
            return

        if snapshot.status == SnapshotStatus.AUTH_REQUIRED:
            self.status.setText("not signed in")
            self.status.setStyleSheet("color: #f59e0b; font-size: 10px;")
            self.action_btn.setVisible(self.provider in ("claude", "codex"))
            self._set_rows([])
            return

        if snapshot.status == SnapshotStatus.ERROR:
            self.status.setText("error")
            self.status.setStyleSheet("color: #ef4444; font-size: 10px;")
            self.status.setToolTip(snapshot.error or "unknown error")
            self.action_btn.setVisible(False)
            self._set_rows([])
            return

        # OK
        self.status.setText("")
        self.status.setStyleSheet("color: #9ca3af; font-size: 10px;")
        self.status.setToolTip("")
        self.action_btn.setVisible(False)
        self._set_rows(
            [(m.label, m.percent_used, m.resets_at, m.note) for m in snapshot.metrics]
        )

    def _set_rows(
        self,
        rows: list[tuple[str, float | None, datetime | None, str | None]],
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
        for row, (label, pct, reset, note) in zip(self._rows, rows):
            row.set_metric(label, pct, reset, note)


class UsageWidget(QWidget):
    """The compact always-on-top window."""

    refresh_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    sign_in_requested = pyqtSignal(str)
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
        title = QLabel("usage view")
        title.setStyleSheet("color:#9ca3af; font-size:10px; font-weight:600;")

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
        outer.addStretch(1)

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
            self._tiles[provider] = tile
            self._tile_layout.addWidget(tile)
        return self._tiles[provider]

    def remove_tile(self, provider: str) -> None:
        tile = self._tiles.pop(provider, None)
        if tile is None:
            return
        self._tile_layout.removeWidget(tile)
        tile.deleteLater()

    def update_snapshot(self, snapshot: UsageSnapshot, display_name: str) -> None:
        tile = self.ensure_tile(snapshot.provider, display_name)
        tile.set_snapshot(snapshot)
        self._last_fetch_at = max(snapshot.fetched_at, self._last_fetch_at or snapshot.fetched_at)
        self._refresh_age_label()

    def set_refreshing(self, refreshing: bool) -> None:
        self.refresh_btn.setEnabled(not refreshing)
        if refreshing:
            self.age_label.setText("refreshing…")

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
