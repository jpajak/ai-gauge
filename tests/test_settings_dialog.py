from PyQt6.QtCore import QEvent, QMimeData, QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QColor, QDragMoveEvent, QDropEvent, QImage, QMouseEvent
from PyQt6.QtWidgets import QApplication, QGroupBox, QLabel, QPushButton

from aigauge import settings_dialog
from aigauge.providers import registry
from aigauge.config import BrowserAccount, ColorThresholds, Config
from aigauge.settings_dialog import SettingsDialog


def _button(dialog: SettingsDialog, name: str) -> QPushButton:
    button = dialog.findChild(QPushButton, name)
    assert button is not None
    return button


def test_brand_header_showcases_app_identity(qtbot):
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)

    assert not dialog.brand_icon.pixmap().isNull()
    assert dialog.brand_icon.width() == 56
    assert dialog.brand_icon.height() == 56
    assert dialog.brand_title.text() == "AI Gauge"
    assert "usage at a glance" in dialog.brand_subtitle.text()
    assert settings_dialog.__version__ in dialog.brand_subtitle.text()


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


def test_paste_cookie_button_follows_the_spec_flag(qtbot, monkeypatch):
    spec = registry.ProviderSpec(
        id="no_paste",
        display_name="No Paste",
        settings_label="No Paste",
        short_label="Np",
        auth="browser_session",
        multi_account=True,
        default_enabled=False,
        default_order=99,
        make=lambda config, account, parent: None,
        paste_cookie=False,
    )
    monkeypatch.setitem(registry._SPECS_BY_ID, "no_paste", spec)  # noqa: SLF001

    row = settings_dialog._BrowserAccountRow(  # noqa: SLF001
        BrowserAccount(id="no_paste", kind="no_paste"), removable=False
    )
    qtbot.addWidget(row)

    assert row.findChild(QPushButton, "no_paste_paste_cookie_btn") is None
    # The other browser-session buttons are unaffected.
    assert row.findChild(QPushButton, "no_paste_signin_btn") is not None
    assert row.findChild(QPushButton, "no_paste_clear_signin_btn") is not None


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


def test_mcp_integration_defaults_off_and_gates_policies(qtbot, monkeypatch):
    monkeypatch.setattr(settings_dialog, "set_start_at_login", lambda enabled: None)
    config = Config()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)

    assert not dialog.mcp_enabled_cb.isChecked()
    enabled, threshold = dialog.mcp_policy_controls["codex"]
    assert not enabled.isEnabled()
    assert not threshold.isEnabled()

    dialog.mcp_enabled_cb.setChecked(True)
    enabled.setChecked(True)
    dialog.apply_to(config)

    assert config.mcp_enabled is True
    assert config.mcp_pause_policies["codex"] == 90




def test_opencode_go_uses_masked_api_key_field_instead_of_browser_buttons(
    qtbot, monkeypatch
):
    monkeypatch.setattr(
        settings_dialog, "get_account_api_key", lambda kind, account_id: None
    )
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)

    row = next(
        row
        for row in dialog._browser_account_rows  # noqa: SLF001
        if row.account_id == "opencode_go"
    )
    assert row.api_key_edit is not None
    assert row.api_key_edit.echoMode() == row.api_key_edit.EchoMode.Password
    assert dialog.findChild(QPushButton, "opencode_go_signin_btn") is None
    assert dialog.findChild(QPushButton, "opencode_go_paste_cookie_btn") is None


def test_opencode_go_accept_saves_key_to_account_secret(qtbot, monkeypatch):
    secrets = {}
    monkeypatch.setattr(
        settings_dialog,
        "get_account_api_key",
        lambda kind, account_id: secrets.get(account_id),
    )
    monkeypatch.setattr(
        settings_dialog,
        "set_account_api_key",
        lambda kind, account_id, value: (
            secrets.__setitem__(account_id, value)
            if value
            else secrets.pop(account_id, None)
        ),
    )
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)
    row = next(
        row
        for row in dialog._browser_account_rows  # noqa: SLF001
        if row.account_id == "opencode_go"
    )
    row.api_key_edit.setText("oc_sk_test")

    dialog._accept()  # noqa: SLF001

    assert secrets == {"opencode_go": "oc_sk_test"}


