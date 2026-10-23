from __future__ import annotations

import ctypes
import html
import math
import re
import sys
from datetime import datetime, timedelta

from PyQt6.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QEvent,
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
    QPalette,
    QPen,
    QPixmap,
    QPolygonF,
    QRegion,
)
from PyQt6.QtWidgets import (
    QApplication,
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
    DEFAULT_PROVIDER_ORDER,
    ColorThresholds,
    Config,
    SnapCorner,
    WINDOW_COLLAPSED_MIN_HEIGHT,
    WINDOW_COLLAPSED_MIN_WIDTH,
    WINDOW_MAX_HEIGHT,
    WINDOW_MAX_WIDTH,
    WINDOW_MIN_HEIGHT,
    WINDOW_MIN_WIDTH,
    WINDOW_WIDTH,
    browser_account,
    display_name_for_account,
    provider_order,
)
from .gauge import color_for_percent, thresholds_for_provider
from .icons import app_icon
from .models import SnapshotStatus, UsageSnapshot
from .providers.registry import (
    account_family,
    account_kinds,
    api_key_kinds,
    browser_session_kinds,
    provider_spec,
)
from .ratio import (
    MIN_SAMPLES,
    MIN_SESSION_DELTA,
    MIN_WEEKLY_DELTA,
    RatioEstimate,
)

ROW_BAR_HEIGHT = 8
# Narrowest a percentage gauge may be squeezed before the panel refuses to
# shrink further. Short enough to let a dense panel be dragged genuinely
# narrow, long enough for the fill and the pace tick to stay readable.
MIN_GAUGE_WIDTH = 80
# Floor for the metric-label column. Every tile pads its own labels to their
# widest, so this floor is what keeps the bars starting at the same x across
# providers whose labels differ.
MIN_LABEL_WIDTH = 56
PACE_TICK_OVERHANG = 2
CHIP_NOTCH_HEIGHT = 4
CHIP_NOTCH_HALF_WIDTH = 3.5
# Default display order. The live order is the user's saved one, read through
# provider_order(config); this alias is kept for callers that want the default.
PROVIDER_ORDER = DEFAULT_PROVIDER_ORDER
COLLAPSED_MIN_HEIGHT = WINDOW_COLLAPSED_MIN_HEIGHT
EXPANDED_MIN_WIDTH = WINDOW_MIN_WIDTH
COLLAPSED_MIN_WIDTH = WINDOW_COLLAPSED_MIN_WIDTH
# Window chrome. PANEL_BG doubles as the widget's palette Window brush so any
# pixel Qt erases outside paintEvent's rounded rect matches the panel instead
# of the system theme's colour.
PANEL_BG = "#111827"
PANEL_BORDER = "#1f2937"
PANEL_CORNER_RADIUS = 8
# Footer row: tall enough for the 10px status text and the 14px resize grip.
FOOTER_HEIGHT = 16
# Pointer movement (device-independent pixels) still treated as a click on the
# footer status rather than the start of a window drag.
STATUS_CLICK_SLOP = 4
# Distances are Qt device-independent pixels. Qt maps the pointer, window, and
# available screen geometry into this same coordinate system, so these retain
# the same apparent size at 100%, 150%, and 200% display scaling.
CORNER_SNAP_DISTANCE = 32
CORNER_SNAP_RELEASE_DISTANCE = 40
CORNER_SNAP_INSET = 8


def _clamp_height(value: int) -> int:
    return max(WINDOW_MIN_HEIGHT, min(value, WINDOW_MAX_HEIGHT))


def _provider_family(provider: str) -> str:
    """The account kind of a tile id, or the id itself for single providers."""
    return account_family(provider) or provider


def _is_api_key_tile(provider: str) -> bool:
    return _provider_family(provider) in api_key_kinds()


def _claude_weekly_limit_hit(snapshot: UsageSnapshot | None) -> bool:
    """Return whether Claude's primary weekly allowance is fully used.

    Fable has an independent weekly allowance, so only the primary ``Weekly``
    metric should put the whole Claude tile into the limit-hit state.
    """
    if (
        snapshot is None
        or snapshot.status != SnapshotStatus.OK
        or _provider_family(snapshot.provider) != "claude"
    ):
        return False
    return any(
        metric.label.strip().lower() == "weekly"
        and metric.percent_used is not None
        and metric.percent_used >= 100.0
        for metric in snapshot.metrics
    )


def _claude_limit_hit_tooltip(snapshot: UsageSnapshot) -> str:
    lines = ["Claude's weekly plan limit is fully used."]
    session = next(
        (
            metric
            for metric in snapshot.metrics
            if metric.label.strip().lower() == "session"
        ),
        None,
    )
    weekly = next(
        (
            metric
            for metric in snapshot.metrics
            if metric.label.strip().lower() == "weekly"
        ),
        None,
    )
    if session is not None and session.note:
        lines.append(session.note.rstrip(".") + ".")
    if weekly is not None and weekly.resets_at is not None:
        reset = weekly.resets_at.strftime("%Y-%m-%d %H:%M")
        relative = _format_relative(weekly.resets_at)
        lines.append(f"Weekly reset: {reset}" + (f" ({relative})." if relative else "."))
    return "\n".join(lines)


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


def _chip_fill_for_percent(
    p: float | None, colors: ColorThresholds | None = None
) -> str:
    if p is None:
        return "#374151"
    color = color_for_percent(p, colors)
    return QColor(color).darker(135).name()


def _format_summary_percent(p: float | None) -> str:
    return "--" if p is None else f"{p:.0f}%"


def _format_ratio_inline(estimate: "RatioEstimate | None") -> str | None:
    """Compact session->weekly headline for the tile header right side.

    Returns ``~N/wk`` once the estimate is confident, a faint ``burn ~?``
    placeholder while still calibrating, or ``None`` to hide the element
    entirely (no session/weekly pair, e.g. Copilot/OpenRouter, or never
    tracked).
    """
    if estimate is None:
        return None
    if estimate.confident and estimate.sessions_per_week is not None:
        return f"~{estimate.sessions_per_week:.1f}/wk"
    return "burn ~?"


def _calibration_progress_line(estimate: "RatioEstimate") -> str:
    return (
        "This week calibrating: session "
        f"{min(estimate.session_delta, MIN_SESSION_DELTA):.0f}/{MIN_SESSION_DELTA:.0f} pts · "
        f"weekly {min(estimate.coverage_pct, MIN_WEEKLY_DELTA):.1f}/{MIN_WEEKLY_DELTA:.0f} pts · "
        f"readings {min(estimate.sample_count, MIN_SAMPLES)}/{MIN_SAMPLES}"
    )


