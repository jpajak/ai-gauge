"""Windows implementation of the platform seam.

- App data lives under ``%APPDATA%/ai-gauge``.
- Cookies are stored DPAPI-encrypted via the existing ``secret_storage`` module
  (Windows Credential Manager caps blobs at ~2.5KB, which Codex JWTs blow past).
- Auto-start uses a named Task Scheduler entry instead of a Run-key value.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from .base import APP_NAME, Platform, autostart_command

log = logging.getLogger("aigauge.platforms.windows")

TASK_NAME = "AI Gauge"
TASK_AUTHOR = "AloeDesk"
TASK_DESCRIPTION = "Starts AI Gauge when the user signs in."
_TASK_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"


def _schtasks_path() -> str:
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows"
    candidate = Path(windir) / "System32" / "schtasks.exe"
    if candidate.exists():
        return str(candidate)
    return "schtasks.exe"


def _current_user_id() -> str:
    """Best-effort ``DOMAIN\\User`` for the current interactive user.

    Used to scope the logon task to this user so registering it does not need
    admin. Returns "" if it can't be determined, in which case the task is left
    unscoped (and creation may require elevation).
    """
    user = (os.environ.get("USERNAME") or "").strip()
    if not user:
        try:
            import getpass

            user = getpass.getuser()
        except Exception:  # noqa: BLE001
            return ""
    domain = (os.environ.get("USERDOMAIN") or "").strip()
    if domain and "\\" not in user and "@" not in user:
        return f"{domain}\\{user}"
    return user


def _task_xml(argv: list[str]) -> str:
    if not argv:
        raise ValueError("autostart command must not be empty")

    ET.register_namespace("", _TASK_NS)
    task = ET.Element(f"{{{_TASK_NS}}}Task", {"version": "1.4"})
    registration = ET.SubElement(task, f"{{{_TASK_NS}}}RegistrationInfo")
    ET.SubElement(registration, f"{{{_TASK_NS}}}Author").text = TASK_AUTHOR
    ET.SubElement(registration, f"{{{_TASK_NS}}}Description").text = TASK_DESCRIPTION

    user_id = _current_user_id()

    triggers = ET.SubElement(task, f"{{{_TASK_NS}}}Triggers")
    trigger = ET.SubElement(triggers, f"{{{_TASK_NS}}}LogonTrigger")
    ET.SubElement(trigger, f"{{{_TASK_NS}}}Enabled").text = "true"
    # Scope the trigger to this user. Without a UserId it fires "when any user
    # logs on", which requires admin to register and otherwise fails with
    # "Access is denied". (UserId comes after Enabled per the Task schema.)
    if user_id:
        ET.SubElement(trigger, f"{{{_TASK_NS}}}UserId").text = user_id

    principals = ET.SubElement(task, f"{{{_TASK_NS}}}Principals")
    principal = ET.SubElement(principals, f"{{{_TASK_NS}}}Principal", {"id": "Author"})
    # Run as the current interactive user. UserId precedes LogonType per the
    # schema; InteractiveToken runs only while that user is logged on, so no
    # stored password or elevation is needed.
    if user_id:
        ET.SubElement(principal, f"{{{_TASK_NS}}}UserId").text = user_id
    ET.SubElement(principal, f"{{{_TASK_NS}}}LogonType").text = "InteractiveToken"
    ET.SubElement(principal, f"{{{_TASK_NS}}}RunLevel").text = "LeastPrivilege"

    settings = ET.SubElement(task, f"{{{_TASK_NS}}}Settings")
    ET.SubElement(settings, f"{{{_TASK_NS}}}MultipleInstancesPolicy").text = "IgnoreNew"
    ET.SubElement(settings, f"{{{_TASK_NS}}}DisallowStartIfOnBatteries").text = "false"
    ET.SubElement(settings, f"{{{_TASK_NS}}}StopIfGoingOnBatteries").text = "false"
    ET.SubElement(settings, f"{{{_TASK_NS}}}AllowHardTerminate").text = "true"
    ET.SubElement(settings, f"{{{_TASK_NS}}}StartWhenAvailable").text = "false"
    ET.SubElement(settings, f"{{{_TASK_NS}}}RunOnlyIfNetworkAvailable").text = "false"
    ET.SubElement(settings, f"{{{_TASK_NS}}}Enabled").text = "true"
    ET.SubElement(settings, f"{{{_TASK_NS}}}Hidden").text = "false"
    ET.SubElement(settings, f"{{{_TASK_NS}}}RunOnlyIfIdle").text = "false"
    ET.SubElement(settings, f"{{{_TASK_NS}}}WakeToRun").text = "false"
    ET.SubElement(settings, f"{{{_TASK_NS}}}ExecutionTimeLimit").text = "PT0S"

    actions = ET.SubElement(task, f"{{{_TASK_NS}}}Actions", {"Context": "Author"})
    exec_action = ET.SubElement(actions, f"{{{_TASK_NS}}}Exec")
    ET.SubElement(exec_action, f"{{{_TASK_NS}}}Command").text = argv[0]
    if len(argv) > 1:
        ET.SubElement(exec_action, f"{{{_TASK_NS}}}Arguments").text = subprocess.list2cmdline(
            argv[1:]
        )

    body = ET.tostring(task, encoding="unicode")
    # schtasks /Create /XML requires the file to be UTF-16, and the declaration's
    # encoding must match the file's actual byte encoding or schtasks rejects it
    # with "The task XML is malformed ... unable to switch the encoding". The
    # caller writes this string to a UTF-16 file (see set_autostart).
    return f'<?xml version="1.0" encoding="UTF-16"?>\r\n{body}'


def _run_schtasks(args: list[str]) -> subprocess.CompletedProcess[str]:
    command = [_schtasks_path(), *args]
    log.info("updating Windows startup task: %s", " ".join(command))
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


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
        if enabled:
            # UTF-16 to satisfy schtasks /XML (matches the declaration emitted
            # by _task_xml); Python's utf-16 codec writes a little-endian BOM.
            task_file = tempfile.NamedTemporaryFile(
                mode="w", suffix=".xml", encoding="utf-16", delete=False
            )
            try:
                with task_file:
                    task_file.write(_task_xml(autostart_command()))
                result = _run_schtasks(
                    ["/Create", "/TN", TASK_NAME, "/XML", task_file.name, "/F"]
                )
            finally:
                try:
                    Path(task_file.name).unlink()
                except FileNotFoundError:
                    pass
        else:
            if not self.get_autostart():
                return
            result = _run_schtasks(["/Delete", "/TN", TASK_NAME, "/F"])

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"could not update Windows startup task: {detail}")

    def get_autostart(self) -> bool:
        if sys.platform != "win32":
            return False
        result = _run_schtasks(["/Query", "/TN", TASK_NAME])
        return result.returncode == 0