def test_opencode_go_existing_key_can_be_cleared(qtbot, monkeypatch):
    secrets = {"opencode_go": "saved-key"}
    monkeypatch.setattr(
        settings_dialog,
        "get_account_api_key",
        lambda kind, account_id: secrets.get(account_id),
    )
    monkeypatch.setattr(
        settings_dialog,
        "set_account_api_key",
        lambda kind, account_id, value: (
            secrets.__setitem__(account_id, value)
            if value
            else secrets.pop(account_id, None)
        ),
    )
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)
    row = next(
        row
        for row in dialog._browser_account_rows  # noqa: SLF001
        if row.account_id == "opencode_go"
    )
    assert row.clear_api_key_cb is not None
    assert not row.clear_api_key_cb.isHidden()
    row.clear_api_key_cb.setChecked(True)

    dialog._accept()  # noqa: SLF001

    assert secrets == {}


def test_opencode_go_key_draft_survives_adding_an_account(qtbot, monkeypatch):
    monkeypatch.setattr(
        settings_dialog, "get_account_api_key", lambda kind, account_id: None
    )
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)
    first_row = next(
        row
        for row in dialog._browser_account_rows  # noqa: SLF001
        if row.account_id == "opencode_go"
    )
    first_row.api_key_edit.setText("oc_sk_unsaved")

    dialog._add_browser_account("opencode_go")  # noqa: SLF001

    rebuilt_first_row = next(
        row
        for row in dialog._browser_account_rows  # noqa: SLF001
        if row.account_id == "opencode_go"
    )
    assert rebuilt_first_row.api_key_edit.text() == "oc_sk_unsaved"


def test_removing_opencode_account_clears_its_key_on_accept(qtbot, monkeypatch):
    secrets = {"opencode_go": "saved-key"}
    monkeypatch.setattr(
        settings_dialog,
        "get_account_api_key",
        lambda kind, account_id: secrets.get(account_id),
    )
    monkeypatch.setattr(
        settings_dialog,
        "set_account_api_key",
        lambda kind, account_id, value: (
            secrets.__setitem__(account_id, value)
            if value
            else secrets.pop(account_id, None)
        ),
    )
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)

    dialog._remove_browser_account("opencode_go")  # noqa: SLF001
    dialog._accept()  # noqa: SLF001

    assert secrets == {}


def test_opencode_go_settings_apply_multiple_accounts(qtbot, monkeypatch):
    monkeypatch.setattr(settings_dialog, "set_start_at_login", lambda enabled: None)
    monkeypatch.setattr(
        settings_dialog, "get_account_api_key", lambda kind, account_id: None
    )
    config = Config()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)

    dialog.opencode_go_cb.setChecked(True)
    first_row = next(
        row
        for row in dialog._browser_account_rows  # noqa: SLF001
        if row.account_id == "opencode_go"
    )
    dialog._add_browser_account("opencode_go")  # noqa: SLF001
    second_row = next(
        row
        for row in dialog._browser_account_rows  # noqa: SLF001
        if row.account_id.startswith("opencode_go-")
    )
    second_row.name_edit.setText("Work")
    second_row.colors = ColorThresholds(
        green_max=20,
        yellow_max=50,
        orange_max=80,
    )
    dialog.apply_to(config)

    accounts = [a for a in config.browser_accounts if a.kind == "opencode_go"]
    assert config.providers.opencode_go is True
    assert [a.name for a in accounts] == [None, "Work"]
    assert accounts[1].colors.green_max == 20

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


def test_color_thresholds_apply_per_account_and_copilot(qtbot, monkeypatch):
    monkeypatch.setattr(settings_dialog, "set_start_at_login", lambda enabled: None)
    config = Config()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)

    codex_row = next(
        row for row in dialog._browser_account_rows if row.account_id == "codex"  # noqa: SLF001
    )
    codex_row.colors = ColorThresholds(
        green_max=20, yellow_max=50, orange_max=80
    )
    dialog.copilot_colors.colors = ColorThresholds(
        green_max=25, yellow_max=55, orange_max=85
    )
    dialog.apply_to(config)

    codex = next(a for a in config.browser_accounts if a.id == "codex")
    assert codex.colors == ColorThresholds(
        green_max=20, yellow_max=50, orange_max=80
    )
    assert config.copilot.colors == ColorThresholds(
        green_max=25, yellow_max=55, orange_max=85
    )


