"""Linux implementation of the platform seam.

- App data follows XDG: ``$XDG_CONFIG_HOME/ai-gauge`` or ``~/.config/ai-gauge``.
- Secrets go through ``keyring`` — usually the GNOME/KDE Secret Service over
  D-Bus. On headless boxes without a keyring daemon, the call raises and the
  caller treats the secret as missing (we don't fall back to plaintext).
- Auto-start is a ``.desktop`` file under ``~/.config/autostart`` (XDG
  Autostart spec).
"""
from __future__ import annotations

import logging
import os
import shlex
from pathlib import Path

import keyring

from .base import APP_NAME, Platform, autostart_command

log = logging.getLogger("aigauge.platforms.linux")

_KEYRING_SERVICE = "ai-gauge"
_DESKTOP_FILENAME = f"{APP_NAME}.desktop"


def _xdg_config_home() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base)
    return Path.home() / ".config"


def _autostart_path() -> Path:
    return _xdg_config_home() / "autostart" / _DESKTOP_FILENAME


class LinuxPlatform(Platform):
    name = "linux"

    def _default_app_data_dir(self) -> Path:
        return _xdg_config_home() / APP_NAME

    def save_secret(self, name: str, value: str | None) -> None:
        if value is None:
            try:
                keyring.delete_password(_KEYRING_SERVICE, name)
            except keyring.errors.PasswordDeleteError:
                pass
            return
        try:
            keyring.set_password(_KEYRING_SERVICE, name, value)
        except keyring.errors.KeyringError as exc:
            log.warning("keyring set_password failed: %s", exc)
            raise

    def load_secret(self, name: str) -> str | None:
        try:
            return keyring.get_password(_KEYRING_SERVICE, name)
        except keyring.errors.KeyringError as exc:
            log.warning("keyring get_password failed: %s", exc)
            return None

    def set_autostart(self, enabled: bool) -> None:
        path = _autostart_path()
        if not enabled:
            path.unlink(missing_ok=True)
            return

        path.parent.mkdir(parents=True, exist_ok=True)
        exec_line = " ".join(shlex.quote(arg) for arg in autostart_command())
        path.write_text(
            "\n".join(
                [
                    "[Desktop Entry]",
                    "Type=Application",
                    "Name=AI Gauge",
                    "Comment=AI usage monitor for Claude / Codex / Copilot",
                    f"Exec={exec_line}",
                    "Terminal=false",
                    "X-GNOME-Autostart-enabled=true",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def get_autostart(self) -> bool:
        return _autostart_path().exists()
