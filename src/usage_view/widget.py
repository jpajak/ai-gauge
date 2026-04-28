from __future__ import annotations

from datetime import datetime, timedelta

from PyQt6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .config import (
    Config,
    WINDOW_COLLAPSED_HEIGHT,
    WINDOW_MAX_HEIGHT,
    WINDOW_MIN_HEIGHT,
    WINDOW_WIDTH,
)
from .models import SnapshotStatus, UsageSnapshot

ROW_BAR_HEIGHT = 8
PROVIDER_ORDER = ("claude", "codex", "copilot")


def _clamp_height(value: int) -> int:
    return max(WINDOW_MIN_HEIGHT, min(value, WINDOW_MAX_HEIGHT))


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


def _format_countdown(dt: datetime) -> str:
    secs = int((dt - datetime.now()).total_seconds())
    if secs <= 0:
        return "now"
    if secs < 60:
        return "<1m"
    if secs < 3600:
        return f"{(secs + 59) // 60}m"
    h, m = divmod((secs + 59) // 60, 60)
    return f"{h}h {m:02d}m" if m else f"{h}h"


# Severity bands are shared between the expanded row bars and the compact
# chips. Each band pairs a bright tone (used in row bars, where the percent
# label sits next to the bar) with a darker tone of the same hue family
# (used in chips, where text sits on top of the fill). Same band → same
# color name in both views.
def _color_for_percent(p: float | None) -> str:
    if p is None:
        return "#6b7280"
    if p >= 95:
        return "#ef4444"  # red-500
    if p >= 80:
        return "#f97316"  # orange-500
    if p >= 60:
        return "#f59e0b"  # amber-500
    return "#22c55e"  # green-500


def _chip_fill_for_percent(p: float | None) -> str:
    if p is None:
        return "#374151"
    if p >= 95:
        return "#b91c1c"  # red-700
    if p >= 80:
        return "#c2410c"  # orange-700
    if p >= 60:
        return "#b45309"  # amber-700
    return "#15803d"  # green-700


def _format_summary_percent(p: float | None) -> str:
    return "--" if p is None else f"{p:.0f}%"


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


class _SummaryChip(QWidget):
    """Pill-shaped chip with a colored fill bar showing usage percent.

    Two redundant signals: fill length (how full the pill is) and fill color
    (severity). White text is drawn on top and stays readable on both the
    dark base and the darker-tone fill colors.
    """

    _BASE = QColor("#1f2937")
    _BORDER = QColor("#374151")
    _TEXT = QColor("#f9fafb")
    _NEUTRAL_FILL = QColor("#374151")
    _AUTH_FILL = QColor("#92400e")  # amber-800 — wants action

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._text = ""
        self._percent: float | None = None
        self._fill_color = self._NEUTRAL_FILL
        font = self.font()
        font.setPixelSize(11)
        font.setBold(True)
        self.setFont(font)
        self.setFixedHeight(18)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def text(self) -> str:
        return self._text

    def set_state(
        self,
        text: str,
        percent: float | None,
        kind: str,
    ) -> None:
        """kind ∈ {"ok", "loading", "auth", "error"}."""
        self._text = text
        if kind == "ok":
            self._percent = percent
            self._fill_color = QColor(_chip_fill_for_percent(percent))
        elif kind == "auth":
            self._percent = 100.0
            self._fill_color = self._AUTH_FILL
        else:  # error or loading — neutral, empty
            self._percent = None
            self._fill_color = self._NEUTRAL_FILL
        fm = self.fontMetrics()
        self.setFixedWidth(fm.horizontalAdvance(text) + 18)
        self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        radius = rect.height() / 2

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)

        # Base
        painter.setClipPath(path)
        painter.fillRect(rect, self._BASE)

        # Fill bar — left-to-right, proportional to percent
        if self._percent is not None and self._percent > 0:
            ratio = max(0.0, min(1.0, self._percent / 100.0))
            fill_rect = QRectF(rect.x(), rect.y(), rect.width() * ratio, rect.height())
            painter.fillRect(fill_rect, self._fill_color)

        # Border
        painter.setClipping(False)
        pen = QPen(self._BORDER)
        pen.setWidth(1)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(
            rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius
        )

        # Text
        painter.setPen(self._TEXT)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._text)


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
        self.pct.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self.reset = QLabel("")
        self.reset.setStyleSheet("color: #9ca3af; font-size: 10px;")
        self.reset.setFixedWidth(58)
        self.reset.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

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
        # Restore determinate range in case this row was previously a skeleton.
        if self.bar.maximum() == 0:
            self.bar.setRange(0, 100)
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

    def set_skeleton(self, label: str = "Session") -> None:
        """Indeterminate placeholder while waiting for first data.

        Qt animates a stripe inside the chunk when ``range == (0, 0)``; the
        bar still respects the QSS chunk color, so we get a muted shimmer.
        """
        self.label.setText(label)
        self.setToolTip("")
        self.bar.setRange(0, 0)
        self.bar.setStyleSheet(
            "QProgressBar { background:#1f2937; border:none; border-radius:3px; }"
            "QProgressBar::chunk { background:#4b5563; border-radius:3px; }"
        )
        self.pct.setText("")
        self.reset.setText("")
        self.reset.setToolTip("")


