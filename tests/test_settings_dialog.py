from PyQt6.QtWidgets import QPushButton

from aigauge import settings_dialog
from aigauge.config import Config
from aigauge.settings_dialog import SettingsDialog


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


def test_clear_sign_in_button_emits_account_signal(qtbot):
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)

    with qtbot.waitSignal(dialog.clear_sign_in_clicked) as signal:
        _button(dialog, "claude_clear_signin_btn").click()

    assert signal.args == ["claude"]


def test_claude_open_usage_button_launches_browser(qtbot, monkeypatch):
    opened = []
    monkeypatch.setattr(
        settings_dialog, "_open_in_browser", lambda url: opened.append(url)
    )

    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)
    _button(dialog, "claude_open_usage_btn").click()

    assert opened == [settings_dialog.CLAUDE_USAGE_URL]


def test_codex_open_usage_button_launches_browser(qtbot, monkeypatch):
    opened = []
    monkeypatch.setattr(
        settings_dialog, "_open_in_browser", lambda url: opened.append(url)
    )

    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)
    _button(dialog, "codex_open_usage_btn").click()

    assert opened == [settings_dialog.CODEX_USAGE_URL]




def test_opencode_go_sign_in_button_emits_sign_in_signal(qtbot):
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)

    with qtbot.waitSignal(dialog.sign_in_clicked) as signal:
        _button(dialog, "opencode_go_signin_btn").click()

    assert signal.args == ["opencode_go"]


def test_opencode_go_paste_cookie_button_emits_signal(qtbot):
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)

    with qtbot.waitSignal(dialog.paste_cookie_clicked) as signal:
        _button(dialog, "opencode_go_paste_cookie_btn").click()

    assert signal.args == ["opencode_go"]


def test_opencode_go_clear_sign_in_button_emits_signal(qtbot):
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)

    with qtbot.waitSignal(dialog.clear_sign_in_clicked) as signal:
        _button(dialog, "opencode_go_clear_signin_btn").click()

    assert signal.args == ["opencode_go"]


def test_opencode_go_open_usage_button_launches_configured_url(qtbot, monkeypatch):
    opened = []
    monkeypatch.setattr(
        settings_dialog, "_open_in_browser", lambda url: opened.append(url)
    )

    config = Config()
    config.opencode_go.usage_url = "https://opencode.ai/workspace/test/go"
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    _button(dialog, "opencode_go_open_usage_btn").click()

    assert opened == ["https://opencode.ai/workspace/test/go"]


def test_opencode_go_settings_apply(qtbot, monkeypatch):
    monkeypatch.setattr(settings_dialog, "set_start_at_login", lambda enabled: None)
    config = Config()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)

    dialog.opencode_go_cb.setChecked(True)
    dialog.opencode_go_url.setText("https://opencode.ai/workspace/custom/go")
    dialog.apply_to(config)

    assert config.providers.opencode_go is True
    assert config.opencode_go.usage_url == "https://opencode.ai/workspace/custom/go"

def test_add_codex_account_creates_named_secondary_row(qtbot, monkeypatch):
    monkeypatch.setattr(settings_dialog, "set_start_at_login", lambda enabled: None)
    config = Config()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)

    dialog._add_browser_account("codex")  # noqa: SLF001
    dialog.apply_to(config)

    codex_accounts = [a for a in config.browser_accounts if a.kind == "codex"]
    assert len(codex_accounts) == 2
    assert codex_accounts[1].name == "Account 2"
    assert codex_accounts[1].enabled is True


def test_remove_secondary_account_clears_session(qtbot, monkeypatch):
    removed = []
    monkeypatch.setattr(settings_dialog, "set_start_at_login", lambda enabled: None)
    monkeypatch.setattr(
        settings_dialog,
        "clear_browser_session",
        lambda account_id: removed.append(account_id),
    )
    config = Config()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)

    dialog._add_browser_account("claude")  # noqa: SLF001
    account_id = dialog._browser_accounts[-1].id  # noqa: SLF001
    dialog._remove_browser_account(account_id)  # noqa: SLF001
    dialog.apply_to(config)

    assert removed == [account_id]

def test_fade_when_inactive_setting_applies(qtbot, monkeypatch):
    monkeypatch.setattr(settings_dialog, "set_start_at_login", lambda enabled: None)
    config = Config()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)

    assert not dialog.fade_when_inactive_cb.isChecked()
    assert not dialog.opacity_slider.isEnabled()

    dialog.fade_when_inactive_cb.setChecked(True)
    dialog.opacity_slider.setValue(62)
    dialog.apply_to(config)

    assert config.window.fade_when_inactive is True
    assert config.window.opacity == 0.62

def test_clear_saved_pat_checkbox_removes_existing_pat(qtbot, monkeypatch):
    calls = []
    monkeypatch.setattr(
        settings_dialog, "get_github_pat", lambda: None if calls else "saved"
    )
    monkeypatch.setattr(
        settings_dialog, "set_github_pat", lambda value: calls.append(value)
    )

    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)
    dialog.clear_pat_cb.setChecked(True)

    dialog._accept()  # noqa: SLF001

    assert calls == [None]