def test_color_threshold_dialog_resolves_visible_arrow_assets(qtbot):
    dialog = settings_dialog._ColorThresholdDialog(ColorThresholds())  # noqa: SLF001
    qtbot.addWidget(dialog)

    stylesheet = dialog.styleSheet()
    assert "__UP_ARROW__" not in stylesheet
    assert "__DOWN_ARROW__" not in stylesheet
    assert "QSpinBox::up-arrow" in stylesheet
    assert "QSpinBox::down-arrow" in stylesheet


def test_color_editor_updates_live_ranges_and_all_four_colors(qtbot):
    editor = settings_dialog._ColorThresholdEditor(ColorThresholds())  # noqa: SLF001
    qtbot.addWidget(editor)

    editor.green.setValue(25)
    editor.yellow.setValue(55)
    editor.orange.setValue(85)
    editor._colors["red"] = "#123456"  # noqa: SLF001
    editor._refresh_band_buttons()  # noqa: SLF001

    assert editor._color_buttons["green"].text() == "Green · 0–25%"  # noqa: SLF001
    assert editor._color_buttons["yellow"].text() == "Yellow · 26–55%"  # noqa: SLF001
    assert editor._color_buttons["orange"].text() == "Orange · 56–85%"  # noqa: SLF001
    assert editor._color_buttons["red"].text() == "Red · 86%+"  # noqa: SLF001
    assert editor.value().red_color == "#123456"

    editor.reset_defaults()
    assert editor.value() == ColorThresholds()


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


def test_remove_original_codex_account_and_add_one_back(qtbot, monkeypatch):
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

    dialog._remove_browser_account("codex")  # noqa: SLF001
    dialog.apply_to(config)

    assert [a for a in config.browser_accounts if a.kind == "codex"] == []
    assert removed == ["codex"]

    replacement = SettingsDialog(config)
    qtbot.addWidget(replacement)
    replacement._add_browser_account("codex")  # noqa: SLF001
    replacement.apply_to(config)

    codex_accounts = [a for a in config.browser_accounts if a.kind == "codex"]
    assert len(codex_accounts) == 1
    assert codex_accounts[0].name == "Account 1"

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


def test_square_corners_setting_applies(qtbot, monkeypatch):
    monkeypatch.setattr(settings_dialog, "set_start_at_login", lambda enabled: None)
    config = Config()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)

    assert not dialog.square_corners_cb.isChecked()

    dialog.square_corners_cb.setChecked(True)
    dialog.apply_to(config)

    assert config.window.square_corners is True


def test_sign_in_browser_setting_applies(qtbot, monkeypatch):
    monkeypatch.setattr(settings_dialog, "set_start_at_login", lambda enabled: None)
    config = Config()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)

    dialog.set_sign_in_browser("brave")
    dialog.apply_to(config)

    assert config.sign_in_browser == "brave"

def test_fable_toggle_is_per_claude_account(qtbot, monkeypatch):
    monkeypatch.setattr(settings_dialog, "set_start_at_login", lambda enabled: None)
    config = Config(
        browser_accounts=[
            BrowserAccount(id="claude", kind="claude"),
            BrowserAccount(id="claude-work", kind="claude", name="Work"),
            BrowserAccount(id="codex", kind="codex"),
        ]
    )
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)

    rows = {row.account_id: row for row in dialog._browser_account_rows}  # noqa: SLF001
    assert rows["codex"].fable_cb is None, "only Claude accounts offer the toggle"
    assert rows["claude"].fable_cb.isChecked(), "on by default"

    rows["claude-work"].fable_cb.setChecked(False)
    dialog.apply_to(config)

    saved = {a.id: a.show_fable for a in config.browser_accounts}
    assert saved["claude"] is True
    assert saved["claude-work"] is False, "each account is independent"


def test_fable_toggle_reflects_saved_account_state(qtbot, monkeypatch):
    monkeypatch.setattr(settings_dialog, "set_start_at_login", lambda enabled: None)
    config = Config(
        browser_accounts=[
            BrowserAccount(id="claude", kind="claude", show_fable=False),
        ]
    )
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)

    row = dialog._browser_account_rows[0]  # noqa: SLF001
    assert not row.fable_cb.isChecked()


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


