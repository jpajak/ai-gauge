from PyQt6.QtWidgets import QPushButton

from usage_view import settings_dialog
from usage_view.config import Config
from usage_view.settings_dialog import SettingsDialog


def _button(dialog: SettingsDialog, name: str) -> QPushButton:
    button = dialog.findChild(QPushButton, name)
    assert button is not None
    return button


def test_sign_in_button_emits_sign_in_signal(qtbot):
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)

    with qtbot.waitSignal(dialog.sign_in_clicked) as signal:
        _button(dialog, "claude_signin_btn").click()

    assert signal.args == ["claude"]


def test_paste_cookie_button_emits_paste_cookie_signal(qtbot):
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)

    with qtbot.waitSignal(dialog.paste_cookie_clicked) as signal:
        _button(dialog, "codex_paste_cookie_btn").click()

    assert signal.args == ["codex"]


def test_claude_design_limit_is_optional(qtbot, monkeypatch):
    monkeypatch.setattr(settings_dialog, "set_start_with_windows", lambda enabled: None)
    config = Config()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)

    assert dialog.claude_design_cb.isChecked() is False
    dialog.claude_design_cb.setChecked(True)
    dialog.apply_to(config)

    assert config.providers.claude_design is True


def test_clear_saved_pat_checkbox_removes_existing_pat(qtbot, monkeypatch):
    calls = []
    monkeypatch.setattr(settings_dialog, "get_github_pat", lambda: None if calls else "saved")
    monkeypatch.setattr(settings_dialog, "set_github_pat", lambda value: calls.append(value))

    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)
    dialog.clear_pat_cb.setChecked(True)

    dialog._accept()  # noqa: SLF001

    assert calls == [None]