def _format_ratio_tooltip(
    estimate: "RatioEstimate | None",
    recent: list[float],
    live: "RatioEstimate | None" = None,
) -> str:
    if estimate is None:
        return ""
    lines: list[str] = []
    if estimate.confident and estimate.sessions_per_week is not None:
        carry_over = estimate.source == "history"
        when = "Last week" if carry_over else "This week so far"
        lines.append(f"{when}: ~{estimate.sessions_per_week:.1f} full sessions/week")
        if estimate.weekly_pct_per_session is not None:
            lines.append(f"1 session ≈ {estimate.weekly_pct_per_session:.1f}% of weekly")
        # Carry-over: surface that the current week is still being measured.
        if carry_over and live is not None and not live.confident:
            lines.append(_calibration_progress_line(live))
        elif not carry_over and estimate.coverage_pct:
            lines.append(
                f"Coverage {estimate.coverage_pct:.0f}% of weekly · "
                f"{estimate.sample_count} readings"
            )
    else:
        lines.append("Session → weekly burn rate: calibrating")
        lines.append(_calibration_progress_line(estimate))
        lines.append("Counts usage seen while running, not the absolute %.")
    if recent:
        trend = " → ".join(f"{v:.1f}" for v in recent)
        lines.append(f"Recent weeks: {trend} sessions/wk")
    lines.append("Click for history.")
    return "\n".join(lines)


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
    if "agy cli not found" in e:
        # First, so a path quoted in the message cannot match a tag below.
        return "error · agy not found"
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
        colors: ColorThresholds | None = None,
    ) -> None:
        """kind ∈ {"ok", "loading", "auth", "error"}."""
        self._text = text
        self._pace_pct = max(0.0, min(100.0, pace)) if pace is not None else None
        if kind == "ok":
            self._percent = percent
            self._fill_color = QColor(_chip_fill_for_percent(percent, colors))
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

    def __init__(
        self,
        parent: QWidget | None = None,
        colors: ColorThresholds | None = None,
    ):
        super().__init__(parent)
        self._colors = colors or ColorThresholds()
        self.label = QLabel()
        self.label.setStyleSheet("color: #d1d5db; font-size: 11px;")
        self.label.setMinimumWidth(MIN_LABEL_WIDTH)
        self._resets_at: datetime | None = None
        self._window: timedelta | None = None

        self.bar = _PaceProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(ROW_BAR_HEIGHT + PACE_TICK_OVERHANG * 2)
        self.bar.setMinimumWidth(MIN_GAUGE_WIDTH)
        self.bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.pct = QLabel("--")
        self.pct.setStyleSheet("color: #f3f4f6; font-size: 11px; font-weight: 600;")
        self.pct.setMinimumWidth(0)
        self.pct.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self.reset = QLabel("")
        self.reset.setStyleSheet("color: #9ca3af; font-size: 10px;")
        self.reset.setMinimumWidth(0)
        self.reset.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 1, 0, 1)
        self._layout.setSpacing(1)
        self._main_layout = QHBoxLayout()
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(6)
        self._main_layout.addWidget(self.label)
        self._main_layout.addWidget(self.bar, 1)
        self._main_layout.addWidget(self.pct)
        self._main_layout.addWidget(self.reset)
        self._layout.addLayout(self._main_layout)
        self._split_note = False
        self._available_width = WINDOW_MAX_WIDTH
        self._reset_natural_width = 0
        self._reset_stacked = False

    def set_available_width(self, available_width: int) -> bool:
        self._available_width = max(0, available_width)
        return self._sync_responsive_layout()

    def _inline_minimum_width(self) -> int:
        widgets = [self.label, self.bar, self.pct, self.reset]
        visible = [widget for widget in widgets if not widget.isHidden()]
        widths = []
        for widget in visible:
            if widget is self.reset and self._split_note:
                widths.append(self._reset_natural_width)
            else:
                widths.append(widget.minimumWidth())
        spacing = max(0, len(visible) - 1) * self._main_layout.spacing()
        return sum(widths) + spacing

    def _sync_responsive_layout(self) -> bool:
        should_stack_reset = (
            self._split_note
            and (
                self.label.minimumWidth()
                + self._reset_natural_width
                + self._main_layout.spacing()
            )
            > self._available_width
        )
        changed = False

        if should_stack_reset != self._reset_stacked:
            self._main_layout.removeWidget(self.reset)
            self._layout.removeWidget(self.reset)
            if should_stack_reset:
                self.reset.setAlignment(
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                )
                self._layout.addWidget(self.reset)
            else:
                self.reset.setAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                self._main_layout.addWidget(self.reset)
            self._reset_stacked = should_stack_reset
            changed = True

        if self._split_note and self._reset_stacked:
            wrapped_width = max(1, self._available_width)
            if (
                not self.reset.wordWrap()
                or self.reset.maximumWidth() != wrapped_width
            ):
                self.reset.setWordWrap(True)
                self.reset.setMinimumWidth(0)
                self.reset.setMaximumWidth(wrapped_width)
                changed = True
        elif self._split_note:
            if (
                self.reset.wordWrap()
                or self.reset.width() != self._reset_natural_width
            ):
                self.reset.setWordWrap(False)
                self.reset.setFixedWidth(self._reset_natural_width)
                changed = True

        if changed:
            self._layout.invalidate()
            self._layout.activate()
            self.updateGeometry()
        return changed

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
        self.label.setMinimumWidth(MIN_LABEL_WIDTH)
        self.label.setMaximumWidth(16777215)
        split_note = (
            percent is None
            and resets_at is None
            and window is None
            and reset_label is None
            and " · " in label
        )
        self._split_note = split_note
        self.reset.setWordWrap(False)
        if split_note:
            left, right = label.split(" · ", 1)
            self.label.setText(left)
            self.reset.setStyleSheet("color: #d1d5db; font-size: 11px;")
        else:
            self.label.setText(label)
            self.reset.setStyleSheet("color: #9ca3af; font-size: 10px;")
        label_width = self.label.fontMetrics().horizontalAdvance(self.label.text()) + 4
        self.label.setMinimumWidth(max(MIN_LABEL_WIDTH, label_width))
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
            # QProgressBar treats an above-maximum value as out of range and
            # may draw no chunk at all. Keep the label truthful (e.g. 110%)
            # while clamping only the visual fill to a complete bar.
            self.bar.setValue(int(round(max(0.0, min(percent, 100.0)))))
            self.pct.setText(f"{percent:.0f}%")
            self.pct.setFixedWidth(
                self.pct.fontMetrics().horizontalAdvance(self.pct.text()) + 2
            )
            self.pct.setVisible(True)
            self.bar.setVisible(True)
        color = color_for_percent(percent, self._colors)
        self.bar.setStyleSheet(
            f"QProgressBar {{ background:#374151; border:none; border-radius:3px; }}"
            f"QProgressBar::chunk {{ background:{color}; border-radius:3px; }}"
        )
        if split_note:
            self.reset.setText(right)
            self.reset.setVisible(True)
            right_width = self.reset.fontMetrics().horizontalAdvance(right) + 4
            self._reset_natural_width = max(92, right_width)
            self.reset.setFixedWidth(self._reset_natural_width)
            self.reset.setToolTip("")
        else:
            self.reset.setText(rel)
            self.reset.setVisible(bool(rel))
            self._reset_natural_width = (
                self.reset.fontMetrics().horizontalAdvance(rel) + 2 if rel else 0
            )
            self.reset.setFixedWidth(self._reset_natural_width)
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
        self._sync_responsive_layout()

    def refresh_pace(self) -> None:
        self.bar.set_pace(_time_elapsed_percent(self._resets_at, self._window))

    def set_skeleton(self, label: str = "Session") -> None:
        """Indeterminate placeholder while waiting for first data.

        Qt animates a stripe inside the chunk when ``range == (0, 0)``; the
        bar still respects the QSS chunk color, so we get a muted shimmer.
        """
        self._split_note = False
        self._reset_natural_width = 0
        self.reset.setWordWrap(False)
        self.label.setText(label)
        label_width = self.label.fontMetrics().horizontalAdvance(label) + 4
        self.label.setMinimumWidth(max(MIN_LABEL_WIDTH, label_width))
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
        self.pct.setFixedWidth(0)
        self.reset.setText("")
        self.reset.setFixedWidth(0)
        self.reset.setToolTip("")
        self._sync_responsive_layout()




def _compact_metric_code(label: str) -> str:
    key = label.strip().lower()
    return {
        "session": "S",
        "weekly": "W",
        "fable": "F",
        "rolling": "R",
        "monthly": "M",
    }.get(key, (label.strip()[:1] or "?").upper())


