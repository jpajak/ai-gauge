"""Auto-start at login.

Thin wrapper around the platform seam. The actual per-OS work (registry on
Windows, LaunchAgent plist on macOS, .desktop file on Linux) lives in
``aigauge.platforms``.
"""
from __future__ import annotations

from .platforms import autostart_command, get_platform


def _startup_command() -> str:
    # Stringified form of the autostart argv. Kept for legacy callers / tests
    # that want a human-readable command line. The platform impls themselves
    # use the argv directly (no quoting fragility).
    argv = autostart_command()
    if not argv:
        return ""
    head, *tail = argv
    return " ".join([f'"{head}"', *tail])


def set_start_at_login(enabled: bool) -> None:
    get_platform().set_autostart(enabled)


def get_start_at_login() -> bool:
    return get_platform().get_autostart()
