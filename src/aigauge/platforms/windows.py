"""Windows implementation of the platform seam.

- App data lives under ``%APPDATA%/ai-gauge``.
- Cookies are stored DPAPI-encrypted via the existing ``secret_storage`` module
  (Windows Credential Manager caps blobs at ~2.5KB, which Codex JWTs blow past).
- Auto-start uses ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .base import APP_NAME, Platform, autostart_command

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = APP_NAME


def _quote_command(argv: list[str]) -> str:
    # winreg REG_SZ values are passed verbatim to CreateProcess. Quote the
    # launcher path (which can hide a space, e.g. C:\Program Files\Python\...)
    # and leave argv tokens alone.
    if not argv:
        return ""
    head, *tail = argv
    return " ".join([f'"{head}"', *tail])


class WindowsPlatform(Platform):
    name = "windows"

    def _default_app_data_dir(self) -> Path:
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / APP_NAME
        return Path.home() / "AppData" / "Roaming" / APP_NAME

    def save_secret(self, name: str, value: str | None) -> None:
        from .. import secret_storage

        secret_storage.save_secret(name, value)

    def load_secret(self, name: str) -> str | None:
        from .. import secret_storage

        return secret_storage.load_secret(name)

    def set_autostart(self, enabled: bool) -> None:
        if sys.platform != "win32":
            return
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _RUN_KEY,
            0,
            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
        ) as key:
            if enabled:
                winreg.SetValueEx(
                    key, _RUN_VALUE, 0, winreg.REG_SZ, _quote_command(autostart_command())
                )
            else:
                try:
                    winreg.DeleteValue(key, _RUN_VALUE)
                except FileNotFoundError:
                    pass

    def get_autostart(self) -> bool:
        if sys.platform != "win32":
            return False
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_QUERY_VALUE
            ) as key:
                winreg.QueryValueEx(key, _RUN_VALUE)
                return True
        except FileNotFoundError:
            return False