class _CompactMetric(QWidget):
    """Tiny inline metric for a collapsed provider tile."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.code = QLabel("")
        self.code.setStyleSheet("color:#d1d5db; font-size:10px; font-weight:700;")
        self.code.setFixedWidth(10)
        self.code.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedSize(30, 6)
        self.bar.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._bar_expanding = False

        self.pct = QLabel("--")
        self.pct.setStyleSheet("color:#f3f4f6; font-size:10px; font-weight:600;")
        self.pct.setFixedWidth(28)
        self.pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.reset = QLabel("")
        self.reset.setStyleSheet("color:#9ca3af; font-size:10px;")
        self.reset.setFixedWidth(38)
        self.reset.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addWidget(self.code)
        layout.addWidget(self.bar)
        layout.addWidget(self.pct)
        layout.addWidget(self.reset)

    def set_bar_expanding(self, expanding: bool) -> None:
        if self._bar_expanding == expanding:
            return
        self._bar_expanding = expanding
        if expanding:
            self.bar.setMinimumWidth(30)
            self.bar.setMaximumWidth(16777215)
            self.bar.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Fixed,
            )
        else:
            self.bar.setFixedWidth(30)
            self.bar.setSizePolicy(
                QSizePolicy.Policy.Fixed,
                QSizePolicy.Policy.Fixed,
            )
        self.updateGeometry()

    def set_metric(self, metric, *, show_reset: bool = True) -> None:
        percent = metric.percent_used
        reset = metric.reset_label if metric.reset_label is not None else _format_relative(metric.resets_at)
        self.code.setText(_compact_metric_code(metric.label))
        self.pct.setText(_format_summary_percent(percent))
        self.reset.setText(reset if show_reset else "")
        self.reset.setVisible(show_reset and bool(reset))
        self.bar.setValue(0 if percent is None else int(round(percent)))
        color = color_for_percent(percent)
        self.bar.setStyleSheet(
            f"QProgressBar {{ background:#374151; border:none; border-radius:3px; }}"
            f"QProgressBar::chunk {{ background:{color}; border-radius:3px; }}"
        )
        tooltip = f"{metric.label}: {_format_summary_percent(percent)}"
        if reset:
            tooltip += f" · resets {reset}"
        if metric.note:
            tooltip += f"\n{metric.note}"
        self.setToolTip(tooltip)


class _ProviderTile(QFrame):
    """A provider section: header line + N metric rows."""

    sign_in_requested = pyqtSignal(str)  # provider name
    details_requested = pyqtSignal(str)  # provider name (when error label is clicked)
    ratio_history_requested = pyqtSignal(str)  # provider name (ratio label clicked)
    expanded_changed = pyqtSignal(str, bool)  # provider name, expanded

    def __init__(
        self,
        provider: str,
        display_name: str,
        colors: ColorThresholds | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.provider = provider
        self._colors = colors or ColorThresholds()
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

        # Session->weekly burn rate, shown on the right of the header when OK.
        # Sits in the space the (hidden) Sign in button / (empty) status label
        # leave free on an OK tile, so it never fights them for room.
        self.ratio_label = QLabel("")
        self.ratio_label.setVisible(False)
        self.ratio_label.setTextFormat(Qt.TextFormat.RichText)
        self.ratio_label.setStyleSheet("color: #9ca3af; font-size: 10px;")
        self.ratio_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.ratio_label.linkActivated.connect(
            lambda _href: self.ratio_history_requested.emit(self.provider)
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

        self._header_row = QHBoxLayout()
        self._header_row.setContentsMargins(0, 0, 0, 0)
        self._header_row.addWidget(self.expand_btn)
        self._header_row.addWidget(self.header)

        self._compact_metrics: list[_CompactMetric] = []
        self._compact_summary = QWidget(self)
        self._compact_summary.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._compact_summary.setVisible(False)
        self._compact_layout = QHBoxLayout(self._compact_summary)
        self._compact_layout.setContentsMargins(4, 0, 0, 0)
        self._compact_layout.setSpacing(6)
        self._compact_layout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._header_row.addStretch(1)
        self._header_row.addWidget(self._compact_summary)
        self._header_row.addWidget(self.action_btn)
        self._header_row.addWidget(self.ratio_label)
        self._header_row.addWidget(self.status)

        self._rows: list[_MetricRow] = []
        self._expanded = self._supports_compact_collapse()
        self._latest_snapshot: UsageSnapshot | None = None
        self._ratio_estimate: RatioEstimate | None = None
        self._ratio_recent: list[float] = []
        self._ratio_live: RatioEstimate | None = None

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(6, 4, 6, 4)
        self._layout.setSpacing(2)
        self._layout.addLayout(self._header_row)
        self._available_width = WINDOW_MAX_WIDTH
        self._compact_below_header = False

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

    def _inline_compact_minimum_width(self) -> int:
        widgets = [
            self.expand_btn,
            self.header,
            self._compact_summary,
            self.action_btn,
            self.ratio_label,
            self.status,
        ]
        visible = [widget for widget in widgets if not widget.isHidden()]
        margins = self._layout.contentsMargins()
        spacing = max(0, len(visible) - 1) * self._header_row.spacing()
        widget_width = 0
        for widget in visible:
            if widget is self._compact_summary:
                compact_margins = self._compact_layout.contentsMargins()
                compact_spacing = max(
                    0,
                    len(self._compact_metrics) - 1,
                ) * self._compact_layout.spacing()
                widget_width += (
                    compact_margins.left()
                    + compact_margins.right()
                    + compact_spacing
                    + sum(
                        item.minimumSizeHint().width()
                        for item in self._compact_metrics
                    )
                )
            else:
                widget_width += widget.sizeHint().width()
        return margins.left() + margins.right() + spacing + widget_width

    def _row_available_width(self) -> int:
        margins = self._layout.contentsMargins()
        return max(
            0,
            self._available_width - margins.left() - margins.right(),
        )

    def minimum_inline_width(self) -> int:
        """Width that keeps each of this provider's rows on one line.

        Both row shapes count: a percentage gauge, and the label/value pair
        OpenRouter's summary uses. A split-note row stacks its value under its
        label once it no longer fits, and because the layout is only re-flowed
        when a width drag ends, leaving that row out of the floor let the drag
        pass the stacking point and spring into it on release.
        """
        row_width = max(
            (
                row._inline_minimum_width()
                for row in self._rows
                if (not row.bar.isHidden() and not row.pct.isHidden())
                or row._split_note
            ),
            default=0,
        )
        if row_width == 0:
            return 0
        margins = self._layout.contentsMargins()
        return row_width + margins.left() + margins.right()

    def set_available_width(self, available_width: int) -> bool:
        self._available_width = max(0, available_width)
        should_wrap = (
            not self._compact_summary.isHidden()
            and self._inline_compact_minimum_width() > self._available_width
        )
        layout_changed = self._compact_below_header != should_wrap
        if layout_changed:
            self._header_row.removeWidget(self._compact_summary)
            self._layout.removeWidget(self._compact_summary)
            if should_wrap:
                self._compact_summary.setSizePolicy(
                    QSizePolicy.Policy.Expanding,
                    QSizePolicy.Policy.Fixed,
                )
                self._compact_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
                self._layout.insertWidget(1, self._compact_summary)
            else:
                self._compact_summary.setSizePolicy(
                    QSizePolicy.Policy.Fixed,
                    QSizePolicy.Policy.Fixed,
                )
                self._compact_layout.setAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                self._header_row.insertWidget(3, self._compact_summary)
            self._compact_below_header = should_wrap
        for item in self._compact_metrics:
            item.set_bar_expanding(self._compact_below_header)
        rows_changed = False
        row_width = self._row_available_width()
        for row in self._rows:
            rows_changed = row.set_available_width(row_width) or rows_changed
        if layout_changed or rows_changed:
            self._layout.invalidate()
            self._layout.activate()
            self.updateGeometry()
        return layout_changed or rows_changed

    def set_colors(self, colors: ColorThresholds) -> None:
        if self._colors == colors:
            return
        self._colors = colors
        for row in self._rows:
            row._colors = colors
        if self._latest_snapshot is not None:
            self.set_snapshot(self._latest_snapshot)

    def _supports_compact_collapse(self) -> bool:
        spec = provider_spec(_provider_family(self.provider))
        return spec is not None and spec.compact_collapse

    def _compact_metrics_from(self, snapshot: UsageSnapshot) -> list:
        metrics = [
            metric
            for metric in snapshot.metrics
            if metric.tag is None and metric.percent_used is not None
        ]
        if _provider_family(self.provider) == "opencode_go":
            return [
                metric
                for metric in metrics
                if metric.label.strip().lower() in ('rolling', 'monthly')
            ]
        return metrics

    def _set_compact_metrics(self, metrics: list, *, show_reset: bool = True) -> None:
        while len(self._compact_metrics) < len(metrics):
            item = _CompactMetric(self._compact_summary)
            self._compact_metrics.append(item)
            self._compact_layout.addWidget(item)
        while len(self._compact_metrics) > len(metrics):
            item = self._compact_metrics.pop()
            self._compact_layout.removeWidget(item)
            item.hide()
            item.setParent(None)
            item.deleteLater()
        for item, metric in zip(self._compact_metrics, metrics):
            item.set_metric(metric, show_reset=show_reset)
            item.show()
        self._compact_summary.setVisible(bool(metrics))
        self.set_available_width(self._available_width)

    def _hide_compact_metrics(self) -> None:
        self._compact_summary.setVisible(False)

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
            self.ratio_label.setVisible(False)
            self._hide_compact_metrics()
            self._set_skeleton(["Session"])
            return

        if snapshot.status == SnapshotStatus.AUTH_REQUIRED:
            is_api_key = _is_api_key_tile(self.provider)
            self.status.setText("API key needed" if is_api_key else "not signed in")
            self.status.setStyleSheet(
                "color: #f59e0b; font-size: 10px; font-style: normal;"
            )
            self.status.setToolTip(snapshot.error or "")
            self.status.setCursor(Qt.CursorShape.ArrowCursor)
            self.action_btn.setVisible(
                _provider_family(self.provider) in account_kinds()
            )
            self.action_btn.setText("Add key" if is_api_key else "Sign in")
            self.expand_btn.setVisible(False)
            self.ratio_label.setVisible(False)
            self._hide_compact_metrics()
            self._set_rows([])
            return

        if snapshot.status == SnapshotStatus.ERROR:
            label = (
                "error · stale"
                if snapshot.metrics
                else _short_error_reason(snapshot.error)
            )
            self.status.setText(
                f'<a href="details" style="color:#ef4444; text-decoration:none;">{label}</a>'
            )
            self.status.setStyleSheet(
                "color: #ef4444; font-size: 10px; font-style: normal;"
            )
            tooltip = (snapshot.error or "unknown error") + "\n\nClick for details."
            if snapshot.metrics:
                tooltip += "\nLast successful values are still shown below."
            self.status.setToolTip(tooltip)
            self.status.setCursor(Qt.CursorShape.PointingHandCursor)
            self.action_btn.setVisible(False)
            self.ratio_label.setVisible(False)
            has_breakdown = any(m.tag for m in snapshot.metrics)
            compact_metrics = (
                self._compact_metrics_from(snapshot)
                if self._supports_compact_collapse()
                else []
            )
            self.expand_btn.setVisible(has_breakdown or bool(compact_metrics))
            self._update_expand_btn_glyph()
            if compact_metrics and not self._expanded:
                self._set_rows([])
                self._set_compact_metrics(compact_metrics)
                return
            self._hide_compact_metrics()
            visible = [
                m
                for m in snapshot.metrics
                if (not m.tag or self._expanded) and not m.in_header
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
            return

        # OK. A fully used Claude weekly allowance is still a successful
        # snapshot, but it is an important provider state rather than merely a
        # red progress bar. Surface it in the header until the reset arrives.
        limit_hit = _claude_weekly_limit_hit(snapshot)
        # A metric flagged in_header (the Claude API balance left) is the
        # header's right-side text instead of a row, unless the limit-hit
        # state needs the spot.
        header_metric = next(
            (m for m in snapshot.metrics if m.in_header), None
        )
        if limit_hit:
            self.status.setText("limit hit")
        elif header_metric is not None:
            self.status.setText(html.escape(header_metric.label))
        else:
            self.status.setText("")
        self.status.setStyleSheet(
            (
                "color: #ef4444; font-size: 10px; font-style: normal; "
                "font-weight: 700;"
            )
            if limit_hit
            else "color: #9ca3af; font-size: 10px; font-style: normal;"
        )
        if limit_hit:
            self.status.setToolTip(_claude_limit_hit_tooltip(snapshot))
        elif header_metric is not None:
            self.status.setToolTip(header_metric.note or "")
        else:
            self.status.setToolTip("")
        self.status.setCursor(Qt.CursorShape.ArrowCursor)
        self.action_btn.setVisible(False)
        has_breakdown = any(m.tag for m in snapshot.metrics)
        compact_metrics = (
            self._compact_metrics_from(snapshot)
            if self._supports_compact_collapse()
            else []
        )
        self.expand_btn.setVisible(has_breakdown or bool(compact_metrics))
        self._update_expand_btn_glyph()
        if compact_metrics and not self._expanded:
            self._render_ratio_label()
            self._set_rows([])
            self._set_compact_metrics(compact_metrics)
            return
        self._hide_compact_metrics()
        visible = [
            m
            for m in snapshot.metrics
            if (not m.tag or self._expanded) and not m.in_header
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
        self._render_ratio_label()

    def set_ratio(
        self,
        estimate: RatioEstimate | None,
        recent: list[float] | None = None,
        live: RatioEstimate | None = None,
    ) -> None:
        """Show the session->weekly burn rate on the header right side.

        ``None`` (or a tile that isn't currently OK) hides the element. When the
        shown value is a carry-over from last week (this week still calibrating)
        it is dimmed and marked with a degree sign, with the live progress in the
        tooltip.
        """
        self._ratio_estimate = estimate
        self._ratio_recent = list(recent or [])
        self._ratio_live = live
        self._render_ratio_label()

    def _render_ratio_label(self) -> None:
        estimate = self._ratio_estimate
        text = _format_ratio_inline(estimate)
        snapshot = self._latest_snapshot
        has_session = snapshot is not None and any(
            metric.label.lower() == "session" for metric in snapshot.metrics
        )
        if (
            text is None
            or snapshot is None
            or snapshot.status != SnapshotStatus.OK
            or not has_session
            or _claude_weekly_limit_hit(snapshot)
            or (self._supports_compact_collapse() and not self._expanded)
        ):
            self.ratio_label.setVisible(False)
            self.ratio_label.setText("")
            return
        carry_over = (
            estimate is not None
            and estimate.confident
            and estimate.source == "history"
        )
        color = "#6b7280" if carry_over else "#9ca3af"
        if carry_over:
            text = f"{text}°"  # degree marker: last week's value, refining this week
        self.ratio_label.setText(
            f'<a href="ratio-history" style="color:{color}; text-decoration:none;">'
            f"{text}</a>"
        )
        self.ratio_label.setToolTip(
            _format_ratio_tooltip(estimate, self._ratio_recent, self._ratio_live)
        )
        self.ratio_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ratio_label.setVisible(True)

    def set_expanded(self, expanded: bool, *, emit: bool = True) -> None:
        if self._expanded == expanded:
            return
        self._expanded = expanded
        self._update_expand_btn_glyph()
        # Re-render rows from the latest snapshot to add/remove breakdown rows.
        if self._latest_snapshot is not None:
            self.set_snapshot(self._latest_snapshot)
        self._render_ratio_label()
        if emit:
            self.expanded_changed.emit(self.provider, expanded)

    def _on_expand_clicked(self) -> None:
        self.set_expanded(not self._expanded, emit=True)

    def _update_expand_btn_glyph(self) -> None:
        self.expand_btn.setText("▾" if self._expanded else "▸")
        if self._supports_compact_collapse():
            self.expand_btn.setToolTip(
                "Collapse to summary" if self._expanded else "Show details"
            )
        else:
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
            r = _MetricRow(self, self._colors)
            r.set_available_width(self._row_available_width())
            self._rows.append(r)
            self._layout.addWidget(r)
        while len(self._rows) > len(rows):
            r = self._rows.pop()
            self._layout.removeWidget(r)
            r.hide()
            r.setParent(None)
            r.deleteLater()
        for row, (label, pct, reset, reset_label, note, window, _tag) in zip(
            self._rows,
            rows,
        ):
            row.set_metric(label, pct, reset, reset_label, note, window)
        self._normalize_gauge_columns()
        row_width = self._row_available_width()
        for row in self._rows:
            row.set_available_width(row_width)
        self._layout.invalidate()
        self.layout().activate()
        self.updateGeometry()

    def _normalize_gauge_columns(self) -> None:
        """Give comparable gauges one consistent scale within this provider."""
        gauge_rows = [
            row
            for row in self._rows
            if not row.bar.isHidden() and not row.pct.isHidden()
        ]
        if len(gauge_rows) <= 1:
            return

        label_width = max(row.label.minimumWidth() for row in gauge_rows)
        percent_width = max(row.pct.width() for row in gauge_rows)
        reset_width = max(row._reset_natural_width for row in gauge_rows)
        for row in gauge_rows:
            row.label.setFixedWidth(label_width)
            row.pct.setFixedWidth(percent_width)
            row.reset.setFixedWidth(reset_width)
            # Reserve an empty reset column when another row has one so every
            # bar still starts and ends at the same coordinates.
            row.reset.setVisible(reset_width > 0)

    def _set_skeleton(self, labels: list[str]) -> None:
        while len(self._rows) < len(labels):
            r = _MetricRow(self, self._colors)
            r.set_available_width(self._row_available_width())
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
        self.layout().activate()
        self.updateGeometry()


class _StatusFooter(QWidget):
    """Bottom row carrying the refresh status and the resize grip.

    The hairline is painted rather than set as a stylesheet border: a plain
    QWidget ignores one unless it is also told to style its background, which
    would then paint over the panel this row sits on.
    """

    def paintEvent(self, event):  # noqa: N802
        # Filled rather than stroked: a hairline stroke lands on a half device
        # pixel at fractional display scaling and disappears.
        painter = QPainter(self)
        painter.fillRect(QRectF(0, 0, self.width(), 1), QColor(PANEL_BORDER))


class _HorizontalResizeGrip(QWidget):
    """Bottom-right handle that changes only its target window's width."""

    resize_started = pyqtSignal()
    resize_finished = pyqtSignal()

    def __init__(self, target: QWidget, parent: QWidget | None = None):
        super().__init__(parent)
        self._target = target
        self._start_global_x: int | None = None
        self._start_width = 0
        self.setFixedSize(14, 14)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setToolTip("Drag to resize width")

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self._start_global_x = int(round(event.globalPosition().x()))
        self._start_width = self._target.width()
        self.resize_started.emit()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            self._start_global_x is None
            or not event.buttons() & Qt.MouseButton.LeftButton
        ):
            super().mouseMoveEvent(event)
            return
        delta = int(round(event.globalPosition().x())) - self._start_global_x
        width = max(
            self._target.minimumWidth(),
            min(self._start_width + delta, self._target.maximumWidth()),
        )
        self._target.resize(width, self._target.height())
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            self._start_global_x is None
            or event.button() != Qt.MouseButton.LeftButton
        ):
            super().mouseReleaseEvent(event)
            return
        self._start_global_x = None
        self.resize_finished.emit()
        event.accept()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(QPen(QColor("#9ca3af"), 1))
        edge = self.width() - 2
        for offset in (4, 8, 12):
            painter.drawLine(edge - offset, edge, edge, edge - offset)


