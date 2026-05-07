from __future__ import annotations

import math
import re
from datetime import datetime, timedelta

from PyQt6.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QIcon,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
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
    browser_account,
    display_name_for_account,
)
from .models import SnapshotStatus, UsageSnapshot

ROW_BAR_HEIGHT = 8
PACE_TICK_OVERHANG = 2
CHIP_NOTCH_HEIGHT = 4
CHIP_NOTCH_HALF_WIDTH = 3.5
PROVIDER_ORDER = ("claude", "codex", "copilot", "openrouter")
COLLAPSED_MIN_HEIGHT = WINDOW_COLLAPSED_HEIGHT


def _clamp_height(value: int) -> int:
    return max(WINDOW_MIN_HEIGHT, min(value, WINDOW_MAX_HEIGHT))


def _provider_family(provider: str) -> str:
    if provider == "claude" or provider.startswith("claude-"):
        return "claude"
    if provider == "codex" or provider.startswith("codex-"):
        return "codex"
    return provider


def _provider_sort_key(provider: str) -> tuple[int, str]:
    family = _provider_family(provider)
    try:
        return (PROVIDER_ORDER.index(family), provider)
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


def _time_elapsed_percent(
    resets_at: datetime | None,
    window: timedelta | None,
) -> float | None:
    if resets_at is None or window is None or window.total_seconds() <= 0:
        return None
    started = resets_at - window
    elapsed = (datetime.now() - started).total_seconds()
    pct = elapsed / window.total_seconds() * 100.0
    return max(0.0, min(100.0, pct))


def _format_duration_short(duration: timedelta, *, total: bool = False) -> str:
    secs = max(0, int(duration.total_seconds()))
    if secs >= 86400:
        days = secs / 86400
        value = max(1, round(days)) if secs else 0
        return f"{value}d"
    mins = (secs + 59) // 60
    if mins < 60:
        return f"{mins}m"
    h, m = divmod(mins, 60)
    if total or not m:
        return f"{h}h"
    return f"{h}h {m:02d}m"


def _format_window_remaining(
    resets_at: datetime | None,
    window: timedelta | None,
) -> str | None:
    if resets_at is None or window is None or window.total_seconds() <= 0:
        return None
    remaining = max(timedelta(0), resets_at - datetime.now())
    return (
        f"{_format_duration_short(remaining)} of "
        f"{_format_duration_short(window, total=True)}"
    )


def _pace_tooltip_line(
    resets_at: datetime | None,
    window: timedelta | None,
) -> str | None:
    pace = _time_elapsed_percent(resets_at, window)
    remaining = _format_window_remaining(resets_at, window)
    if pace is None or remaining is None:
        return None
    return f"Time elapsed: {pace:.0f}% ({remaining})"


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


def _openrouter_compact_text(snapshot: UsageSnapshot) -> tuple[str, str]:
    summary = snapshot.metrics[0] if snapshot.metrics else None
    label = summary.label if summary else ""
    tooltip = label
    balance_match = re.search(r"\bBalance\s+\$([0-9][0-9,]*(?:\.[0-9]{2})?)", label)
    if balance_match:
        return f"OpenRouter ${balance_match.group(1)}", tooltip
    today_match = re.search(
        r"\btoday\s+\$([0-9][0-9,]*(?:\.[0-9]{2})?)",
        label,
        re.IGNORECASE,
    )
    if today_match:
        return f"OpenRouter today ${today_match.group(1)}", tooltip
    return "OpenRouter --", tooltip


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