class _ProviderTile(QFrame):
    """A provider section: header line + N metric rows."""

    sign_in_requested = pyqtSignal(str)  # provider name
    details_requested = pyqtSignal(str)  # provider name (when error label is clicked)

    def __init__(self, provider: str, display_name: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.provider = provider
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.header = QLabel(display_name)
        self.header.setStyleSheet("color: #e5e7eb; font-size: 12px; font-weight: 700;")

        self.status = QLabel("loading…")
        self.status.setStyleSheet(
            "color: #6b7280; font-size: 10px; font-style: italic;"
        )
        self.status.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
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
        self.action_btn.clicked.connect(
            lambda: self.sign_in_requested.emit(self.provider)
        )

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

        # Refresh-in-progress dim. Animates between 1.0 and 0.55 so the user
        # sees a brief breath when refresh starts/completes instead of a snap.
        self._refreshing = False
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity_effect)
        self._opacity_anim = QPropertyAnimation(
            self._opacity_effect, b"opacity", self
        )
        self._opacity_anim.setDuration(200)
        self._opacity_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

        # Show skeleton state immediately so first launch isn't a blank tile.
        self.set_snapshot(None)

    def set_refreshing(self, refreshing: bool) -> None:
        if self._refreshing == refreshing:
            return
        self._refreshing = refreshing
        target = 0.55 if refreshing else 1.0
        self._opacity_anim.stop()
        self._opacity_anim.setStartValue(self._opacity_effect.opacity())
        self._opacity_anim.setEndValue(target)
        self._opacity_anim.start()

    def set_snapshot(self, snapshot: UsageSnapshot | None) -> None:
        if snapshot is None:
            self.status.setText("loading…")
            self.status.setStyleSheet(
                "color: #6b7280; font-size: 10px; font-style: italic;"
            )
            self.status.setToolTip("")
            self.status.setCursor(Qt.CursorShape.ArrowCursor)
            self.action_btn.setVisible(False)
            self._set_skeleton(["Session"])
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
            r.hide()
            r.setParent(None)
            r.deleteLater()
        for row, (label, pct, reset, reset_label, note) in zip(self._rows, rows):
            row.set_metric(label, pct, reset, reset_label, note)
        self._layout.invalidate()
        self.updateGeometry()

    def _set_skeleton(self, labels: list[str]) -> None:
        while len(self._rows) < len(labels):
            r = _MetricRow(self)
            self._rows.append(r)
            self._layout.addWidget(r)
        while len(self._rows) > len(labels):
            r = self._rows.pop()
            self._layout.removeWidget(r)
            r.hide()
            r.setParent(None)
            r.deleteLater()
        for row, label in zip(self._rows, labels):
            row.set_skeleton(label)
        self._layout.invalidate()
        self.updateGeometry()