class UsageWidget(QWidget):
    """The compact always-on-top window."""

    refresh_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    sign_in_requested = pyqtSignal(str)
    details_requested = pyqtSignal(str)
    ratio_history_requested = pyqtSignal(str)
    tile_expanded_changed = pyqtSignal(str, bool)
    activated_requested = pyqtSignal()
    quit_requested = pyqtSignal()
    closed = pyqtSignal()

    def __init__(self, config: Config, parent: QWidget | None = None):
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool,
        )
        self._config = config
        self._mouse_inside = False
        self._drag_offset: QPoint | None = None
        self._press_global: QPoint | None = None
        self._drag_snap_corner: SnapCorner | None = None
        self._drag_snap_screen = None
        self._resizing_with_grip = False
        self.setMinimumWidth(WINDOW_WIDTH)
        self.setMaximumWidth(WINDOW_MAX_WIDTH)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        # Qt erases a top-level widget with the palette's Window brush before
        # paintEvent runs. paintEvent only covers the rounded rect, so on a
        # light system theme the four corners kept #f0f0f0 and showed as grey
        # notches (issue #7). Pin the brush to the panel colour so an
        # unpainted pixel can never contrast, whatever the system theme is.
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(PANEL_BG))
        self.setPalette(palette)
        # Background is drawn in paintEvent; no widget-level stylesheet — that
        # would cascade into child dialogs (Settings) and break their layout.
        self._apply_window_opacity()

        self._apply_always_on_top(config.window.always_on_top)

        self._tiles: dict[str, _ProviderTile] = {}
        self._snapshots: dict[str, UsageSnapshot | None] = {}
        self._last_fetch_at: datetime | None = None
        self._refresh_mode: str | None = None
        self._refresh_interval_minutes: int | None = None
        self._next_refresh_at: datetime | None = None
        self._cadence_short_text = ""
        self._age_text = ""
        self._refreshing = False
        self._collapsed = config.window.collapsed
        self._header_visible = config.window.show_header
        self._always_on_top_suspensions = 0
        self._collapsed_chip_max_width = 0
        self._grip_overlaid = False

        # Header bar
        self.title_icon = QLabel()
        self.title_icon.setPixmap(app_icon().pixmap(24, 24))
        self.title_icon.setFixedSize(24, 24)
        self.title_icon.setToolTip("AI Gauge")

        self.title_label = QLabel(f"AI Gauge {__version__}")
        self.title_label.setToolTip(f"ai-gauge {__version__}")
        self.title_label.setStyleSheet(
            "color:#9ca3af; font-size:10px; font-weight:600;"
        )

        self.refresh_btn = self._mini_button("", "Refresh now")
        self.refresh_btn.setIcon(
            _refresh_icon(normal="#9ca3af", active="#f3f4f6", size=16)
        )
        self.refresh_btn.setIconSize(QSize(16, 16))
        self.refresh_btn.clicked.connect(self.refresh_requested.emit)

        self.collapse_btn = self._mini_button("▾", "Switch to compact view")
        self.collapse_btn.clicked.connect(lambda: self.set_collapsed(True))

        self.settings_btn = self._mini_button("⚙", "Settings")
        self.settings_btn.clicked.connect(self.settings_requested.emit)

        self.hide_btn = self._mini_button("—", "Hide to system tray")
        self.hide_btn.clicked.connect(self.hide)

        self.close_btn = self._mini_button("✕", "Quit AI Gauge")
        self.close_btn.clicked.connect(self.quit_requested.emit)

        # Refresh status lives on the footer row, not here: the title bar
        # carries identity and controls, and the footer keeps the status
        # visible when the header is hidden.
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color:#6b7280; font-size:10px;")
        self.status_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.status_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )

        header = QHBoxLayout()
        header.setContentsMargins(8, 4, 4, 2)
        header.setSpacing(4)
        header.addWidget(self.title_icon)
        header.addWidget(self.title_label)
        header.addStretch(1)
        header.addWidget(self.refresh_btn)
        header.addWidget(self.collapse_btn)
        header.addWidget(self.settings_btn)
        header.addWidget(self.hide_btn)
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

        self._expand_btn = self._mini_button("▴", "Switch to full view")
        self._expand_btn.clicked.connect(lambda: self.set_collapsed(False))

        self._collapsed_hide_btn = self._mini_button("—", "Hide to system tray")
        self._collapsed_hide_btn.clicked.connect(self.hide)

        self._collapsed_close_btn = self._mini_button("✕", "Quit AI Gauge")
        self._collapsed_close_btn.clicked.connect(self.quit_requested.emit)

        self._collapsed_widget = QWidget(self)
        self._collapsed_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        collapsed_outer = QVBoxLayout(self._collapsed_widget)
        collapsed_outer.setContentsMargins(8, 4, 8, 6)
        collapsed_outer.setSpacing(4)

        self._collapsed_header_widget = QWidget(self._collapsed_widget)
        collapsed_header = QHBoxLayout(self._collapsed_header_widget)
        self._collapsed_header_layout = collapsed_header
        collapsed_header.setContentsMargins(0, 0, 0, 0)
        collapsed_header.setSpacing(4)
        collapsed_title = QLabel(f"AI Gauge {__version__}")
        self._collapsed_title = collapsed_title
        collapsed_title.setStyleSheet("color:#9ca3af; font-size:10px; font-weight:600;")
        self._collapsed_title_icon = QLabel()
        self._collapsed_title_icon.setPixmap(app_icon().pixmap(18, 18))
        self._collapsed_title_icon.setFixedSize(18, 18)
        self._collapsed_title_icon.setToolTip("AI Gauge")
        collapsed_header.addWidget(self._collapsed_title_icon)
        collapsed_header.addWidget(collapsed_title)
        self._collapsed_cadence_label = QLabel("")
        self._collapsed_cadence_label.setStyleSheet("color:#6b7280; font-size:10px;")
        collapsed_header.addWidget(self._collapsed_cadence_label)
        collapsed_header.addStretch(1)
        self._collapsed_age_label = QLabel("")
        self._collapsed_age_label.setStyleSheet("color:#6b7280; font-size:10px;")
        collapsed_header.addWidget(self._collapsed_age_label)
        collapsed_header.addWidget(self._expand_btn)
        collapsed_header.addWidget(self._collapsed_hide_btn)
        collapsed_header.addWidget(self._collapsed_close_btn)

        self._collapsed_summary_layout = QVBoxLayout()
        self._collapsed_summary_layout.setContentsMargins(0, 0, 0, 0)
        self._collapsed_summary_layout.setSpacing(3)
        self._collapsed_summary_layout.addWidget(self._collapsed_label)

        collapsed_outer.addWidget(self._collapsed_header_widget)
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

        self._resize_footer = _StatusFooter(self)
        self._resize_footer.setFixedHeight(FOOTER_HEIGHT)
        resize_footer_layout = QHBoxLayout(self._resize_footer)
        resize_footer_layout.setContentsMargins(8, 0, 1, 1)
        resize_footer_layout.setSpacing(4)
        resize_footer_layout.addWidget(self.status_label)
        resize_footer_layout.addStretch(1)
        self._resize_grip = _HorizontalResizeGrip(self, self._resize_footer)
        self._resize_grip.resize_started.connect(self._on_resize_started)
        self._resize_grip.resize_finished.connect(self._on_resize_finished)
        resize_footer_layout.addWidget(self._resize_grip)
        outer.addWidget(self._resize_footer)

        initial_width = max(
            WINDOW_WIDTH,
            min(config.window.width, WINDOW_MAX_WIDTH),
        )
        self.resize(QSize(initial_width, WINDOW_MIN_HEIGHT))
        if config.window.x is not None and config.window.y is not None:
            self.move(QPoint(config.window.x, config.window.y))
            self._clamp_to_visible_screen()

        # Drag-by-anywhere

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
            tile = _ProviderTile(
                provider,
                display_name,
                self._colors_for(provider),
                self,
            )
            tile.sign_in_requested.connect(self.sign_in_requested.emit)
            tile.details_requested.connect(self.details_requested.emit)
            tile.ratio_history_requested.connect(self.ratio_history_requested.emit)
            tile.expanded_changed.connect(self.tile_expanded_changed.emit)
            tile.expanded_changed.connect(
                lambda _provider, _expanded: self._refit_height()
            )
            if provider in (getattr(self._config, "collapsed_tiles", []) or []):
                tile.set_expanded(False, emit=False)
            elif provider in (self._config.expanded_tiles or []):
                tile.set_expanded(True, emit=False)
            tile.set_available_width(self._available_tile_width())
            self._tiles[provider] = tile
            self._insert_tile_in_provider_order(provider, tile)
            self._refit_height()
        else:
            self._tiles[provider].header.setText(display_name)
            self._tiles[provider].set_colors(self._colors_for(provider))
            self._tiles[provider].set_available_width(self._available_tile_width())
        return self._tiles[provider]

    def _colors_for(self, provider: str) -> ColorThresholds:
        return thresholds_for_provider(self._config, provider)

    def _tile_sort_key(self, provider: str) -> tuple[int, int, str]:
        kinds = account_kinds()
        accounts = [
            account
            for account in getattr(self._config, "browser_accounts", [])
            if account.kind in kinds
        ]
        account_ids = [account.id for account in accounts]
        order = provider_order(self._config)
        if provider in account_ids:
            position = account_ids.index(provider)
            family = accounts[position].kind
        else:
            position = 10_000
            family = _provider_family(provider)
        # A family the order does not name ranks after every named one.
        rank = order.index(family) if family in order else len(order)
        return (rank, position, provider)

    def apply_provider_order(self) -> None:
        """Re-sort the existing tiles and summary chips into the saved order.

        Tiles move only among the layout slots they already occupy, so any
        non-tile item keeps its position. A layout already in order is left
        alone.
        """
        slots: list[int] = []
        current: list[_ProviderTile] = []
        for i in range(self._tile_layout.count()):
            item_widget = self._tile_layout.itemAt(i).widget()
            if isinstance(item_widget, _ProviderTile):
                slots.append(i)
                current.append(item_widget)
        wanted = sorted(current, key=lambda tile: self._tile_sort_key(tile.provider))
        if wanted != current:
            for tile in current:
                self._tile_layout.removeWidget(tile)
            for slot, tile in zip(slots, wanted):
                self._tile_layout.insertWidget(slot, tile)
            self._tile_layout.invalidate()
        self._refresh_collapsed_summary()

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

    def set_ratio(
        self,
        provider: str,
        estimate: RatioEstimate | None,
        recent: list[float] | None = None,
        live: RatioEstimate | None = None,
    ) -> None:
        tile = self._tiles.get(provider)
        if tile is not None:
            tile.set_ratio(estimate, recent, live)

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
        """Refit automatic height after content or responsive layout changes."""
        QTimer.singleShot(0, self._do_refit_height)

    def _available_tile_width(self) -> int:
        margins = self._tile_layout.contentsMargins()
        return max(
            0,
            self.width() - margins.left() - margins.right() - 8,
        )

    def _minimum_expanded_width(self) -> int:
        content_width = max(
            (tile.minimum_inline_width() for tile in self._tiles.values()),
            default=0,
        )
        if content_width:
            margins = self._tile_layout.contentsMargins()
            content_width += margins.left() + margins.right() + 8
        return min(
            WINDOW_MAX_WIDTH,
            max(EXPANDED_MIN_WIDTH, content_width),
        )

    def _collapsed_chrome_width(self) -> int:
        margins = self._collapsed_widget.layout().contentsMargins()
        return margins.left() + margins.right()

    def _collapsed_width_for(self, *, compact: bool) -> int:
        """Pill width that fits its content, with the header fully shed or shown.

        ``compact=True`` gives the floor the user may drag down to — icon and
        buttons only. ``compact=False`` gives the comfortable width that shows
        the whole header. Both account for the widest summary chip.

        The header must be measured explicitly rather than read as-is: a
        previous responsive pass may have already shortened it, which would
        otherwise make the floor drift with whatever was last displayed.
        """
        if not self._header_visible:
            return max(
                COLLAPSED_MIN_WIDTH,
                min(
                    WINDOW_MAX_WIDTH,
                    self._collapsed_chip_max_width + self._collapsed_chrome_width(),
                ),
            )

        title = self._collapsed_title.text()
        cadence_visible = not self._collapsed_cadence_label.isHidden()
        age_visible = not self._collapsed_age_label.isHidden()
        layout = self._collapsed_header_layout
        if compact:
            self._collapsed_title.setText("")
            self._collapsed_cadence_label.setVisible(False)
            self._collapsed_age_label.setVisible(False)
        else:
            self._collapsed_title.setText(f"AI Gauge {__version__}")
            self._collapsed_cadence_label.setVisible(
                bool(self._collapsed_cadence_label.text())
            )
            self._collapsed_age_label.setVisible(
                bool(self._collapsed_age_label.text())
            )
        layout.invalidate()
        layout.activate()
        header_width = layout.minimumSize().width()
        self._collapsed_title.setText(title)
        self._collapsed_cadence_label.setVisible(cadence_visible)
        self._collapsed_age_label.setVisible(age_visible)
        layout.invalidate()
        layout.activate()

        # _collapsed_chip_max_width is recorded by _rebuild_collapsed_summary,
        # which already builds every chip — measuring here would construct a
        # second throwaway set on each refit.
        content = max(header_width, self._collapsed_chip_max_width)
        return max(
            COLLAPSED_MIN_WIDTH,
            min(WINDOW_MAX_WIDTH, content + self._collapsed_chrome_width()),
        )

    def _minimum_collapsed_width(self) -> int:
        """Narrowest the pill may be dragged: icon, buttons, widest chip."""
        return self._collapsed_width_for(compact=True)

    def _preferred_collapsed_width(self) -> int:
        """Width used when the user hasn't picked one: fits the full header."""
        return self._collapsed_width_for(compact=False)

    def _target_collapsed_width(self) -> int:
        """Width to apply in collapsed mode: the user's, else fit-to-content."""
        minimum = self._minimum_collapsed_width()
        saved = self._config.window.collapsed_width
        if saved is None:
            return max(minimum, self._preferred_collapsed_width())
        return max(minimum, min(saved, WINDOW_MAX_WIDTH))

    def _apply_responsive_collapsed_header(self) -> None:
        """Shed header detail as the pill narrows, least useful part first.

        Mirrors _apply_responsive_header for the collapsed pill: version, then
        the "Xs ago" stamp, then the cadence, then the name — leaving the icon
        and the three buttons, which always stay.
        """
        if not self._header_visible:
            return

        layout = self._collapsed_header_layout
        available = self.width() - self._collapsed_chrome_width()
        at_width_floor = self.width() <= self._minimum_collapsed_width()

        def overflows() -> bool:
            layout.invalidate()
            layout.activate()
            return layout.minimumSize().width() > available

        self._collapsed_title.setText(f"AI Gauge {__version__}")
        self._collapsed_cadence_label.setVisible(
            bool(self._collapsed_cadence_label.text())
        )
        self._collapsed_age_label.setVisible(bool(self._collapsed_age_label.text()))
        if overflows():
            self._collapsed_title.setText("AI Gauge")
        if overflows() and not self._collapsed_age_label.isHidden():
            self._collapsed_age_label.hide()
        if overflows() and not self._collapsed_cadence_label.isHidden():
            self._collapsed_cadence_label.hide()
        if overflows() or at_width_floor:
            self._collapsed_title.setText("")
        layout.invalidate()
        layout.activate()

    def _apply_responsive_layout(self) -> bool:
        available_width = self._available_tile_width()
        changed = False
        for tile in self._tiles.values():
            changed = tile.set_available_width(available_width) or changed
        return changed

    def _available_expanded_height(self) -> int:
        screen = (
            QApplication.screenAt(self.frameGeometry().center())
            or self.screen()
            or QApplication.primaryScreen()
        )
        if screen is None:
            return WINDOW_MAX_HEIGHT
        return max(
            WINDOW_MIN_HEIGHT,
            min(WINDOW_MAX_HEIGHT, screen.availableGeometry().height() - 8),
        )

    def _do_refit_height(self) -> None:
        if self._resizing_with_grip:
            return
        if self._collapsed:
            # Width is content-derived (or the user's saved pill width) rather
            # than a fixed WINDOW_WIDTH, so one provider doesn't get a mostly
            # empty 340px strip. Height stays fixed to the content.
            minimum_width = self._minimum_collapsed_width()
            self.setMinimumWidth(minimum_width)
            self.setMaximumWidth(WINDOW_MAX_WIDTH)
            target_width = max(minimum_width, self._target_collapsed_width())
            if self.width() != target_width:
                self.resize(target_width, self.height())
                # The chips were wrapped against the previous width. A pill
                # that just narrowed has to reflow before its height is
                # measured, or a chip is clipped instead of moving down a row.
                self._refresh_collapsed_summary()
            self._apply_responsive_collapsed_header()
            # The floor is one chip row, not a fixed pill height: a pill wide
            # enough to fit its chips on one row — or one with the header
            # hidden — must not reserve space for a second row.
            target_height = max(
                COLLAPSED_MIN_HEIGHT,
                min(WINDOW_MAX_HEIGHT, self._collapsed_widget.sizeHint().height()),
            )
            self.setFixedHeight(target_height)
            self._position_overlaid_grip()
            self._restore_position_after_refit()
            return

        # Release the collapsed/fitted constraints before measuring this pass.
        self.setMaximumWidth(WINDOW_MAX_WIDTH)
        minimum_width = self._minimum_expanded_width()
        self.setMinimumWidth(minimum_width)
        target_width = max(
            minimum_width,
            min(self.width(), WINDOW_MAX_WIDTH),
        )
        if self.width() != target_width:
            self.resize(target_width, self.height())
        self._apply_responsive_header()
        self._apply_responsive_layout()

        self._tile_layout.invalidate()
        self._tile_container.updateGeometry()
        self._tile_scroll.updateGeometry()
        self.updateGeometry()
        self.layout().invalidate()
        self.layout().activate()
        header_height = (
            self._header_widget.sizeHint().height() if self._header_visible else 0
        )
        # The footer's height is fixed; its layout hint can come out smaller,
        # and under-reporting it here lets the tiles overlap the footer row.
        footer_height = max(FOOTER_HEIGHT, self._resize_footer.sizeHint().height())
        tile_height = self._tile_container.sizeHint().height()
        height_limit = self._available_expanded_height()
        max_tile_height = max(
            40,
            height_limit - header_height - footer_height,
        )
        fitted_tile_height = min(tile_height, max_tile_height)
        fitted_height = max(
            WINDOW_MIN_HEIGHT,
            min(
                height_limit,
                header_height + fitted_tile_height + footer_height,
            ),
        )
        self._tile_scroll.setFixedHeight(fitted_tile_height)
        # Height is content-owned. Locking it also turns the corner grip into a
        # width-only control and prevents both clipping and empty vertical space.
        self.setFixedHeight(fitted_height)
        if self.width() != target_width:
            self.resize(target_width, fitted_height)
        self._restore_position_after_refit()

    def set_refreshing(self, refreshing: bool) -> None:
        self.refresh_btn.setEnabled(not refreshing)
        self._refreshing = refreshing
        if refreshing:
            self._collapsed_age_label.setText("refreshing…")
        self._apply_footer_status()
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
        self._age_text = text
        self._collapsed_age_label.setText("refreshing…" if self._refreshing else text)

    def _apply_responsive_header(self) -> None:
        self.title_label.setText(f"AI Gauge {__version__}")
        header_layout = self._header_widget.layout()
        header_layout.setContentsMargins(8, 4, 4, 2)
        header_layout.setSpacing(4)
        header_layout.invalidate()
        header_layout.activate()

        # Progressively reduce the identity text only when the measured header
        # no longer fits. Core controls always remain visible.
        if header_layout.minimumSize().width() > self.width():
            self.title_label.setText("AI Gauge")
            header_layout.invalidate()
            header_layout.activate()
        if header_layout.minimumSize().width() > self.width():
            header_layout.setContentsMargins(4, 4, 2, 2)
            header_layout.setSpacing(2)
            header_layout.invalidate()
            header_layout.activate()
        if header_layout.minimumSize().width() > self.width():
            self.title_label.setText("AI")
            header_layout.invalidate()
            header_layout.activate()

    def _footer_status_texts(self) -> tuple[str, str, str]:
        """Footer text at full and narrow widths, plus its shared tooltip."""
        if self._refreshing:
            return "Refreshing…", "Refreshing…", "Refresh is currently running."
        age = f"Updated {self._age_text}" if self._age_text else ""
        remaining = self._cadence_short_text
        countdown = f"next {remaining}" if remaining else ""
        full = " · ".join(part for part in (age, countdown) if part)
        lines = []
        if self._last_fetch_at is not None:
            lines.append(
                "Last refresh: "
                f"{self._last_fetch_at.strftime('%Y-%m-%d %H:%M:%S')} "
                f"({self._age_text})"
            )
        if self._next_refresh_at is not None:
            interval = self._refresh_interval_minutes or 0
            lines.append(
                "Next auto-refresh: "
                f"{self._next_refresh_at.strftime('%Y-%m-%d %H:%M:%S')} "
                f"— {self._refresh_mode} mode, {interval} min cadence"
            )
        if full:
            lines.append("Click to refresh now.")
        return full, countdown or full, "\n".join(lines)

    def _apply_footer_status(self) -> None:
        """Render the footer status, shedding the age half if it cannot fit."""
        full, short, tooltip = self._footer_status_texts()
        color = "#9ca3af" if self._refresh_mode == "active" else "#6b7280"
        self.status_label.setStyleSheet(f"color:{color}; font-size:10px;")
        self.status_label.setToolTip(tooltip)
        self.status_label.setText(full)
        self.status_label.setVisible(bool(full))
        footer_layout = self._resize_footer.layout()
        footer_layout.invalidate()
        footer_layout.activate()
        if (
            not self.status_label.isHidden()
            and short != full
            and footer_layout.minimumSize().width() > self.width()
        ):
            self.status_label.setText(short)
            footer_layout.invalidate()
            footer_layout.activate()
        if (
            not self.status_label.isHidden()
            and footer_layout.minimumSize().width() > self.width()
        ):
            self.status_label.hide()

    def _refresh_cadence_label(self) -> None:
        if self._refresh_mode is None or self._next_refresh_at is None:
            self._cadence_short_text = ""
            self._collapsed_cadence_label.setText("")
            self._collapsed_cadence_label.setToolTip("")
            self._apply_footer_status()
            return
        remaining = _format_countdown(self._next_refresh_at)
        self._cadence_short_text = remaining
        self._collapsed_cadence_label.setText(remaining)
        interval = self._refresh_interval_minutes or 0
        tooltip = (
            f"In {self._refresh_mode} mode — {interval} min cadence. "
            f"Next auto-refresh: {self._next_refresh_at.strftime('%Y-%m-%d %H:%M:%S')}."
        )
        self._collapsed_cadence_label.setToolTip(tooltip)
        color = "#9ca3af" if self._refresh_mode == "active" else "#6b7280"
        self._collapsed_cadence_label.setStyleSheet(f"color:{color}; font-size:10px;")
        self._apply_footer_status()

    def _session_summary_for(self, provider: str) -> str:
        display = display_name_for_account(self._config, provider)
        snapshot = self._snapshots.get(provider)
        if snapshot is None:
            return f"{display} --"
        if snapshot.status == SnapshotStatus.AUTH_REQUIRED:
            suffix = "add key" if _is_api_key_tile(provider) else "sign in"
            return f"{display} {suffix}"
        if snapshot.status == SnapshotStatus.ERROR:
            metric = next(
                (m for m in snapshot.metrics if m.label.lower() == "session"),
                snapshot.metrics[0] if snapshot.metrics else None,
            )
            if metric is not None:
                return f"{display} {_format_summary_percent(metric.percent_used)} stale"
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
            self._collapsed_chip_max_width = 0
            return
        self._collapsed_label.setText("")
        self._collapsed_label.hide()
        providers = sorted(self._tiles, key=self._tile_sort_key)
        margins = self._collapsed_widget.layout().contentsMargins()
        # Wrap chips against the pill's real width rather than a fixed 340px,
        # so a narrowed pill reflows instead of overflowing (issue #7).
        available_width = max(
            COLLAPSED_MIN_WIDTH,
            (self.width() if self._collapsed else self._target_collapsed_width())
            - margins.left()
            - margins.right(),
        )
        row_widget: QWidget | None = None
        row_layout: QHBoxLayout | None = None
        row_width = 0
        spacing = 5
        max_chip_width = 0
        for provider in providers:
            chip = self._summary_chip(provider)
            chip_width = chip.width()
            max_chip_width = max(max_chip_width, chip_width)
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
        self._collapsed_chip_max_width = max_chip_width
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
        # A named account shows only its name, except the default account of a
        # browser-session kind (id == kind), which keeps the provider name.
        display = (
            account.name.strip()
            if account is not None
            and provider not in browser_session_kinds()
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
            is_api_key = _is_api_key_tile(provider)
            text = f"{display} {'add key' if is_api_key else 'sign in'}"
            tooltip = snapshot.error or (
                "API key required." if is_api_key else "Sign in required."
            )
            kind = "auth"
        elif snapshot.status == SnapshotStatus.ERROR:
            metric = next(
                (m for m in snapshot.metrics if m.label.lower() == "session"),
                snapshot.metrics[0] if snapshot.metrics else None,
            )
            if metric is not None:
                percent = metric.percent_used
                pace = _time_elapsed_percent(metric.resets_at, metric.window)
                text = f"{display} {_format_summary_percent(percent)} stale"
                tooltip = snapshot.error or "Refresh failed."
                pace_line = _pace_tooltip_line(metric.resets_at, metric.window)
                if pace_line:
                    tooltip += "\n\n" + pace_line
            else:
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
        chip.set_state(text, percent, kind, pace, self._colors_for(provider))
        chip.setToolTip(tooltip)
        return chip

    def set_collapsed(self, collapsed: bool) -> None:
        if self._collapsed == collapsed:
            return
        if collapsed:
            self._save_expanded_width()
        self._collapsed = collapsed
        self._config.window.collapsed = collapsed
        self._apply_collapsed_state(save=True)

    def set_header_visible(self, visible: bool) -> None:
        """Show or hide the controls header without changing content density."""
        if self._header_visible == visible:
            return
        self._header_visible = visible
        self._config.window.show_header = visible
        self._apply_collapsed_state(save=False)
        self._config.save()

    def _apply_header_visibility(self) -> None:
        self._header_widget.setVisible(not self._collapsed and self._header_visible)
        self._collapsed_header_widget.setVisible(
            self._collapsed and self._header_visible
        )

    def _apply_collapsed_state(self, *, save: bool) -> None:
        self._collapsed_widget.setVisible(self._collapsed)
        self._apply_header_visibility()
        self._tile_scroll.setVisible(not self._collapsed)
        self._tile_container.setVisible(not self._collapsed)
        self._resize_footer.setVisible(not self._collapsed)
        # The pill is width-resizable now and the grip is its only affordance
        # for that, but a footer row would add ~14px to a widget whose whole
        # point is being small. Float the grip over the corner instead.
        self._set_grip_overlaid(self._collapsed)
        self._refresh_collapsed_summary()
        if self._collapsed:
            self._do_refit_height()
        else:
            # Release compact mode's fixed size, restore the saved width, and
            # let visible content determine both the width floor and height.
            self.setMaximumWidth(WINDOW_MAX_WIDTH)
            minimum_width = self._minimum_expanded_width()
            self.setMinimumWidth(minimum_width)
            self.setMaximumHeight(WINDOW_MAX_HEIGHT)
            self.setMinimumHeight(WINDOW_MIN_HEIGHT)
            target_width = max(
                minimum_width,
                min(self._config.window.width, WINDOW_MAX_WIDTH),
            )
            self.resize(target_width, WINDOW_MIN_HEIGHT)
            self._refit_height()
        self._restore_position_after_refit()
        if save:
            self._config.window.collapsed = self._collapsed
            self._config.save()

    def _set_grip_overlaid(self, overlaid: bool) -> None:
        """Move the resize grip between the footer row and a corner overlay."""
        if overlaid == self._grip_overlaid:
            return
        self._grip_overlaid = overlaid
        footer_layout = self._resize_footer.layout()
        if overlaid:
            footer_layout.removeWidget(self._resize_grip)
            self._resize_grip.setParent(self)
            self._resize_grip.show()
        else:
            footer_layout.addWidget(self._resize_grip)
        self._position_overlaid_grip()

    def _position_overlaid_grip(self) -> None:
        if not self._grip_overlaid:
            return
        grip = self._resize_grip
        grip.move(
            self.width() - grip.width() - 3,
            self.height() - grip.height() - 3,
        )
        grip.raise_()

    def _apply_always_on_top(self, on: bool) -> None:
        flags = self.windowFlags()
        updated_flags = flags
        if on:
            updated_flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            updated_flags &= ~Qt.WindowType.WindowStaysOnTopHint
        if updated_flags != flags:
            self.setWindowFlags(updated_flags)

    def set_always_on_top(self, on: bool) -> None:
        """Persist and apply the user's topmost preference."""
        if self._config.window.always_on_top == on:
            return
        self._config.window.always_on_top = on
        self._config.save()
        self._set_suspended_topmost(on and not self._always_on_top_suspensions)

    def set_snap_to_corners(self, on: bool) -> None:
        """Persist corner snapping and release any anchor when it is disabled."""
        if self._config.window.snap_to_corners == on:
            return
        self._config.window.snap_to_corners = on
        if on:
            self._update_snap_anchor_from_position()
        else:
            self._config.window.snap_corner = None
            self._drag_snap_corner = None
            self._drag_snap_screen = None
            self._remember_position()
        self._config.save()

    def _set_native_topmost(self, on: bool) -> bool:
        """Toggle Windows topmost state without recreating the Qt window."""
        if sys.platform != "win32" or not self.isVisible():
            return False
        try:
            hwnd_topmost = -1
            hwnd_notopmost = -2
            swp_no_move = 0x0002
            swp_no_size = 0x0001
            swp_no_activate = 0x0010
            return bool(
                ctypes.windll.user32.SetWindowPos(
                    int(self.winId()),
                    hwnd_topmost if on else hwnd_notopmost,
                    0,
                    0,
                    0,
                    0,
                    swp_no_move | swp_no_size | swp_no_activate,
                )
            )
        except (AttributeError, OSError):
            return False

    def _set_suspended_topmost(self, on: bool) -> None:
        """Change temporary topmost state, avoiding a visible-window flash."""
        if self._set_native_topmost(on):
            return
        was_visible = self.isVisible()
        self._apply_always_on_top(on)
        if was_visible:
            self.show()

    def suspend_always_on_top(self) -> None:
        """Drop always-on-top so a spawned browser window can come forward.

        Used while a Connect or Settings dialog is open: those dialogs ask
        the user to interact with a Chrome/Edge window the app launched,
        and an always-on-top widget would sit over it.
        """
        self._always_on_top_suspensions += 1
        if self._always_on_top_suspensions == 1:
            self._set_suspended_topmost(False)

    def restore_always_on_top(self) -> None:
        """Re-apply the configured always-on-top setting after a dialog closes."""
        self._always_on_top_suspensions = max(0, self._always_on_top_suspensions - 1)
        if self._always_on_top_suspensions:
            return
        self._set_suspended_topmost(self._config.window.always_on_top)

    def _target_window_opacity(self) -> float:
        if not self._config.window.fade_when_inactive:
            return 1.0
        if self._mouse_inside or self.isActiveWindow() or self._drag_offset is not None:
            return 1.0
        return self._config.window.opacity

    def _apply_window_opacity(self) -> None:
        self.setWindowOpacity(self._target_window_opacity())

    def apply_window_settings(self) -> None:
        """Re-read window-related fields from config and apply."""
        was_visible = self.isVisible()
        self._apply_window_opacity()
        self._apply_always_on_top(
            self._config.window.always_on_top and not self._always_on_top_suspensions
        )
        self._collapsed = self._config.window.collapsed
        self._header_visible = self._config.window.show_header
        self._apply_collapsed_state(save=False)
        self._apply_corner_mask()
        self.update()
        if was_visible:
            self.show()  # re-applying flags hides the window
            self._apply_window_opacity()

    def _is_popup_window(self) -> bool:
        window_type = self.windowFlags() & Qt.WindowType.WindowType_Mask
        return window_type == Qt.WindowType.Popup

    def _screen_for_position(self, point: QPoint | None = None):
        # A release point expresses the destination screen during a drag. For
        # automatic refits, prefer the existing top-left: a right-anchored pill
        # can temporarily grow across a monitor boundary before it is moved
        # back, making its frame center identify the wrong screen.
        candidates = [point, self.pos(), self.frameGeometry().center()]
        for candidate in candidates:
            if candidate is None:
                continue
            screen = QApplication.screenAt(candidate)
            if screen is not None:
                return screen
        return self.screen() or QApplication.primaryScreen()

    def _corner_gaps(
        self, position: QPoint, corner: SnapCorner, screen
    ) -> tuple[int, int]:
        geo = screen.availableGeometry()
        left_gap = abs(position.x() - geo.left())
        right_gap = abs(position.x() + self.width() - 1 - geo.right())
        top_gap = abs(position.y() - geo.top())
        bottom_gap = abs(position.y() + self.height() - 1 - geo.bottom())
        horizontal_gap = left_gap if corner.endswith("left") else right_gap
        vertical_gap = top_gap if corner.startswith("top") else bottom_gap
        return horizontal_gap, vertical_gap

    def _position_near_corner(
        self,
        position: QPoint,
        corner: SnapCorner,
        screen,
        distance: int,
    ) -> bool:
        return max(self._corner_gaps(position, corner, screen)) <= distance

    def _corner_near_position(
        self,
        position: QPoint,
        screen,
        distance: int = CORNER_SNAP_DISTANCE,
    ) -> SnapCorner | None:
        corners: tuple[SnapCorner, ...] = (
            "top_left",
            "top_right",
            "bottom_left",
            "bottom_right",
        )
        corner, gaps = min(
            ((corner, self._corner_gaps(position, corner, screen)) for corner in corners),
            key=lambda item: max(item[1]),
        )
        if max(gaps) > distance:
            return None
        return corner

    def _corner_near_current_position(self, screen) -> SnapCorner | None:
        return self._corner_near_position(self.pos(), screen)

    def _screen_for_drag_position(self, position: QPoint, pointer: QPoint | None = None):
        # Choose by the proposed window center rather than by the pointer. This
        # avoids jumping to an adjacent monitor just because the grabbed point
        # crossed its boundary while most of the widget remained on this one.
        center = position + QPoint(self.width() // 2, self.height() // 2)
        return QApplication.screenAt(center) or self._screen_for_position(
            pointer or position
        )

    def _move_during_drag(
        self, position: QPoint, pointer: QPoint | None = None
    ) -> None:
        """Move to the raw drag position or show a transient magnetic snap."""
        if not self._config.window.snap_to_corners or self._is_popup_window():
            self._drag_snap_corner = None
            self._drag_snap_screen = None
            self.move(position)
            return

        # Once captured, use a slightly larger release zone. The eight-pixel
        # hysteresis prevents a one-pixel pointer wobble from flickering the
        # widget in and out of the corner, while dragging away still detaches.
        if self._drag_snap_corner is not None and self._drag_snap_screen is not None:
            if self._position_near_corner(
                position,
                self._drag_snap_corner,
                self._drag_snap_screen,
                CORNER_SNAP_RELEASE_DISTANCE,
            ):
                self.move(
                    self._snap_target(self._drag_snap_corner, self._drag_snap_screen)
                )
                return
            self._drag_snap_corner = None
            self._drag_snap_screen = None

        screen = self._screen_for_drag_position(position, pointer)
        if screen is None:
            self.move(position)
            return
        corner = self._corner_near_position(position, screen)
        if corner is None:
            self.move(position)
            return
        self._drag_snap_corner = corner
        self._drag_snap_screen = screen
        self.move(self._snap_target(corner, screen))

    def _snap_target(self, corner: SnapCorner, screen) -> QPoint:
        geo = screen.availableGeometry()
        max_x = max(geo.left(), geo.right() - self.width() + 1)
        max_y = max(geo.top(), geo.bottom() - self.height() + 1)
        left_x = min(max_x, geo.left() + CORNER_SNAP_INSET)
        right_x = max(geo.left(), max_x - CORNER_SNAP_INSET)
        top_y = min(max_y, geo.top() + CORNER_SNAP_INSET)
        bottom_y = max(geo.top(), max_y - CORNER_SNAP_INSET)
        x = left_x if corner.endswith("left") else right_x
        y = top_y if corner.startswith("top") else bottom_y
        return QPoint(x, y)

    def _remember_position(self) -> None:
        self._config.window.x = self.x()
        self._config.window.y = self.y()

    def _apply_snap_anchor(self, screen=None) -> bool:
        corner = self._config.window.snap_corner
        if (
            not self._config.window.snap_to_corners
            or corner is None
            or self._drag_offset is not None
            or self._is_popup_window()
        ):
            return False
        screen = screen or self._screen_for_position()
        if screen is None:
            return False
        target = self._snap_target(corner, screen)
        if self.pos() != target:
            self.move(target)
        self._remember_position()
        return True

    def _restore_position_after_refit(self) -> None:
        if self._drag_offset is not None:
            return
        if not self._apply_snap_anchor():
            self._clamp_to_visible_screen()
            self._remember_position()

    def _update_snap_anchor_from_position(self, point: QPoint | None = None) -> None:
        if self._is_popup_window():
            return
        if not self._config.window.snap_to_corners:
            self._config.window.snap_corner = None
            self._clamp_to_visible_screen()
            self._remember_position()
            return
        screen = self._screen_for_position(point)
        if screen is None:
            return
        self._config.window.snap_corner = self._corner_near_current_position(screen)
        if not self._apply_snap_anchor(screen):
            self._clamp_to_visible_screen()
            self._remember_position()

    def _clamp_to_visible_screen(self) -> None:
        """Pull the window fully onto a visible screen.

        The saved x/y are device-independent pixels captured at whatever
        display scale was active when the user last moved the widget. Raising
        the OS scale (e.g. to 175% or 200%) shrinks the logical desktop, so a
        spot that was on-screen at 100-150% can land entirely outside the
        visible area — the app keeps running (tray icon, Settings) but the
        widget never appears. Unplugging the monitor it was parked on does the
        same. Clamp into the available geometry so it always comes back.
        """
        pos = self.pos()
        screen = (
            QApplication.screenAt(pos)
            or self.screen()
            or QApplication.primaryScreen()
        )
        if screen is None:
            return
        geo = screen.availableGeometry()
        # geo.right()/bottom() are inclusive, so the last fully-visible top-left
        # is right - width + 1 (clamped below left/top for tiny screens).
        max_x = max(geo.left(), geo.right() - self.width() + 1)
        max_y = max(geo.top(), geo.bottom() - self.height() + 1)
        x = max(geo.left(), min(pos.x(), max_x))
        y = max(geo.top(), min(pos.y(), max_y))
        if x != pos.x() or y != pos.y():
            self.move(x, y)

    def showEvent(self, event):  # noqa: N802
        # A DPI/scale or monitor change can happen while the widget is hidden;
        # restore its corner anchor or clamp it on every show.
        super().showEvent(event)
        self._restore_position_after_refit()
        self._apply_window_opacity()
        self._apply_corner_mask()

    def enterEvent(self, event):  # noqa: N802
        self._mouse_inside = True
        self._apply_window_opacity()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._mouse_inside = False
        self._apply_window_opacity()
        super().leaveEvent(event)

    def changeEvent(self, event):  # noqa: N802
        if event.type() == QEvent.Type.ActivationChange:
            self._apply_window_opacity()
        super().changeEvent(event)

    def _on_resize_started(self) -> None:
        self._resizing_with_grip = True

    def _on_resize_finished(self) -> None:
        # Keep height fixed for the drag, then apply responsive rows once the
        # final width is known and the pointer no longer needs to follow them.
        self._resizing_with_grip = False
        if self._collapsed:
            # Record the dragged width *before* refitting: the refit resizes to
            # the saved width, so saving afterwards would snap the pill back to
            # its fit-to-content default and discard the drag.
            self._save_collapsed_width()
            # Reflow the chips into the new width before measuring height.
            self._refresh_collapsed_summary()
        self._do_refit_height()
        self._save_expanded_width()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._apply_corner_mask()
        self._position_overlaid_grip()
        if hasattr(self, "status_label"):
            self._apply_responsive_header()
            self._refresh_cadence_label()

    def _save_collapsed_width(self) -> None:
        if not self._collapsed:
            return
        self._config.window.collapsed_width = max(
            COLLAPSED_MIN_WIDTH,
            min(self.width(), WINDOW_MAX_WIDTH),
        )
        self._config.save()

    def _save_expanded_width(self) -> None:
        if self._collapsed:
            return
        self._config.window.width = max(
            EXPANDED_MIN_WIDTH,
            min(self.width(), WINDOW_MAX_WIDTH),
        )
        # Retain these serialized fields for compatibility with older configs,
        # but height is no longer user-controlled or restored.
        self._config.window.height = _clamp_height(self.height())
        self._config.window.manually_resized = False
        self._config.save()

    def show_as_popover(self, anchor_global_x: int, anchor_global_y: int) -> None:
        """Show the widget below ``(anchor_global_x, anchor_global_y)``.

        Used as the macOS menu-bar drop-down. The widget gets ``Qt.Popup``
        flags so it dismisses when the user clicks outside it. The widget's
        own X button still hides it; settings dialogs still spawn correctly
        because they're separate top-level windows.
        """
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Popup)
        self.setWindowOpacity(1.0)
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
            self._press_global = event.globalPosition().toPoint()
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            self._drag_snap_corner = None
            self._drag_snap_screen = None
            self._apply_window_opacity()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            self._drag_offset is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            pointer = event.globalPosition().toPoint()
            self._move_during_drag(pointer - self._drag_offset, pointer)
            event.accept()

    def _finish_window_drag(self, release_point: QPoint) -> None:
        if self._drag_offset is None:
            return
        # Evaluate the release coordinate too: on some platforms the final
        # mouse position arrives only with the release rather than a move.
        self._move_during_drag(release_point - self._drag_offset, release_point)
        corner = self._drag_snap_corner
        screen = self._drag_snap_screen

        # Refit while the drag marker is still set so the previous persisted
        # anchor cannot pull the window back before this drag is committed.
        self._do_refit_height()
        self._drag_offset = None
        self._drag_snap_corner = None
        self._drag_snap_screen = None

        if not self._is_popup_window():
            if self._config.window.snap_to_corners and corner is not None:
                self._config.window.snap_corner = corner
                self._apply_snap_anchor(screen)
            else:
                self._config.window.snap_corner = None
                self._clamp_to_visible_screen()
                self._remember_position()

        self._apply_window_opacity()
        self._remember_position()
        self._config.window.collapsed = self._collapsed
        self._save_expanded_width()
        self._save_collapsed_width()
        self._config.save()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._drag_offset is None:
            self._press_global = None
            super().mouseReleaseEvent(event)
            return
        release = event.globalPosition().toPoint()
        refresh = self._is_status_click(release)
        self._finish_window_drag(release)
        self._press_global = None
        if refresh:
            self.refresh_requested.emit()
        event.accept()

    def _is_status_click(self, release_global: QPoint) -> bool:
        """True for a press and release on the footer status without a drag.

        The window is draggable from anywhere, so the press stays ambiguous
        until release: a click refreshes, any real movement moves the window.
        """
        press = self._press_global
        if press is None or not self.status_label.isVisibleTo(self):
            return False
        if (release_global - press).manhattanLength() > STATUS_CLICK_SLOP:
            return False
        return self.status_label.rect().contains(
            self.status_label.mapFromGlobal(press)
        )

    def closeEvent(self, event):  # noqa: N802
        self._do_refit_height()
        self._remember_position()
        self._config.window.collapsed = self._collapsed
        self._save_expanded_width()
        self._save_collapsed_width()
        self._config.save()
        self.closed.emit()
        super().closeEvent(event)

    def _corner_radius(self) -> int:
        """Corner radius for the panel, or 0 when square corners are configured."""
        return 0 if self._config.window.square_corners else PANEL_CORNER_RADIUS

    def _apply_corner_mask(self) -> None:
        """Clip the window to its rounded outline.

        Without this, the corner pixels outside the rounded rect still belong
        to the window and are erased with the palette brush. Masking removes
        them from the window shape outright, which — unlike
        WA_TranslucentBackground — needs no compositor, so it also holds up
        over RDP and on bare X11 sessions (issue #7).
        """
        radius = self._corner_radius()
        if radius <= 0:
            self.clearMask()
            return
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), radius, radius)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    # Subtle rounded background
    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(PANEL_BG))
        painter.setPen(QPen(QColor(PANEL_BORDER), 1))
        radius = self._corner_radius()
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), radius, radius)