def test_mcp_policy_for_removed_account_is_not_saved(qtbot, monkeypatch):
    """The MCP tab's rows are built once, before an account can be removed.

    Without a save-side filter, apply_to() would persist a pause policy for an
    account that no longer exists — inert at guard time, but it accumulates in
    config and reappears with a stale label next time Settings opens.
    """
    monkeypatch.setattr(settings_dialog, "set_start_at_login", lambda enabled: None)
    monkeypatch.setattr(settings_dialog, "clear_browser_session", lambda account_id: None)
    config = Config()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)

    dialog.mcp_enabled_cb.setChecked(True)
    for account_id in ("codex", "claude"):
        dialog.mcp_policy_controls[account_id][0].setChecked(True)

    # Drop the Codex account the way the Accounts tab does.
    dialog._browser_account_rows = [
        row for row in dialog._browser_account_rows if row.account_id != "codex"
    ]
    dialog.apply_to(config)

    assert "claude" in config.mcp_pause_policies
    assert "codex" not in config.mcp_pause_policies


_PAGE_KEYS = [
    "general", "claude", "codex", "opencode_go", "copilot",
    "openrouter", "antigravity", "mcp", "local_usage",
]


def test_settings_pages_are_listed_in_order_and_general_is_selected(qtbot):
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)

    assert dialog.page_keys() == _PAGE_KEYS
    assert dialog.current_page_key() == "general"
    assert [
        dialog.nav_list.item(i).text() for i in range(dialog.nav_list.count())
    ] == [
        "General", "PROVIDERS", "Claude", "Codex", "OpenCode", "GitHub Copilot",
        "OpenRouter", "Antigravity", "TOOLS", "MCP", "Local usage",
    ]


def test_every_settings_page_key_resolves_to_a_page(qtbot):
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)

    widgets = []
    for key in _PAGE_KEYS:
        widget = dialog.page_widget(key)
        assert widget is not None
        widgets.append(widget)
        dialog.show_page(key)
        assert dialog.pages.currentWidget() is widget
    assert len({id(w) for w in widgets}) == len(_PAGE_KEYS)


def test_settings_caption_rows_cannot_be_selected(qtbot):
    from PyQt6.QtCore import Qt

    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)

    captions = [
        dialog.nav_list.item(i)
        for i in range(dialog.nav_list.count())
        if dialog.nav_list.item(i).data(Qt.ItemDataRole.UserRole) is None
    ]
    assert [c.text() for c in captions] == ["PROVIDERS", "TOOLS"]
    for caption in captions:
        assert caption.flags() == Qt.ItemFlag.NoItemFlags
        assert not caption.isSelected()
    # Arrow keys step over a caption: General -> Claude, Antigravity -> MCP.
    dialog.show()
    dialog.nav_list.setFocus()
    qtbot.keyClick(dialog.nav_list, Qt.Key.Key_Down)
    assert dialog.current_page_key() == "claude"
    dialog.show_page("antigravity")
    qtbot.keyClick(dialog.nav_list, Qt.Key.Key_Down)
    assert dialog.current_page_key() == "mcp"
    qtbot.keyClick(dialog.nav_list, Qt.Key.Key_Up)
    assert dialog.current_page_key() == "antigravity"


def test_settings_row_and_page_stay_in_sync(qtbot):
    from PyQt6.QtCore import Qt

    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)

    dialog.show_page("mcp")
    assert dialog.nav_list.currentItem().data(Qt.ItemDataRole.UserRole) == "mcp"
    assert dialog.pages.currentWidget() is dialog.page_widget("mcp")

    row = next(
        i
        for i in range(dialog.nav_list.count())
        if dialog.nav_list.item(i).data(Qt.ItemDataRole.UserRole) == "codex"
    )
    dialog.nav_list.setCurrentRow(row)
    assert dialog.pages.currentWidget() is dialog.page_widget("codex")
    assert dialog.current_page_key() == "codex"


def test_show_provider_lands_on_each_provider_page(qtbot):
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)

    for kind in ("claude", "codex", "opencode_go", "copilot", "openrouter", "antigravity"):
        dialog.show_page("general")
        dialog.show_provider(kind)
        assert dialog.current_page_key() == kind
        assert dialog.pages.currentWidget() is dialog.page_widget(kind)


# ----- provider enable checkboxes, sidebar state and order -----

