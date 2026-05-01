"""Per-OS behavior lives behind this seam.

Callers do::

    from .platforms import get_platform
    get_platform().app_data_dir()

instead of branching on ``sys.platform`` themselves.
"""
from __future__ import annotations

import sys
from functools import cache

from .base import APP_NAME, Platform, UIMode, autostart_command


@cache
def get_platform() -> Platform:
    if sys.platform == "win32":
        from .windows import WindowsPlatform

        return WindowsPlatform()
    if sys.platform == "darwin":
        from .macos import MacOSPlatform

        return MacOSPlatform()
    from .linux import LinuxPlatform

    return LinuxPlatform()


__all__ = ["APP_NAME", "Platform", "UIMode", "autostart_command", "get_platform"]
