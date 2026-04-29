from __future__ import annotations

import sys
from pathlib import Path

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = "ai-gauge"


def _startup_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'

    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    launcher = pythonw if pythonw.exists() else python
    return f'"{launcher}" -m aigauge'


def set_start_with_windows(enabled: bool) -> None:
    if sys.platform != "win32":
        return

    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        _RUN_KEY,
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        if enabled:
            winreg.SetValueEx(key, _RUN_VALUE, 0, winreg.REG_SZ, _startup_command())
        else:
            try:
                winreg.DeleteValue(key, _RUN_VALUE)
            except FileNotFoundError:
                pass