_PROVIDER_KINDS = ["claude", "codex", "opencode_go", "copilot", "openrouter", "antigravity"]


def _nav_keys(dialog: SettingsDialog) -> list:
    return [
        dialog.nav_list.item(i).data(Qt.ItemDataRole.UserRole)
        for i in range(dialog.nav_list.count())
    ]


def _nav_row(dialog: SettingsDialog, key: str):
    return dialog.nav_list.item(_nav_keys(dialog).index(key))


def _drop_at(dialog: SettingsDialog, pos: QPointF) -> QDropEvent:
    event = QDropEvent(
        pos,
        Qt.DropAction.MoveAction,
        QMimeData(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    dialog.nav_list.dropEvent(event)
    return event


def _drop(dialog: SettingsDialog, kind: str, target: str, below: bool) -> QDropEvent:
    """Drag the `kind` row (made current, as a press does) onto `target`'s edge."""
    dialog.show_page(kind)
    rect = dialog.nav_list.visualItemRect(_nav_row(dialog, target))
    y = rect.bottom() - 2 if below else rect.top() + 2
    return _drop_at(dialog, QPointF(rect.center().x(), y))


def test_each_enable_checkbox_lives_on_its_provider_page_only(qtbot):
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)

    checkboxes = {
        "claude": (dialog.claude_cb, "Enable Claude"),
        "codex": (dialog.codex_cb, "Enable Codex"),
        "opencode_go": (dialog.opencode_go_cb, "Enable OpenCode"),
        "copilot": (dialog.copilot_cb, "Enable GitHub Copilot"),
        "openrouter": (dialog.openrouter_cb, "Enable OpenRouter"),
        "antigravity": (dialog.antigravity_cb, "Enable Antigravity"),
    }
    for kind, (checkbox, text) in checkboxes.items():
        assert checkbox.text() == text
        assert dialog.page_widget(kind).isAncestorOf(checkbox)
        assert not dialog.page_widget("general").isAncestorOf(checkbox)
        # Page content stays editable when the provider is off.
        checkbox.setChecked(False)
        assert dialog.page_widget(kind).isEnabled()
        assert checkbox.isEnabled()
    assert all(box.title() != "Providers" for box in dialog.findChildren(QGroupBox))


def test_enable_checkbox_sits_above_hint_and_group_box(qtbot):
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)

    layout = dialog.page_widget("codex").layout()
    assert layout.itemAt(0).widget() is dialog.codex_cb
    hint = layout.itemAt(1).widget()
    assert "does not poll Codex" in hint.text()
    assert "Drag providers in the sidebar" in hint.text()
    assert isinstance(layout.itemAt(2).widget(), QGroupBox)


def test_account_hints_no_longer_point_at_the_general_page(qtbot):
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)

    for kind in ("claude", "codex"):
        texts = [label.text() for label in dialog.page_widget(kind).findChildren(QLabel)]
        assert any("is enabled above" in text for text in texts)
        assert not any("General" in text for text in texts)


def test_sidebar_row_dims_when_provider_is_off_and_restores(qtbot):
    dialog = SettingsDialog(Config(providers={"copilot": False}))
    qtbot.addWidget(dialog)

    row = _nav_row(dialog, "copilot")
    other = _nav_row(dialog, "claude")
    assert row.foreground().color() == QColor("#6b7280")
    assert "off" in row.toolTip()
    assert "Alt+Up" in row.toolTip()
    assert "off" not in other.toolTip()
    assert "Alt+Up" in other.toolTip()
    assert other.data(Qt.ItemDataRole.ForegroundRole) is None

    dialog.copilot_cb.setChecked(True)
    assert row.data(Qt.ItemDataRole.ForegroundRole) is None
    assert "off" not in row.toolTip()

    dialog.claude_cb.setChecked(False)
    assert other.foreground().color() == QColor("#6b7280")
    assert "off" in other.toolTip()


def test_dimming_follows_the_row_after_a_move(qtbot):
    dialog = SettingsDialog(Config(providers={"codex": False}))
    qtbot.addWidget(dialog)

    dialog.move_provider("codex", 4)
    assert _nav_row(dialog, "codex").foreground().color() == QColor("#6b7280")
    dialog.codex_cb.setChecked(True)
    assert _nav_row(dialog, "codex").data(Qt.ItemDataRole.ForegroundRole) is None


