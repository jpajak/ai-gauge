"""macOS menu-bar rendering.

Qt's ``QSystemTrayIcon`` is icon-first on macOS. Wide text pixmaps get squeezed
into the menu-bar icon slot on some builds, which makes percent labels
unreadable. Render a fixed-size provider-dot icon instead and keep readable
numbers in the popover.

The pixmap is rendered at ``device_pixel_ratio`` ×, then has its DPR set so
Qt downscales correctly on Retina displays.
"""
from __future__ import annotations

from typing import Iterable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap

from .config import Config
from .gauge import (
    color_for_percent,
    provider_max_percent,
    thresholds_for_provider,
)
from .models import SnapshotStatus, UsageSnapshot

# Layout constants in logical (pre-DPR) pixels.
DOT_DIAMETER = 6
PROVIDER_GAP = 1
PIXMAP_HEIGHT = 22  # macOS menu-bar standard height
PIXMAP_WIDTH = 22
SIDE_PADDING = (PIXMAP_WIDTH - DOT_DIAMETER) // 2
PROVIDER_LABELS = {
    "claude": "Cl",
    "codex": "Cx",
    "opencode_go": "Go",
    "copilot": "Cp",
    "openrouter": "OR",
}

OK_COLORS = {
    "low": "#22c55e",
    "med": "#f59e0b",
    "high": "#ef4444",
}
NEUTRAL_COLOR = "#6b7280"
SETUP_COLOR = "#38bdf8"
ERROR_COLOR = OK_COLORS["high"]


def _provider_color(
    config: Config | None,
    provider: str,
    snapshot: UsageSnapshot | None,
) -> str:
    if snapshot is None:
        return NEUTRAL_COLOR
    if snapshot.status == SnapshotStatus.AUTH_REQUIRED:
        return SETUP_COLOR
    if snapshot.status == SnapshotStatus.ERROR:
        return ERROR_COLOR
    return color_for_percent(
        provider_max_percent(snapshot),
        thresholds_for_provider(config, provider),
    )


def _provider_value(snapshot: UsageSnapshot | None) -> str:
    if snapshot is None:
        return "..."
    if snapshot.status in (SnapshotStatus.AUTH_REQUIRED, SnapshotStatus.ERROR):
        return "!"
    percent = provider_max_percent(snapshot)
    return "..." if percent is None else f"{percent:.0f}%"


def status_items(
    snapshots: dict[str, UsageSnapshot],
    enabled_providers: Iterable[str],
    config: Config | None = None,
) -> list[tuple[str, str, str]]:
    """Return ``(provider_label, value, color)`` items for native menu bars."""
    return [
        (
            PROVIDER_LABELS.get(provider, provider[:2].title()),
            _provider_value(snapshots.get(provider)),
            _provider_color(config, provider, snapshots.get(provider)),
        )
        for provider in enabled_providers
    ]


def _dot_centers(count: int) -> list[float]:
    count = max(1, count)
    total_width = count * DOT_DIAMETER + (count - 1) * PROVIDER_GAP
    start = (PIXMAP_WIDTH - total_width) / 2
    return [
        start + DOT_DIAMETER / 2 + i * (DOT_DIAMETER + PROVIDER_GAP)
        for i in range(count)
    ]


def measure_pixmap_width(
    snapshots: dict[str, UsageSnapshot],
    enabled_providers: Iterable[str],
) -> int:
    """Return the logical-pixel width the rendered pixmap will need."""
    return PIXMAP_WIDTH


def render_menubar_pixmap(
    snapshots: dict[str, UsageSnapshot],
    enabled_providers: Iterable[str],
    *,
    config: Config | None = None,
    device_pixel_ratio: float = 2.0,
    is_dark: bool = True,
) -> QPixmap:
    """Render a compact per-provider status icon for the macOS menu bar.

    ``is_dark`` is accepted for API compatibility with earlier text rendering.
    Dot colors stay full-saturation in both modes.
    """
    providers = list(enabled_providers)
    width = PIXMAP_WIDTH
    height = PIXMAP_HEIGHT

    # Render at 2× (or whatever DPR is requested) for crisp Retina text.
    dpr = max(1.0, float(device_pixel_ratio))
    pix = QPixmap(int(width * dpr), int(height * dpr))
    pix.setDevicePixelRatio(dpr)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    dot_count = len(providers) if providers else 1
    dot_top = height / 2 - DOT_DIAMETER / 2
    colors = (
        [
            _provider_color(config, provider, snapshots.get(provider))
            for provider in providers
        ]
        if providers
        else [NEUTRAL_COLOR]
    )
    for center_x, color_name in zip(_dot_centers(dot_count), colors, strict=True):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color_name))
        painter.drawEllipse(
            int(round(center_x - DOT_DIAMETER / 2)),
            int(round(dot_top)),
            DOT_DIAMETER,
            DOT_DIAMETER,
        )

    painter.end()
    return pix
