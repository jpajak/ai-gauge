"""Platform abstraction seam.

The app talks to one of three concrete subclasses depending on the host OS.
This module owns:
- the abstract :class:`Platform` interface,
- shared helpers that don't differ by OS (e.g. building the auto-start command
  from ``sys.executable``),
- the ``APP_DATA_DIR_OVERRIDE_ENV`` env-var convention used by the test suite
  to redirect storage away from the user's real config folder.

Concrete impls live next to this module — see windows.py / macos.py / linux.py.
"""
from __future__ import annotations

import os
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Literal

APP_NAME = "ai-gauge"

# Tests (and portable installs) can redirect every platform's app_data_dir by
# setting this env var. We honour APPDATA on every platform — not just Windows
# — so the existing pytest fixture in conftest.py keeps working unchanged.
APP_DATA_DIR_OVERRIDE_ENV = "APPDATA"

UIMode = Literal["floating_widget", "menubar"]


def autostart_command() -> list[str]:
    """Argv used to relaunch the app at login.

    Frozen builds (PyInstaller) reuse ``sys.executable`` directly. Source
    installs prefer ``pythonw`` on Windows so no console window flashes; on
    macOS/Linux there is no separate ``pythonw`` — ``python -m aigauge`` is
    used directly.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable]
    python = Path(sys.executable)
    if sys.platform == "win32":
        pythonw = python.with_name("pythonw.exe")
        launcher = pythonw if pythonw.exists() else python
    else:
        launcher = python
    return [str(launcher), "-m", "aigauge"]


class Platform(ABC):
    """Per-OS implementation of the small set of host-specific behaviors."""

    name: str

    # ----- Filesystem -----

    @abstractmethod
    def _default_app_data_dir(self) -> Path:
        """Per-OS default location for config / logs / encrypted blobs."""

    def app_data_dir(self) -> Path:
        override = os.environ.get(APP_DATA_DIR_OVERRIDE_ENV)
        if override:
            return Path(override) / APP_NAME
        return self._default_app_data_dir()

    # ----- Secret storage (cookies; PAT goes through `keyring` directly) -----

    @abstractmethod
    def save_secret(self, name: str, value: str | None) -> None:
        """Persist ``value`` under ``name``. ``None`` deletes."""

    @abstractmethod
    def load_secret(self, name: str) -> str | None:
        """Return the value stored under ``name``, or ``None`` if absent."""

    # ----- Autostart at login -----

    @abstractmethod
    def set_autostart(self, enabled: bool) -> None: ...

    @abstractmethod
    def get_autostart(self) -> bool: ...

    # ----- UI hints -----

    def default_ui_mode(self) -> UIMode:
        """Whether the floating panel or menu-bar item is the primary UI."""
        return "floating_widget"