def _render_refresh_pixmap(color: str, size: int) -> QPixmap:
    """Hand-drawn refresh icon: ~300° arc with an arrowhead at the gap.

    Drawing it ourselves (instead of relying on a system icon) keeps the
    weight, gap, and arrowhead consistent across platforms and lets us
    match the header's color / hover scheme exactly.
    """
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    qcolor = QColor(color)
    cx = cy = size / 2.0
    radius = size * 0.32
    line_w = max(1.4, size / 8.5)

    # Arc travels clockwise (negative span in Qt) from 70° to 130°,
    # leaving a 60° gap at the top. CW direction matches the conventional
    # "refresh" rotation metaphor.
    start_deg = 70.0
    span_deg = -285.0
    end_deg = (start_deg + span_deg) % 360.0  # = 130°

    pen = QPen(qcolor)
    pen.setWidthF(line_w)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    rect = QRectF(cx - radius, cy - radius, 2 * radius, 2 * radius)
    p.drawArc(rect, int(start_deg * 16), int(span_deg * 16))

    # Arrowhead at the end of the arc (130°), pointing in the CW tangent
    # direction so the eye reads "loop continues into the gap".
    end_rad = math.radians(end_deg)
    end_x = cx + radius * math.cos(end_rad)
    end_y = cy - radius * math.sin(end_rad)  # Qt y-axis points down
    # CW tangent in Qt screen coords:
    tx = math.sin(end_rad)
    ty = math.cos(end_rad)
    # Outward radial (perpendicular, away from center):
    rx = math.cos(end_rad)
    ry = -math.sin(end_rad)

    arrow_len = line_w * 2.4
    half_w = line_w * 1.3
    tip = QPointF(end_x + tx * arrow_len, end_y + ty * arrow_len)
    base_outer = QPointF(end_x + rx * half_w, end_y + ry * half_w)
    base_inner = QPointF(end_x - rx * half_w, end_y - ry * half_w)

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(qcolor)
    p.drawPolygon(QPolygonF([tip, base_outer, base_inner]))

    p.end()
    return pm


