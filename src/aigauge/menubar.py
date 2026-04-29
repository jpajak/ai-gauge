"""macOS menu-bar rendering — Stats-style ``● 42% ● 78% ● 15%`` icon.

Produces a wide ``QPixmap`` from the current snapshot dict that ``app.py``
hands to ``QSystemTrayIcon.setIcon()``. The same threshold colors as the
floating-widget tiles are used so the two surfaces stay visually consistent.

The pixmap is rendered at ``device_pixel_ratio`` ×, then has its DPR set so
Qt downscales correctly on Retina displays.
"""
from __future__ import annotations

from typing import Iterable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPixmap

from .models import SnapshotStatus, UsageSnapshot

# Layout constants in logical (pre-DPR) pixels.
DOT_DIAMETER = 8
DOT_TEXT_GAP = 4
PROVIDER_GAP = 12
SIDE_PADDING = 4
PIXMAP_HEIGHT = 22  # macOS menu-bar standard height

OK_COLORS = {
    "low": "#22c55e",
    "med": "#f59e0b",
    "high": "#ef4444",
}
NEUTRAL_COLOR = "#6b7280"
TEXT_LIGHT = "#f3f4f6"
TEXT_DARK = "#111827"


def _color_for_percent(percent: float | None) -> str:
    if percent is None:
        return NEUTRAL_COLOR
    if percent >= 90:
        return OK_COLORS["high"]
    if percent >= 75:
        return OK_COLORS["med"]
    return OK_COLORS["low"]


def _provider_max_percent(snapshot: UsageSnapshot | None) -> float | None:
    if snapshot is None or snapshot.status != SnapshotStatus.OK:
        return None
    best: float | None = None
    for metric in snapshot.metrics:
        if metric.percent_used is None:
            continue
        if best is None or metric.percent_used > best:
            best = metric.percent_used
    return best


def _format_label(percent: float | None) -> str:
    return "—" if percent is None else f"{percent:.0f}%"


def _menubar_font() -> QFont:
    font = QFont()
    font.setPixelSize(12)
    font.setWeight(QFont.Weight.DemiBold)
    return font


def measure_pixmap_width(
    snapshots: dict[str, UsageSnapshot],
    enabled_providers: Iterable[str],
) -> int:
    """Return the logical-pixel width the rendered pixmap will need."""
    providers = list(enabled_providers)
    if not providers:
        return SIDE_PADDING * 2 + DOT_DIAMETER  # show a single neutral dot
    fm = QFontMetrics(_menubar_font())
    width = SIDE_PADDING
    for i, provider in enumerate(providers):
        percent = _provider_max_percent(snapshots.get(provider))
        label = _format_label(percent)
        width += DOT_DIAMETER + DOT_TEXT_GAP + fm.horizontalAdvance(label)
        if i != len(providers) - 1:
            width += PROVIDER_GAP
    width += SIDE_PADDING
    return width


def render_menubar_pixmap(
    snapshots: dict[str, UsageSnapshot],
    enabled_providers: Iterable[str],
    *,
    device_pixel_ratio: float = 2.0,
    is_dark: bool = True,
) -> QPixmap:
    """Render the per-provider dot+percent strip for the macOS menu bar.

    ``is_dark`` chooses the text color — true on a dark menu bar, false on
    light. Dot colors stay full-saturation in both modes (Stats does the same).
    """
    providers = list(enabled_providers)
    width = measure_pixmap_width(snapshots, providers)
    height = PIXMAP_HEIGHT

    # Render at 2× (or whatever DPR is requested) for crisp Retina text.
    dpr = max(1.0, float(device_pixel_ratio))
    pix = QPixmap(int(width * dpr), int(height * dpr))
    pix.setDevicePixelRatio(dpr)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setFont(_menubar_font())

    text_color = QColor(TEXT_LIGHT if is_dark else TEXT_DARK)

    if not providers:
        # Empty state — single neutral dot, centered.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(NEUTRAL_COLOR))
        cy = height / 2 - DOT_DIAMETER / 2
        painter.drawEllipse(SIDE_PADDING, int(cy), DOT_DIAMETER, DOT_DIAMETER)
        painter.end()
        return pix

    fm = QFontMetrics(_menubar_font())
    # Vertical center of the dot vs. font baseline. Painter draws text by its
    # baseline, so position both relative to the pixmap midline.
    midline = height // 2
    dot_top = midline - DOT_DIAMETER // 2
    text_baseline = midline + fm.ascent() // 2 - 1  # nudge up 1px for optical center

    cursor = SIDE_PADDING
    for i, provider in enumerate(providers):
        percent = _provider_max_percent(snapshots.get(provider))
        color = QColor(_color_for_percent(percent))
        label = _format_label(percent)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(cursor, dot_top, DOT_DIAMETER, DOT_DIAMETER)
        cursor += DOT_DIAMETER + DOT_TEXT_GAP

        painter.setPen(text_color)
        painter.drawText(cursor, text_baseline, label)
        cursor += fm.horizontalAdvance(label)
        if i != len(providers) - 1:
            cursor += PROVIDER_GAP

    painter.end()
    return pix