class UsageWidget(QWidget):
    """The compact always-on-top window."""

    refresh_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    sign_in_requested = pyqtSignal(str)
    details_requested = pyqtSignal(str)
    activated_requested = pyqtSignal()
    closed = pyqtSignal()

    def __init__(self, config: Config, parent: QWidget | None = None):
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool,
        )
        self._config = config
        self.setFixedWidth(WINDOW_WIDTH)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        # Background is drawn in paintEvent; no widget-level stylesheet — that
        # would cascade into child dialogs (Settings) and break their layout.
        self.setWindowOpacity(config.window.opacity)

        self._apply_always_on_top(config.window.always_on_top)

        self._tiles: dict[str, _ProviderTile] = {}
        self._snapshots: dict[str, UsageSnapshot | None] = {}
        self._last_fetch_at: datetime | None = None
        self._refresh_mode: str | None = None
        self._refresh_interval_minutes: int | None = None
        self._next_refresh_at: datetime | None = None
        self._collapsed = config.window.collapsed
        self._always_on_top_suspensions = 0

        # Header bar
        title = QLabel(f"usage view {__version__}")
        title.setToolTip(f"usage-view {__version__}")
        title.setStyleSheet("color:#9ca3af; font-size:10px; font-weight:600;")

        self.cadence_label = QLabel("")
        self.cadence_label.setStyleSheet("color:#6b7280; font-size:10px;")
        self.cadence_label.setToolTip("")

        self.refresh_btn = self._mini_button("↻", "Refresh now")
        self.refresh_btn.clicked.connect(self.refresh_requested.emit)

        self.collapse_btn = self._mini_button("−", "Collapse to compact view")
        self.collapse_btn.clicked.connect(lambda: self.set_collapsed(True))

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
        header.addWidget(self.collapse_btn)
        header.addWidget(self.settings_btn)
        header.addWidget(self.close_btn)

        self._header_widget = QWidget(self)
        self._header_widget.setLayout(header)
        self._header_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        self._collapsed_label = QLabel("")
        self._collapsed_label.setStyleSheet(
            "color:#e5e7eb; font-size:10px; font-weight:600;"
        )
        self._collapsed_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )

        self._expand_btn = self._mini_button("+", "Expand")
        self._expand_btn.clicked.connect(lambda: self.set_collapsed(False))

        self._collapsed_widget = QWidget(self)
        self._collapsed_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        collapsed_outer = QVBoxLayout(self._collapsed_widget)
        collapsed_outer.setContentsMargins(8, 4, 8, 6)
        collapsed_outer.setSpacing(4)

        collapsed_header = QHBoxLayout()
        collapsed_header.setContentsMargins(0, 0, 0, 0)
        collapsed_header.setSpacing(4)
        collapsed_title = QLabel(f"usage view {__version__}")
        collapsed_title.setStyleSheet(
            "color:#9ca3af; font-size:10px; font-weight:600;"
        )
        collapsed_header.addWidget(collapsed_title)
        self._collapsed_cadence_label = QLabel("")
        self._collapsed_cadence_label.setStyleSheet("color:#6b7280; font-size:10px;")
        collapsed_header.addWidget(self._collapsed_cadence_label)
        collapsed_header.addStretch(1)
        self._collapsed_age_label = QLabel("")
        self._collapsed_age_label.setStyleSheet("color:#6b7280; font-size:10px;")
        collapsed_header.addWidget(self._collapsed_age_label)
        collapsed_header.addWidget(self._expand_btn)

        self._collapsed_summary_layout = QHBoxLayout()
        self._collapsed_summary_layout.setContentsMargins(0, 0, 0, 0)
        self._collapsed_summary_layout.setSpacing(5)
        self._collapsed_summary_layout.addWidget(self._collapsed_label)
        self._collapsed_summary_layout.addStretch(1)

        collapsed_outer.addLayout(collapsed_header)
        collapsed_outer.addLayout(self._collapsed_summary_layout)

        self._tile_container = QWidget(self)
        self._tile_container.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self._tile_layout = QVBoxLayout(self._tile_container)
        self._tile_layout.setContentsMargins(2, 0, 2, 4)
        self._tile_layout.setSpacing(2)
        self._tile_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._collapsed_widget)
        outer.addWidget(self._header_widget)
        outer.addWidget(self._tile_container)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Height is layout-driven (refit on tile/snapshot changes); width is
        # intentionally fixed because the frameless widget has no resize handle.
        self.resize(
            QSize(
                WINDOW_WIDTH,
                _clamp_height(config.window.height),
            )
        )
        if config.window.x is not None and config.window.y is not None:
            self.move(QPoint(config.window.x, config.window.y))

        # Drag-by-anywhere
        self._drag_offset: QPoint | None = None

        # Update "Xs ago" and next-refresh countdown labels every second.
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._refresh_header_labels)
        self._tick.start(1000)
        self._apply_collapsed_state(save=False)

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

    def _insert_tile_in_provider_order(
        self, provider: str, tile: _ProviderTile
    ) -> None:
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
        self._snapshots.pop(provider, None)
        if tile is None:
            return
        self._tile_layout.removeWidget(tile)
        tile.deleteLater()
        self._refresh_collapsed_summary()
        self._refit_height()

    def update_snapshot(self, snapshot: UsageSnapshot, display_name: str) -> None:
        tile = self.ensure_tile(snapshot.provider, display_name)
        self._snapshots[snapshot.provider] = snapshot
        tile.set_snapshot(snapshot)
        tile.set_refreshing(False)
        self._last_fetch_at = max(
            snapshot.fetched_at, self._last_fetch_at or snapshot.fetched_at
        )
        self._refresh_header_labels()
        self._refresh_collapsed_summary()
        self._refit_height()

    def mark_loading(self, providers: dict[str, str]) -> None:
        """Signal a refresh is in progress without wiping prior data.

        Tiles that already have a snapshot stay populated and just dim; tiles
        that have never received data keep their skeleton state. Each tile
        un-dims as its individual snapshot arrives in ``update_snapshot``.
        """
        for provider, display_name in providers.items():
            tile = self.ensure_tile(provider, display_name)
            if self._snapshots.get(provider) is None:
                tile.set_snapshot(None)
            tile.set_refreshing(True)
        self._refresh_collapsed_summary()
        self._tile_layout.invalidate()
        self._tile_container.updateGeometry()
        self.updateGeometry()
        self.layout().invalidate()
        self.layout().activate()
        self._do_refit_height()

    def _refit_height(self) -> None:
        """Resize the window vertically to match the layout's preferred height.

        Width stays fixed. Deferred to the next event-loop tick so
        Qt has flushed any pending tile add/remove or stylesheet updates first.
        """
        QTimer.singleShot(0, self._do_refit_height)

    def _do_refit_height(self) -> None:
        if self._collapsed:
            if self.height() != WINDOW_COLLAPSED_HEIGHT or self.width() != WINDOW_WIDTH:
                self.resize(WINDOW_WIDTH, WINDOW_COLLAPSED_HEIGHT)
            return
        self._tile_layout.invalidate()
        self._tile_container.updateGeometry()
        self.updateGeometry()
        self.layout().invalidate()
        target_height = _clamp_height(self.sizeHint().height())
        target_width = WINDOW_WIDTH
        if target_height != self.height() or target_width != self.width():
            self.resize(target_width, target_height)

    def set_refreshing(self, refreshing: bool) -> None:
        self.refresh_btn.setEnabled(not refreshing)
        if refreshing:
            self.age_label.setText("refreshing…")
            self.cadence_label.setText("· refreshing")
            self.cadence_label.setToolTip("Refresh is currently running.")
        self._refresh_collapsed_summary()

    def set_refresh_state(
        self,
        active: bool,
        minutes: int,
        next_at: datetime | None = None,
    ) -> None:
        """Show refresh mode plus a live countdown to the next scheduled run."""
        mode = "active" if active else "idle"
        self._refresh_mode = mode
        self._refresh_interval_minutes = minutes
        self._next_refresh_at = next_at or datetime.now() + timedelta(minutes=minutes)
        self._refresh_header_labels()

    def _refresh_header_labels(self) -> None:
        self._refresh_age_label()
        self._refresh_cadence_label()
        self._refresh_collapsed_summary()

    def _refresh_age_label(self) -> None:
        text = "" if self._last_fetch_at is None else _format_age(self._last_fetch_at)
        self.age_label.setText(text)
        self._collapsed_age_label.setText(text)

    def _refresh_cadence_label(self) -> None:
        if self._refresh_mode is None or self._next_refresh_at is None:
            self.cadence_label.setText("")
            self.cadence_label.setToolTip("")
            return
        remaining = _format_countdown(self._next_refresh_at)
        text = f"· {self._refresh_mode} next {remaining}"
        self.cadence_label.setText(text)
        self._collapsed_cadence_label.setText(text)
        interval = self._refresh_interval_minutes or 0
        tooltip = (
            f"In {self._refresh_mode} mode — {interval} min cadence. "
            f"Next auto-refresh: {self._next_refresh_at.strftime('%Y-%m-%d %H:%M:%S')}."
        )
        self.cadence_label.setToolTip(tooltip)
        self._collapsed_cadence_label.setToolTip(tooltip)
        color = "#9ca3af" if self._refresh_mode == "active" else "#6b7280"
        self.cadence_label.setStyleSheet(f"color:{color}; font-size:10px;")
        self._collapsed_cadence_label.setStyleSheet(f"color:{color}; font-size:10px;")

    def _session_summary_for(self, provider: str) -> str:
        display = {
            "claude": "Claude",
            "codex": "Codex",
            "copilot": "Copilot",
        }.get(provider, provider.title())
        snapshot = self._snapshots.get(provider)
        if snapshot is None:
            return f"{display} --"
        if snapshot.status == SnapshotStatus.AUTH_REQUIRED:
            return f"{display} sign in"
        if snapshot.status == SnapshotStatus.ERROR:
            return f"{display} error"
        metric = next(
            (m for m in snapshot.metrics if m.label.lower() == "session"),
            snapshot.metrics[0] if snapshot.metrics else None,
        )
        return f"{display} {_format_summary_percent(metric.percent_used if metric else None)}"

    def _refresh_collapsed_summary(self) -> None:
        self._clear_collapsed_summary()
        if not self._tiles:
            self._collapsed_summary_layout.insertWidget(0, self._collapsed_label)
            self._collapsed_label.setText("No providers")
            return
        self._collapsed_label.setText("")
        self._collapsed_label.hide()
        for provider in sorted(self._tiles, key=_provider_sort_key):
            self._collapsed_summary_layout.insertWidget(
                max(0, self._collapsed_summary_layout.count() - 1),
                self._summary_chip(provider),
            )

    def _clear_collapsed_summary(self) -> None:
        while self._collapsed_summary_layout.count() > 1:
            item = self._collapsed_summary_layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self._collapsed_label:
                widget.deleteLater()
        self._collapsed_label.show()

    def _summary_chip(self, provider: str) -> _SummaryChip:
        display = {
            "claude": "Claude",
            "codex": "Codex",
            "copilot": "Copilot",
        }.get(provider, provider.title())
        snapshot = self._snapshots.get(provider)
        percent: float | None = None
        text = f"{display} --"
        tooltip = ""
        kind = "loading"
        if snapshot is None:
            tooltip = "Waiting for first refresh."
        elif snapshot.status == SnapshotStatus.AUTH_REQUIRED:
            text = f"{display} sign in"
            tooltip = snapshot.error or "Sign in required."
            kind = "auth"
        elif snapshot.status == SnapshotStatus.ERROR:
            text = f"{display} error"
            tooltip = snapshot.error or "Refresh failed."
            kind = "error"
        else:
            metric = next(
                (m for m in snapshot.metrics if m.label.lower() == "session"),
                snapshot.metrics[0] if snapshot.metrics else None,
            )
            percent = metric.percent_used if metric else None
            text = f"{display} {_format_summary_percent(percent)}"
            tooltip = metric.note if metric and metric.note else ""
            kind = "ok"

        chip = _SummaryChip()
        chip.set_state(text, percent, kind)
        chip.setToolTip(tooltip)
        return chip

    def set_collapsed(self, collapsed: bool) -> None:
        if self._collapsed == collapsed:
            return
        self._collapsed = collapsed
        self._config.window.collapsed = collapsed
        self._config.window.width = WINDOW_WIDTH
        if not collapsed:
            self._config.window.height = _clamp_height(self.height())
        self._config.save()
        self._apply_collapsed_state(save=False)

    def _apply_collapsed_state(self, *, save: bool) -> None:
        self._collapsed_widget.setVisible(self._collapsed)
        self._header_widget.setVisible(not self._collapsed)
        self._tile_container.setVisible(not self._collapsed)
        self._refresh_collapsed_summary()
        if self._collapsed:
            self.setFixedSize(WINDOW_WIDTH, WINDOW_COLLAPSED_HEIGHT)
        else:
            self.setFixedWidth(WINDOW_WIDTH)
            self.setMinimumHeight(WINDOW_MIN_HEIGHT)
            self.setMaximumHeight(WINDOW_MAX_HEIGHT)
            self._refit_height()
        if save:
            self._config.window.collapsed = self._collapsed
            self._config.save()

    def _apply_always_on_top(self, on: bool) -> None:
        flags = self.windowFlags()
        if on:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    def suspend_always_on_top(self) -> None:
        """Drop always-on-top so a spawned browser window can come forward.

        Used while a Connect or Settings dialog is open: those dialogs ask
        the user to interact with a Chrome/Edge window the app launched,
        and an always-on-top widget would sit over it.
        """
        self._always_on_top_suspensions += 1
        was_visible = self.isVisible()
        self._apply_always_on_top(False)
        if was_visible:
            self.show()

    def restore_always_on_top(self) -> None:
        """Re-apply the configured always-on-top setting after a dialog closes."""
        self._always_on_top_suspensions = max(0, self._always_on_top_suspensions - 1)
        if self._always_on_top_suspensions:
            return
        was_visible = self.isVisible()
        self._apply_always_on_top(self._config.window.always_on_top)
        if was_visible:
            self.show()

    def apply_window_settings(self) -> None:
        """Re-read window-related fields from config and apply."""
        was_visible = self.isVisible()
        self.setWindowOpacity(self._config.window.opacity)
        self._apply_always_on_top(
            self._config.window.always_on_top
            and not self._always_on_top_suspensions
        )
        self._collapsed = self._config.window.collapsed
        self._apply_collapsed_state(save=False)
        if was_visible:
            self.show()  # re-applying flags hides the window

    # ----- drag-to-move -----

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated_requested.emit()
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            self._drag_offset is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._drag_offset = None
        self._do_refit_height()
        # Persist position
        self._config.window.x = self.x()
        self._config.window.y = self.y()
        self._config.window.width = WINDOW_WIDTH
        self._config.window.collapsed = self._collapsed
        if not self._collapsed:
            self._config.window.height = _clamp_height(self.height())
        self._config.save()

    def closeEvent(self, event):  # noqa: N802
        self._do_refit_height()
        self._config.window.width = WINDOW_WIDTH
        self._config.window.collapsed = self._collapsed
        if not self._collapsed:
            self._config.window.height = _clamp_height(self.height())
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
