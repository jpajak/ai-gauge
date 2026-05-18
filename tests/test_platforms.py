"""Smoke tests for the platform seam.

These instantiate each Platform impl directly (not through ``get_platform()``)
so the tests run anywhere — Windows CI exercises ``WindowsPlatform``, but the
mac/Linux impls also get coverage by injecting a fake keyring backend.
"""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from aigauge.platforms import autostart_command, get_platform
from aigauge.platforms.linux import LinuxPlatform
from aigauge.platforms.macos import MacOSPlatform


class _FakeKeyring:
    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, name):
        return self.store.get((service, name))

    def set_password(self, service, name, value):
        self.store[(service, name)] = value

    def delete_password(self, service, name):
        try:
            del self.store[(service, name)]
        except KeyError:
            import keyring

            raise keyring.errors.PasswordDeleteError("not found")


def test_get_platform_returns_an_instance_with_a_name():
    p = get_platform()
    assert p.name in ("windows", "macos", "linux")


def test_get_platform_app_data_dir_honors_env_override(tmp_path, monkeypatch):
    # The conftest-wide fixture already sets APPDATA, but assert the contract
    # explicitly here so a future refactor can't quietly break test isolation.
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert str(tmp_path) in str(get_platform().app_data_dir())


def test_autostart_command_includes_module_or_executable():
    argv = autostart_command()
    assert argv  # non-empty
    if argv[0].endswith(("python", "python.exe", "pythonw.exe")):
        assert "-m" in argv
        assert "aigauge" in argv


@pytest.mark.parametrize("PlatformCls", [MacOSPlatform, LinuxPlatform])
def test_keyring_backed_platform_round_trips_secret(PlatformCls, monkeypatch):
    fake = _FakeKeyring()
    # Patch the keyring module that the platform impl imports at top level.
    module_name = f"aigauge.platforms.{PlatformCls.__module__.rsplit('.', 1)[-1]}"
    monkeypatch.setattr(f"{module_name}.keyring.get_password", fake.get_password)
    monkeypatch.setattr(f"{module_name}.keyring.set_password", fake.set_password)
    monkeypatch.setattr(f"{module_name}.keyring.delete_password", fake.delete_password)

    p = PlatformCls()
    assert p.load_secret("my-key") is None
    p.save_secret("my-key", "value-1")
    assert p.load_secret("my-key") == "value-1"
    p.save_secret("my-key", "value-2")
    assert p.load_secret("my-key") == "value-2"
    p.save_secret("my-key", None)
    assert p.load_secret("my-key") is None


def test_macos_default_ui_mode_is_menubar():
    assert MacOSPlatform().default_ui_mode() == "menubar"


def test_linux_default_ui_mode_is_floating_widget():
    assert LinuxPlatform().default_ui_mode() == "floating_widget"


def test_linux_autostart_writes_desktop_file(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    p = LinuxPlatform()
    assert p.get_autostart() is False
    p.set_autostart(True)
    desktop = tmp_path / "autostart" / "ai-gauge.desktop"
    assert desktop.exists()
    text = desktop.read_text(encoding="utf-8")
    assert "[Desktop Entry]" in text
    assert "Exec=" in text
    assert p.get_autostart() is True
    p.set_autostart(False)
    assert p.get_autostart() is False
    assert not desktop.exists()


def test_macos_autostart_writes_plist(tmp_path, monkeypatch):
    # Redirect HOME so the LaunchAgent doesn't land in the real ~/Library.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    # Stub launchctl so the test doesn't call the real binary on a CI runner
    # without macOS.
    with patch("aigauge.platforms.macos.subprocess.run") as run:
        p = MacOSPlatform()
        assert p.get_autostart() is False
        p.set_autostart(True)
        plist = tmp_path / "Library" / "LaunchAgents" / "org.aigauge.ai-gauge.plist"
        assert plist.exists()
        assert p.get_autostart() is True
        p.set_autostart(False)
        assert not plist.exists()
        # launchctl was invoked at least once for load and once for unload.
        commands = [call.args[0] for call in run.call_args_list]
        assert any("load" in cmd for cmd in commands)
        assert any("unload" in cmd for cmd in commands)
