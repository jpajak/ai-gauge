"""macOS implementation of the platform seam.

- App data lives under ``~/Library/Application Support/ai-gauge``.
- Secrets (cookies + PAT) go through ``keyring`` (login Keychain). Keychain
  has no meaningful per-item size limit, so the DPAPI-style encrypted-file
  workaround used on Windows isn't needed.
- Auto-start is a LaunchAgent plist in ``~/Library/LaunchAgents``.
- The default UI is a menu-bar item (Stats-style); the floating widget is
  off by default but reachable through Settings.
"""
from __future__ import annotations

import logging
import os
import plistlib
import subprocess
from pathlib import Path

import keyring

from .base import APP_NAME, Platform, UIMode, autostart_command

log = logging.getLogger("aigauge.platforms.macos")

_LAUNCH_AGENT_LABEL = "org.aigauge.ai-gauge"
_KEYRING_SERVICE = "ai-gauge"


def _launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCH_AGENT_LABEL}.plist"


class MacOSPlatform(Platform):
    name = "macos"

    def _default_app_data_dir(self) -> Path:
        return Path.home() / "Library" / "Application Support" / APP_NAME

    def save_secret(self, name: str, value: str | None) -> None:
        if value is None:
            try:
                keyring.delete_password(_KEYRING_SERVICE, name)
            except keyring.errors.PasswordDeleteError:
                pass
            return
        keyring.set_password(_KEYRING_SERVICE, name, value)

    def load_secret(self, name: str) -> str | None:
        try:
            return keyring.get_password(_KEYRING_SERVICE, name)
        except keyring.errors.KeyringError:
            return None

    def set_autostart(self, enabled: bool) -> None:
        path = _launch_agent_path()
        if not enabled:
            if path.exists():
                # Unload first so launchd forgets the old definition; ignore
                # failure (it may not be loaded if the user never ran the app
                # after enabling the toggle).
                subprocess.run(
                    ["launchctl", "unload", str(path)],
                    check=False,
                    capture_output=True,
                )
                path.unlink(missing_ok=True)
            return

        path.parent.mkdir(parents=True, exist_ok=True)
        plist = {
            "Label": _LAUNCH_AGENT_LABEL,
            "ProgramArguments": autostart_command(),
            "RunAtLoad": True,
            # ProcessType=Interactive so the GUI app gets normal priority and
            # access to the user's session (menu bar, Keychain prompts).
            "ProcessType": "Interactive",
        }
        with path.open("wb") as f:
            plistlib.dump(plist, f)
        subprocess.run(
            ["launchctl", "load", str(path)],
            check=False,
            capture_output=True,
        )

    def get_autostart(self) -> bool:
        return _launch_agent_path().exists()

    def default_ui_mode(self) -> UIMode:
        return "menubar"