def test_sidebar_follows_saved_provider_order(qtbot):
    config = Config(provider_order=["openrouter", "claude"])
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)

    assert dialog.provider_order() == [
        "openrouter", "claude", "codex", "opencode_go", "copilot", "antigravity",
    ]
    assert _nav_keys(dialog) == [
        "general", None, "openrouter", "claude", "codex", "opencode_go",
        "copilot", "antigravity", None, "mcp", "local_usage",
    ]
    assert sorted(dialog.page_keys()) == sorted(_PAGE_KEYS)
    for kind in _PROVIDER_KINDS:
        dialog.show_page(kind)
        assert dialog.pages.currentWidget() is dialog.page_widget(kind)
        assert dialog.current_page_key() == kind


def test_only_provider_rows_are_draggable_and_none_accept_drops(qtbot):
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)

    for i in range(dialog.nav_list.count()):
        item = dialog.nav_list.item(i)
        assert not item.flags() & Qt.ItemFlag.ItemIsDropEnabled
        is_provider = item.data(Qt.ItemDataRole.UserRole) in _PROVIDER_KINDS
        assert bool(item.flags() & Qt.ItemFlag.ItemIsDragEnabled) == is_provider


def test_move_provider_reorders_clamps_and_keeps_other_rows(qtbot):
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)
    dialog.show_page("codex")

    dialog.move_provider("antigravity", 0)
    assert dialog.provider_order() == [
        "antigravity", "claude", "codex", "opencode_go", "copilot", "openrouter",
    ]
    dialog.move_provider("antigravity", 99)
    assert dialog.provider_order() == _PROVIDER_KINDS
    dialog.move_provider("claude", -5)
    assert dialog.provider_order() == _PROVIDER_KINDS
    dialog.move_provider("claude", 3)
    assert dialog.provider_order() == [
        "codex", "opencode_go", "copilot", "claude", "openrouter", "antigravity",
    ]
    # Non-provider rows never move, and asking to move one does nothing.
    dialog.move_provider("mcp", 0)
    dialog.move_provider("general", 3)
    keys = _nav_keys(dialog)
    assert keys[0] == "general" and keys[1] is None
    assert keys[8] is None and keys[9:] == ["mcp", "local_usage"]
    # The page that was shown stays shown and its row stays selected.
    assert dialog.current_page_key() == "codex"
    assert dialog.pages.currentWidget() is dialog.page_widget("codex")
    assert dialog.nav_list.currentItem() is _nav_row(dialog, "codex")


def test_show_page_lands_on_the_right_page_after_a_move(qtbot):
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)

    dialog.move_provider("openrouter", 0)
    dialog.move_provider("claude", 5)
    assert dialog.page_keys() == [
        "general", "openrouter", "codex", "opencode_go", "copilot",
        "antigravity", "claude", "mcp", "local_usage",
    ]
    for key in dialog.page_keys():
        dialog.show_page(key)
        assert dialog.current_page_key() == key
        assert dialog.pages.currentWidget() is dialog.page_widget(key)
        assert dialog.nav_list.currentRow() == _nav_keys(dialog).index(key)
    dialog.show_provider("claude")
    assert dialog.current_page_key() == "claude"


def test_moving_the_selected_provider_keeps_its_page_shown(qtbot):
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)
    dialog.show_page("claude")

    dialog.move_provider("claude", 4)

    assert dialog.current_page_key() == "claude"
    assert dialog.pages.currentWidget() is dialog.page_widget("claude")
    assert dialog.nav_list.currentRow() == _nav_keys(dialog).index("claude")


def test_drop_inside_provider_section_reorders_without_losing_rows(qtbot):
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)
    dialog.show()

    # Lower half of Copilot: Claude lands right after it.
    event = _drop(dialog, "claude", "copilot", below=True)

    assert event.dropAction() == Qt.DropAction.IgnoreAction
    assert dialog.provider_order() == [
        "codex", "opencode_go", "copilot", "claude", "openrouter", "antigravity",
    ]
    keys = _nav_keys(dialog)
    assert sorted(k for k in keys if k in _PROVIDER_KINDS) == sorted(_PROVIDER_KINDS)
    assert dialog.nav_list.count() == 11
    assert dialog.current_page_key() == "claude"

    # Upper half of Codex: Antigravity lands right before it.
    _drop(dialog, "antigravity", "codex", below=False)
    assert dialog.provider_order() == [
        "antigravity", "codex", "opencode_go", "copilot", "claude", "openrouter",
    ]
    # Dropping a row on its own edges changes nothing.
    _drop(dialog, "codex", "codex", below=True)
    _drop(dialog, "codex", "codex", below=False)
    assert dialog.provider_order() == [
        "antigravity", "codex", "opencode_go", "copilot", "claude", "openrouter",
    ]


