from __future__ import annotations

import logging
from collections.abc import Callable

from PyQt6.QtCore import QPoint

from .menubar import status_items
from .models import UsageSnapshot

log = logging.getLogger("aigauge.macos_status_item")

try:  # pragma: no cover - exercised only on macOS with PyObjC installed.
    import objc
    from AppKit import (
        NSApp,
        NSColor,
        NSEventModifierFlagControl,
        NSEventMaskLeftMouseUp,
        NSEventMaskRightMouseUp,
        NSEventTypeLeftMouseUp,
        NSEventTypeRightMouseUp,
        NSFont,
        NSFontAttributeName,
        NSForegroundColorAttributeName,
        NSStatusBar,
        NSVariableStatusItemLength,
    )
    from Foundation import NSMutableAttributedString, NSObject, NSMakeRange
except Exception as exc:  # pragma: no cover - import availability varies by host.
    objc = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


def is_available() -> bool:
    return _IMPORT_ERROR is None


def unavailable_reason() -> str | None:
    return None if _IMPORT_ERROR is None else str(_IMPORT_ERROR)


def _ns_color(hex_color: str):
    value = hex_color.lstrip("#")
    red = int(value[0:2], 16) / 255
    green = int(value[2:4], 16) / 255
    blue = int(value[4:6], 16) / 255
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(red, green, blue, 1.0)


def _attributed_title(items: list[tuple[str, str, str]]):
    text_parts: list[str] = []
    dot_ranges: list[tuple[int, str]] = []
    cursor = 0
    for i, (label, value, color) in enumerate(items):
        if i:
            text_parts.append("  ")
            cursor += 2
        dot_ranges.append((cursor, color))
        part = f"● {label} {value}"
        text_parts.append(part)
        cursor += len(part)

    text = "".join(text_parts) if text_parts else "●"
    base_attrs = {
        NSFontAttributeName: NSFont.monospacedDigitSystemFontOfSize_weight_(11, 0.0),
        NSForegroundColorAttributeName: NSColor.labelColor(),
    }
    title = NSMutableAttributedString.alloc().initWithString_attributes_(
        text,
        base_attrs,
    )
    for index, color in dot_ranges:
        title.addAttributes_range_(
            {NSForegroundColorAttributeName: _ns_color(color)},
            NSMakeRange(index, 1),
        )
    return title


if objc is not None:  # pragma: no cover - needs PyObjC/AppKit.

    class _StatusTarget(NSObject):
        def initWithCallbacks_(self, callbacks):
            self = objc.super(_StatusTarget, self).init()
            if self is None:
                return None
            self._callbacks = callbacks
            return self

        def clicked_(self, sender):
            event = NSApp.currentEvent()
            is_context = False
            if event is not None:
                event_type = event.type()
                is_context = event_type == NSEventTypeRightMouseUp or (
                    event_type == NSEventTypeLeftMouseUp
                    and bool(event.modifierFlags() & NSEventModifierFlagControl)
                )
            if is_context:
                self._callbacks["context"]()
            else:
                self._callbacks["activate"]()


class NativeMacStatusItem:
    """Native variable-width macOS menu-bar item.

    This coexists with the Qt application loop but avoids ``QSystemTrayIcon``'s
    icon-size rendering constraints on macOS.
    """

    def __init__(
        self,
        *,
        on_activate: Callable[[], None],
        on_context: Callable[[], None],
    ):
        if not is_available():
            raise RuntimeError(f"PyObjC/AppKit unavailable: {unavailable_reason()}")
        self._status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength
        )
        self._target = _StatusTarget.alloc().initWithCallbacks_(
            {"activate": on_activate, "context": on_context}
        )
        button = self._status_item.button()
        button.setTarget_(self._target)
        button.setAction_("clicked:")
        button.sendActionOn_(NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp)

    def update(
        self,
        snapshots: dict[str, UsageSnapshot],
        enabled_providers: tuple[str, ...],
    ) -> None:
        items = status_items(snapshots, enabled_providers)
        button = self._status_item.button()
        button.setAttributedTitle_(_attributed_title(items))

    def set_tooltip(self, tooltip: str) -> None:
        self._status_item.button().setToolTip_(tooltip)

    def anchor_point(self) -> QPoint:
        button = self._status_item.button()
        window = button.window()
        if window is None:
            return QPoint(0, 22)
        frame = window.frame()
        screen = window.screen()
        screen_frame = screen.frame() if screen is not None else frame
        x = int(frame.origin.x + frame.size.width / 2)
        y = int(screen_frame.size.height - frame.origin.y)
        return QPoint(x, y)

    def close(self) -> None:
        NSStatusBar.systemStatusBar().removeStatusItem_(self._status_item)