def _refresh_icon(*, normal: str, active: str, size: int) -> QIcon:
    icon = QIcon()
    icon.addPixmap(_render_refresh_pixmap(normal, size), QIcon.Mode.Normal)
    icon.addPixmap(_render_refresh_pixmap(active, size), QIcon.Mode.Active)
    icon.addPixmap(_render_refresh_pixmap(active, size), QIcon.Mode.Selected)
    return icon


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
    _PACE = QColor(229, 231, 235, 230)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._text = ""
        self._percent: float | None = None
        self._pace_pct: float | None = None
        self._fill_color = self._NEUTRAL_FILL
        font = self.font()
        font.setPixelSize(11)
        font.setBold(True)
        self.setFont(font)
        self.setFixedHeight(18 + CHIP_NOTCH_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def text(self) -> str:
        return self._text

    def set_state(
        self,
        text: str,
        percent: float | None,
        kind: str,
        pace: float | None = None,
    ) -> None:
        """kind ∈ {"ok", "loading", "auth", "error"}."""
        self._text = text
        self._pace_pct = max(0.0, min(100.0, pace)) if pace is not None else None
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
        rect = QRectF(
            0,
            CHIP_NOTCH_HEIGHT,
            self.width(),
            self.height() - CHIP_NOTCH_HEIGHT,
        )
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
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

        # Text
        painter.setPen(self._TEXT)
        painter.drawText(
            rect.toRect(),
            Qt.AlignmentFlag.AlignCenter,
            self._text,
        )

        # Pace notch — downward-pointing triangle sitting on the top edge.
        # Drawn last so it isn't clipped by the rounded body or covered by
        # the fill. Stays in negative space against the dark widget
        # background, so it reads cleanly over any chip fill color.
        if self._pace_pct is not None:
            tip_x = rect.x() + (self._pace_pct / 100.0) * rect.width()
            tip_x = max(
                rect.x() + CHIP_NOTCH_HALF_WIDTH,
                min(rect.right() - CHIP_NOTCH_HALF_WIDTH, tip_x),
            )
            notch = QPolygonF(
                [
                    QPointF(tip_x - CHIP_NOTCH_HALF_WIDTH, 0.0),
                    QPointF(tip_x + CHIP_NOTCH_HALF_WIDTH, 0.0),
                    QPointF(tip_x, float(CHIP_NOTCH_HEIGHT)),
                ]
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._PACE)
            painter.drawPolygon(notch)


class _PaceTickOverlay(QWidget):
    _PACE = QColor(243, 244, 246, 180)
    _PACE_SHADOW = QColor(17, 24, 39, 120)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._pace_pct: float | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_pace(self, pct: float | None) -> None:
        self._pace_pct = max(0.0, min(100.0, pct)) if pct is not None else None
        self.update()

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        if self._pace_pct is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        x = (self._pace_pct / 100.0) * self.width()
        for color, width in ((self._PACE_SHADOW, 4), (self._PACE, 2)):
            pen = QPen(color)
            pen.setWidth(width)
            painter.setPen(pen)
            painter.drawLine(
                int(round(x)),
                0,
                int(round(x)),
                self.height(),
            )


class _PaceProgressBar(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._bar = QProgressBar(self)
        self._bar.setGeometry(0, PACE_TICK_OVERHANG, 0, ROW_BAR_HEIGHT)
        self._tick = _PaceTickOverlay(self)
        self._pace_pct: float | None = None

    def set_pace(self, pct: float | None) -> None:
        self._pace_pct = max(0.0, min(100.0, pct)) if pct is not None else None
        self._tick.set_pace(self._pace_pct)

    def setRange(self, minimum: int, maximum: int) -> None:  # noqa: N802
        self._bar.setRange(minimum, maximum)

    def maximum(self) -> int:
        return self._bar.maximum()

    def setValue(self, value: int) -> None:  # noqa: N802
        self._bar.setValue(value)

    def setTextVisible(self, visible: bool) -> None:  # noqa: N802
        self._bar.setTextVisible(visible)

    def setStyleSheet(self, style_sheet: str) -> None:  # noqa: N802
        self._bar.setStyleSheet(style_sheet)

    def resizeEvent(self, event):  # noqa: N802
        self._bar.setGeometry(
            0,
            PACE_TICK_OVERHANG,
            self.width(),
            ROW_BAR_HEIGHT,
        )
        self._tick.setGeometry(0, 0, self.width(), self.height())
        self._tick.raise_()
        super().resizeEvent(event)


class _MetricRow(QWidget):
    """A single label / bar / pct / reset row."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.label = QLabel()
        self.label.setStyleSheet("color: #d1d5db; font-size: 11px;")
        self.label.setMinimumWidth(70)
        self._resets_at: datetime | None = None
        self._window: timedelta | None = None

        self.bar = _PaceProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(ROW_BAR_HEIGHT + PACE_TICK_OVERHANG * 2)
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
        window: timedelta | None = None,
    ) -> None:
        # Reset to flexible width; group alignment in _set_rows may pin it after.
        self.label.setMinimumWidth(70)
        self.label.setMaximumWidth(16777215)
        split_note = (
            percent is None
            and resets_at is None
            and window is None
            and reset_label is None
            and " · " in label
        )
        if split_note:
            left, right = label.split(" · ", 1)
            self.label.setText(left)
            self.reset.setStyleSheet("color: #d1d5db; font-size: 11px;")
        else:
            self.label.setText(label)
            self.reset.setStyleSheet("color: #9ca3af; font-size: 10px;")
        self.setToolTip(note or "")
        self._resets_at = resets_at
        self._window = window
        self.refresh_pace()
        # Restore determinate range in case this row was previously a skeleton.
        if self.bar.maximum() == 0:
            self.bar.setRange(0, 100)
        rel = reset_label if reset_label is not None else _format_relative(resets_at)
        has_timeline = resets_at is not None or window is not None
        if percent is None:
            self.bar.setValue(0)
            self.pct.setText("")
            self.pct.setVisible(False)
            self.bar.setVisible(has_timeline)
        else:
            self.bar.setValue(int(round(percent)))
            self.pct.setText(f"{percent:.0f}%")
            self.pct.setVisible(True)
            self.bar.setVisible(True)
        color = _color_for_percent(percent)
        self.bar.setStyleSheet(
            f"QProgressBar {{ background:#374151; border:none; border-radius:3px; }}"
            f"QProgressBar::chunk {{ background:{color}; border-radius:3px; }}"
        )
        if split_note:
            self.reset.setText(right)
            self.reset.setVisible(True)
            right_width = self.reset.fontMetrics().horizontalAdvance(right) + 4
            self.reset.setFixedWidth(max(92, min(190, right_width)))
            self.reset.setToolTip("")
        else:
            self.reset.setText(rel)
            self.reset.setVisible(bool(rel))
            self.reset.setFixedWidth(58)
        if reset_label:
            self.reset.setToolTip(note or reset_label)
        elif resets_at:
            self.reset.setToolTip(resets_at.strftime("%Y-%m-%d %H:%M"))
        elif not split_note:
            self.reset.setToolTip("")
        pace_line = _pace_tooltip_line(resets_at, window)
        if pace_line:
            tooltip = note or ""
            self.bar.setToolTip((tooltip + "\n\n" if tooltip else "") + pace_line)
        else:
            self.bar.setToolTip(note or "")

    def refresh_pace(self) -> None:
        self.bar.set_pace(_time_elapsed_percent(self._resets_at, self._window))

    def set_skeleton(self, label: str = "Session") -> None:
        """Indeterminate placeholder while waiting for first data.

        Qt animates a stripe inside the chunk when ``range == (0, 0)``; the
        bar still respects the QSS chunk color, so we get a muted shimmer.
        """
        self.label.setText(label)
        self.setToolTip("")
        self._resets_at = None
        self._window = None
        self.bar.setRange(0, 0)
        self.bar.set_pace(None)
        self.bar.setStyleSheet(
            "QProgressBar { background:#1f2937; border:none; border-radius:3px; }"
            "QProgressBar::chunk { background:#4b5563; border-radius:3px; }"
        )
        self.bar.setVisible(True)
        self.pct.setVisible(True)
        self.reset.setVisible(True)
        self.pct.setText("")
        self.reset.setText("")
        self.reset.setToolTip("")


class _ProviderTile(QFrame):
    """A provider section: header line + N metric rows."""

    sign_in_requested = pyqtSignal(str)  # provider name
    details_requested = pyqtSignal(str)  # provider name (when error label is clicked)
    expanded_changed = pyqtSignal(str, bool)  # provider name, expanded

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

        self.expand_btn = QPushButton("▸")  # right-pointing chevron
        self.expand_btn.setVisible(False)
        self.expand_btn.setFixedSize(16, 16)
        self.expand_btn.setStyleSheet(
            "QPushButton { background:transparent; color:#9ca3af; border:none; "
            "font-size:10px; padding:0; }"
            "QPushButton:hover { color:#f3f4f6; }"
        )
        self.expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.expand_btn.setToolTip("Show top models")
        self.expand_btn.clicked.connect(self._on_expand_clicked)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.addWidget(self.expand_btn)
        header_row.addWidget(self.header)
        header_row.addStretch(1)
        header_row.addWidget(self.action_btn)
        header_row.addWidget(self.status)

        self._rows: list[_MetricRow] = []
        self._expanded = False
        self._latest_snapshot: UsageSnapshot | None = None

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
        self._opacity_anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
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
        self._latest_snapshot = snapshot
        if snapshot is None:
            self.status.setText("loading…")
            self.status.setStyleSheet(
                "color: #6b7280; font-size: 10px; font-style: italic;"
            )
            self.status.setToolTip("")
            self.status.setCursor(Qt.CursorShape.ArrowCursor)
            self.action_btn.setVisible(False)
            self.expand_btn.setVisible(False)
            self._set_skeleton(["Session"])
            return

        if snapshot.status == SnapshotStatus.AUTH_REQUIRED:
            self.status.setText("not signed in")
            self.status.setStyleSheet(
                "color: #f59e0b; font-size: 10px; font-style: normal;"
            )
            self.status.setToolTip(snapshot.error or "")
            self.status.setCursor(Qt.CursorShape.ArrowCursor)
            self.action_btn.setVisible(
                _provider_family(self.provider) in ("claude", "codex")
            )
            self.expand_btn.setVisible(False)
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
            self.expand_btn.setVisible(False)
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
        has_breakdown = any(m.tag for m in snapshot.metrics)
        self.expand_btn.setVisible(has_breakdown)
        self._update_expand_btn_glyph()
        visible = [
            m for m in snapshot.metrics if not m.tag or self._expanded
        ]
        self._set_rows(
            [
                (
                    m.label,
                    m.percent_used,
                    m.resets_at,
                    m.reset_label,
                    m.note,
                    m.window,
                    m.tag,
                )
                for m in visible
            ]
        )

    def set_expanded(self, expanded: bool, *, emit: bool = True) -> None:
        if self._expanded == expanded:
            return
        self._expanded = expanded
        self._update_expand_btn_glyph()
        # Re-render rows from the latest snapshot to add/remove breakdown rows.
        if self._latest_snapshot is not None:
            self.set_snapshot(self._latest_snapshot)
        if emit:
            self.expanded_changed.emit(self.provider, expanded)

    def _on_expand_clicked(self) -> None:
        self.set_expanded(not self._expanded, emit=True)

    def _update_expand_btn_glyph(self) -> None:
        self.expand_btn.setText("▾" if self._expanded else "▸")
        self.expand_btn.setToolTip(
            "Hide top models" if self._expanded else "Show top models"
        )

    def _set_rows(
        self,
        rows: list[
            tuple[
                str,
                float | None,
                datetime | None,
                str | None,
                str | None,
                timedelta | None,
                str | None,
            ]
        ],
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
        grouped: dict[str, list[QLabel]] = {}
        for row, (label, pct, reset, reset_label, note, window, tag) in zip(
            self._rows,
            rows,
        ):
            row.set_metric(label, pct, reset, reset_label, note, window)
            if tag and pct is not None:
                grouped.setdefault(tag, []).append(row.label)
        for labels in grouped.values():
            if len(labels) <= 1:
                continue
            max_w = max(
                lbl.fontMetrics().horizontalAdvance(lbl.text()) for lbl in labels
            )
            for lbl in labels:
                lbl.setFixedWidth(max_w + 4)
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
    tile_expanded_changed = pyqtSignal(str, bool)
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
        title = QLabel(f"AI Gauge {__version__}")
        title.setToolTip(f"ai-gauge {__version__}")
        title.setStyleSheet("color:#9ca3af; font-size:10px; font-weight:600;")

        self.cadence_label = QLabel("")
        self.cadence_label.setStyleSheet("color:#6b7280; font-size:10px;")
        self.cadence_label.setToolTip("")

        self.refresh_btn = self._mini_button("", "Refresh now")
        self.refresh_btn.setIcon(
            _refresh_icon(normal="#9ca3af", active="#f3f4f6", size=16)
        )
        self.refresh_btn.setIconSize(QSize(16, 16))
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
        collapsed_title = QLabel(f"AI Gauge {__version__}")
        collapsed_title.setStyleSheet("color:#9ca3af; font-size:10px; font-weight:600;")
        collapsed_header.addWidget(collapsed_title)
        self._collapsed_cadence_label = QLabel("")
        self._collapsed_cadence_label.setStyleSheet("color:#6b7280; font-size:10px;")
        collapsed_header.addWidget(self._collapsed_cadence_label)
        collapsed_header.addStretch(1)
        self._collapsed_age_label = QLabel("")
        self._collapsed_age_label.setStyleSheet("color:#6b7280; font-size:10px;")
        collapsed_header.addWidget(self._collapsed_age_label)
        collapsed_header.addWidget(self._expand_btn)

        self._collapsed_summary_layout = QVBoxLayout()
        self._collapsed_summary_layout.setContentsMargins(0, 0, 0, 0)
        self._collapsed_summary_layout.setSpacing(3)
        self._collapsed_summary_layout.addWidget(self._collapsed_label)

        collapsed_outer.addLayout(collapsed_header)
        collapsed_outer.addLayout(self._collapsed_summary_layout)

        self._tile_container = QWidget(self)
        self._tile_container.setStyleSheet("background:#111827;")
        self._tile_container.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self._tile_layout = QVBoxLayout(self._tile_container)
        self._tile_layout.setContentsMargins(2, 0, 2, 4)
        self._tile_layout.setSpacing(2)
        self._tile_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._tile_scroll = QScrollArea(self)
        self._tile_scroll.setWidgetResizable(True)
        self._tile_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._tile_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._tile_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._tile_scroll.setStyleSheet(
            "QScrollArea { background:#111827; border:none; }"
            "QScrollArea > QWidget > QWidget { background:#111827; }"
            "QScrollBar:vertical { background:#111827; width:6px; margin:0; }"
            "QScrollBar::handle:vertical { background:#4b5563; border-radius:3px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }"
        )
        self._tile_scroll.viewport().setStyleSheet("background:#111827;")
        self._tile_scroll.setWidget(self._tile_container)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._collapsed_widget)
        outer.addWidget(self._header_widget)
        outer.addWidget(self._tile_scroll)
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
            tile.expanded_changed.connect(self.tile_expanded_changed.emit)
            if provider in (self._config.expanded_tiles or []):
                tile.set_expanded(True, emit=False)
            self._tiles[provider] = tile
            self._insert_tile_in_provider_order(provider, tile)
            self._refit_height()
        else:
            self._tiles[provider].header.setText(display_name)
        return self._tiles[provider]

    def _tile_sort_key(self, provider: str) -> tuple[int, int, str]:
        account_ids = [
            account.id
            for account in getattr(self._config, "browser_accounts", [])
            if account.kind in ("claude", "codex")
        ]
        if provider in account_ids:
            account = next(
                account
                for account in self._config.browser_accounts
                if account.id == provider
            )
            family_rank = PROVIDER_ORDER.index(account.kind)
            return (family_rank, account_ids.index(provider), provider)
        family_rank, fallback = _provider_sort_key(provider)
        return (family_rank, 10_000, fallback)

    def _insert_tile_in_provider_order(
        self, provider: str, tile: _ProviderTile
    ) -> None:
        provider_rank = self._tile_sort_key(provider)
        index = self._tile_layout.count()
        for i in range(self._tile_layout.count()):
            existing = self._tile_layout.itemAt(i).widget()
            if not isinstance(existing, _ProviderTile):
                continue
            if self._tile_sort_key(existing.provider) > provider_rank:
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
        self._tile_scroll.updateGeometry()
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
            target_height = max(
                COLLAPSED_MIN_HEIGHT,
                min(WINDOW_MAX_HEIGHT, self._collapsed_widget.sizeHint().height()),
            )
            if self.height() != target_height or self.width() != WINDOW_WIDTH:
                self.resize(WINDOW_WIDTH, target_height)
            return
        self._tile_layout.invalidate()
        self._tile_container.updateGeometry()
        self._tile_scroll.updateGeometry()
        self.updateGeometry()
        self.layout().invalidate()
        header_height = self._header_widget.sizeHint().height()
        tile_height = self._tile_container.sizeHint().height()
        max_tile_height = max(40, WINDOW_MAX_HEIGHT - header_height)
        self._tile_scroll.setFixedHeight(min(tile_height, max_tile_height))
        target_height = _clamp_height(header_height + self._tile_scroll.height())
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
        for tile in self._tiles.values():
            for row in tile._rows:
                row.refresh_pace()
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
        display = display_name_for_account(self._config, provider)
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
        providers = sorted(self._tiles, key=self._tile_sort_key)
        available_width = WINDOW_WIDTH - 16
        row_widget: QWidget | None = None
        row_layout: QHBoxLayout | None = None
        row_width = 0
        spacing = 5
        for provider in providers:
            chip = self._summary_chip(provider)
            chip_width = chip.width()
            needed = chip_width if row_layout is None else chip_width + spacing
            if row_layout is None or row_width + needed > available_width:
                if row_layout is not None:
                    row_layout.addStretch(1)
                row_widget = QWidget(self._collapsed_widget)
                row_layout = QHBoxLayout(row_widget)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.setSpacing(spacing)
                self._collapsed_summary_layout.addWidget(row_widget)
                row_width = 0
                needed = chip_width
            row_layout.addWidget(chip)
            row_width += needed
        if row_layout is not None:
            row_layout.addStretch(1)
        if self._collapsed:
            self._refit_height()

    def _clear_collapsed_summary(self) -> None:
        while self._collapsed_summary_layout.count():
            item = self._collapsed_summary_layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self._collapsed_label:
                widget.deleteLater()
        self._collapsed_label.show()

    def _summary_chip(self, provider: str) -> _SummaryChip:
        account = browser_account(self._config, provider)
        display = (
            account.name.strip()
            if account is not None
            and provider not in ("claude", "codex")
            and account.name
            and account.name.strip()
            else display_name_for_account(self._config, provider)
        )
        snapshot = self._snapshots.get(provider)
        percent: float | None = None
        pace: float | None = None
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
        elif provider == "openrouter":
            text, tooltip = _openrouter_compact_text(snapshot)
            kind = "ok"
        else:
            metric = next(
                (m for m in snapshot.metrics if m.label.lower() == "session"),
                snapshot.metrics[0] if snapshot.metrics else None,
            )
            percent = metric.percent_used if metric else None
            pace = _time_elapsed_percent(
                metric.resets_at if metric else None,
                metric.window if metric else None,
            )
            text = f"{display} {_format_summary_percent(percent)}"
            tooltip = metric.note if metric and metric.note else ""
            if metric:
                pace_line = _pace_tooltip_line(metric.resets_at, metric.window)
                if pace_line:
                    tooltip = (tooltip + "\n\n" if tooltip else "") + pace_line
            kind = "ok"

        chip = _SummaryChip()
        chip.set_state(text, percent, kind, pace)
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
        self._tile_scroll.setVisible(not self._collapsed)
        self._tile_container.setVisible(not self._collapsed)
        self._refresh_collapsed_summary()
        if self._collapsed:
            self.setFixedWidth(WINDOW_WIDTH)
            self.setMinimumHeight(COLLAPSED_MIN_HEIGHT)
            self.setMaximumHeight(WINDOW_MAX_HEIGHT)
            self._do_refit_height()
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
            self._config.window.always_on_top and not self._always_on_top_suspensions
        )
        self._collapsed = self._config.window.collapsed
        self._apply_collapsed_state(save=False)
        if was_visible:
            self.show()  # re-applying flags hides the window

    def show_as_popover(self, anchor_global_x: int, anchor_global_y: int) -> None:
        """Show the widget below ``(anchor_global_x, anchor_global_y)``.

        Used as the macOS menu-bar drop-down. The widget gets ``Qt.Popup``
        flags so it dismisses when the user clicks outside it. The widget's
        own X button still hides it; settings dialogs still spawn correctly
        because they're separate top-level windows.
        """
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Popup)
        self.setWindowOpacity(self._config.window.opacity)
        self._refit_height()
        # Anchor: top of widget aligned just below the menu bar at the icon.
        # If the anchor would push the widget off the right edge of the
        # screen, shift it left so it stays fully on screen.
        screen = self.screen() or self.window().screen()
        target_x = anchor_global_x - self.width() // 2
        if screen is not None:
            geo = screen.availableGeometry()
            target_x = max(
                geo.left() + 4, min(target_x, geo.right() - self.width() - 4)
            )
        self.move(target_x, anchor_global_y + 4)
        self.show()
        self.raise_()
        self.activateWindow()

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