def test_drop_outside_provider_section_changes_nothing(qtbot):
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)
    dialog.show()
    before = _nav_keys(dialog)

    rects = [
        dialog.nav_list.visualItemRect(dialog.nav_list.item(row))
        for row in (0, 1, 8, 9, 10)  # General, PROVIDERS, TOOLS, MCP, Local usage
    ]
    for rect in rects:
        dialog.show_page("claude")
        event = _drop_at(dialog, QPointF(rect.center()))
        assert event.dropAction() == Qt.DropAction.IgnoreAction
        assert _nav_keys(dialog) == before
        assert dialog.nav_list.count() == 11
    assert dialog.nav_list.drop_position(QPoint(5, 5000)) is None


def test_drag_move_refuses_positions_outside_the_provider_section(qtbot):
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)
    dialog.show()

    def move_event(pos: QPoint) -> QDragMoveEvent:
        event = QDragMoveEvent(
            pos,
            Qt.DropAction.MoveAction,
            QMimeData(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        dialog.nav_list.dragMoveEvent(event)
        return event

    inside = dialog.nav_list.visualItemRect(_nav_row(dialog, "codex")).center()
    outside = dialog.nav_list.visualItemRect(_nav_row(dialog, "mcp")).center()
    assert move_event(inside).isAccepted()
    assert not move_event(outside).isAccepted()


def test_alt_arrows_move_the_current_provider_row(qtbot):
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.show_page("codex")
    dialog.nav_list.setFocus()

    qtbot.keyClick(dialog.nav_list, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
    assert dialog.provider_order()[:3] == ["claude", "opencode_go", "codex"]
    assert dialog.current_page_key() == "codex"

    for _ in range(3):  # the third press is at the top and does nothing
        qtbot.keyClick(dialog.nav_list, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
    assert dialog.provider_order() == [
        "codex", "claude", "opencode_go", "copilot", "openrouter", "antigravity",
    ]
    assert dialog.current_page_key() == "codex"

    # Alt+Down on a non-provider row moves nothing.
    dialog.show_page("mcp")
    qtbot.keyClick(dialog.nav_list, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
    assert _nav_keys(dialog)[-2:] == ["mcp", "local_usage"]
    assert dialog.current_page_key() == "mcp"


def test_alt_arrows_work_when_the_arrow_key_carries_the_keypad_modifier(qtbot):
    # macOS sets KeypadModifier on every arrow key.
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.show_page("claude")
    dialog.nav_list.setFocus()

    qtbot.keyClick(
        dialog.nav_list,
        Qt.Key.Key_Down,
        Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.KeypadModifier,
    )

    assert dialog.provider_order()[:2] == ["codex", "claude"]


def test_apply_to_writes_the_provider_order(qtbot):
    config = Config()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)

    dialog.apply_to(config)
    assert config.provider_order == []

    dialog.move_provider("antigravity", 0)
    dialog.apply_to(config)
    assert config.provider_order == [
        "antigravity", "claude", "codex", "opencode_go", "copilot", "openrouter",
    ]

    dialog.move_provider("antigravity", 5)
    dialog.apply_to(config)
    assert config.provider_order == []


def test_apply_to_writes_the_enable_flags_from_the_provider_pages(qtbot):
    config = Config()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)

    dialog.codex_cb.setChecked(False)
    dialog.openrouter_cb.setChecked(True)
    dialog.apply_to(config)

    assert config.providers.codex is False
    assert config.providers.openrouter is True


# ----- sidebar drag grip -----


def _grip_dialog(qtbot) -> SettingsDialog:
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog


def _viewport_move(dialog: SettingsDialog, pos: QPoint) -> None:
    viewport = dialog.nav_list.viewport()
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(pos),
        QPointF(viewport.mapToGlobal(pos)),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(viewport, event)


def _count_color(image: QImage, rect: QRect, color: str) -> int:
    target = QColor(color).rgb()
    return sum(
        1
        for x in range(rect.left(), rect.right() + 1)
        for y in range(rect.top(), rect.bottom() + 1)
        if image.pixelColor(x, y).rgb() == target
    )


def test_sidebar_uses_the_grip_delegate(qtbot):
    dialog = _grip_dialog(qtbot)

    assert isinstance(dialog.nav_list.itemDelegate(), settings_dialog._NavDelegate)


def test_grip_rect_sits_at_the_right_edge_of_the_row(qtbot):
    dialog = _grip_dialog(qtbot)
    row = dialog.nav_list.visualItemRect(_nav_row(dialog, "codex"))

    grip = settings_dialog._nav_grip_rect(row)

    assert row.contains(grip)
    assert grip.right() + 1 == row.right() + 1 - 12
    assert abs(grip.center().y() - row.center().y()) <= 1


def test_provider_rows_paint_a_grip_and_other_rows_do_not(qtbot):
    dialog = _grip_dialog(qtbot)
    nav = dialog.nav_list
    dialog.show_page("general")
    image = nav.grab().toImage()
    offset = nav.viewport().mapTo(nav, QPoint(0, 0))

    def grip_rect(key: str) -> QRect:
        row = nav.visualItemRect(_nav_row(dialog, key))
        return settings_dialog._nav_grip_rect(row).translated(offset)

    assert _count_color(image, grip_rect("codex"), settings_dialog._NAV_GRIP_COLOR) > 0
    assert _count_color(image, grip_rect("general"), settings_dialog._NAV_GRIP_COLOR) == 0
    assert _count_color(image, grip_rect("mcp"), settings_dialog._NAV_GRIP_COLOR) == 0

    # The selected provider row draws its grip in the selected colour.
    dialog.show_page("codex")
    image = nav.grab().toImage()
    selected = settings_dialog._NAV_GRIP_SELECTED_COLOR
    assert _count_color(image, grip_rect("codex"), selected) > 0
    assert _count_color(image, grip_rect("claude"), selected) == 0


def test_cursor_is_an_open_hand_only_over_a_provider_grip_zone(qtbot):
    dialog = _grip_dialog(qtbot)
    nav = dialog.nav_list
    viewport = nav.viewport()
    codex = nav.visualItemRect(_nav_row(dialog, "codex"))
    general = nav.visualItemRect(_nav_row(dialog, "general"))
    caption = nav.visualItemRect(
        next(nav.item(i) for i in range(nav.count()) if nav.item(i).text() == "PROVIDERS")
    )

    _viewport_move(dialog, QPoint(codex.right() - 10, codex.center().y()))
    assert viewport.cursor().shape() == Qt.CursorShape.OpenHandCursor

    _viewport_move(dialog, QPoint(codex.left() + 10, codex.center().y()))
    assert viewport.cursor().shape() == Qt.CursorShape.ArrowCursor

    _viewport_move(dialog, QPoint(codex.right() - 10, codex.center().y()))
    _viewport_move(dialog, QPoint(general.right() - 10, general.center().y()))
    assert viewport.cursor().shape() == Qt.CursorShape.ArrowCursor

    _viewport_move(dialog, QPoint(codex.right() - 10, codex.center().y()))
    _viewport_move(dialog, QPoint(caption.right() - 10, caption.center().y()))
    assert viewport.cursor().shape() == Qt.CursorShape.ArrowCursor

    _viewport_move(dialog, QPoint(codex.right() - 10, codex.center().y()))
    QApplication.sendEvent(viewport, QEvent(QEvent.Type.Leave))
    assert viewport.cursor().shape() == Qt.CursorShape.ArrowCursor


def test_clicking_the_grip_still_shows_the_provider_page(qtbot):
    dialog = _grip_dialog(qtbot)
    nav = dialog.nav_list
    dialog.show_page("general")
    row = nav.visualItemRect(_nav_row(dialog, "copilot"))

    qtbot.mouseClick(
        nav.viewport(),
        Qt.MouseButton.LeftButton,
        pos=QPoint(row.right() - 10, row.center().y()),
    )

    assert dialog.current_page_key() == "copilot"
