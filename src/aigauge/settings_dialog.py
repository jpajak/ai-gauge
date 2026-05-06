from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .config import (
    Config,
    get_github_pat,
    get_openrouter_key,
    get_openrouter_mgmt_key,
    set_github_pat,
    set_openrouter_key,
    set_openrouter_mgmt_key,
)
from .error_dialog import reveal_path
from .logging_setup import log_path
from .startup import set_start_at_login


log = logging.getLogger("aigauge.settings_dialog")

_COPILOT_PLAN_QUOTAS = (
    ("Pro", 300),
    ("Pro+", 1500),
    ("Business", 300),
    ("Enterprise", 1000),
    ("Free", 50),
)


_DARK_STYLESHEET = """
QDialog {
    background: #1f2937;
    color: #e5e7eb;
}
QLabel {
    color: #e5e7eb;
    background: transparent;
}
QLabel[hint="true"] {
    color: #9ca3af;
    font-size: 10px;
}
QGroupBox {
    color: #f3f4f6;
    font-weight: 600;
    border: 1px solid #374151;
    border-radius: 6px;
    margin-top: 10px;
    padding: 10px 10px 8px 10px;
    background: transparent;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 6px;
    background: #1f2937;
    color: #f3f4f6;
}
QTabWidget::pane {
    border: 1px solid #374151;
    border-radius: 6px;
    top: -1px;
}
QTabBar::tab {
    background: #111827;
    color: #cbd5e1;
    border: 1px solid #374151;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    padding: 6px 14px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background: #1f2937;
    color: #f3f4f6;
}
QLineEdit, QSpinBox, QComboBox {
    background: #111827;
    color: #f3f4f6;
    border: 1px solid #374151;
    border-radius: 4px;
    padding: 4px 6px;
    selection-background-color: #2563eb;
    min-height: 22px;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
    border-color: #3b82f6;
}
QComboBox::drop-down {
    width: 22px;
    border: none;
    background: #374151;
}
QComboBox QAbstractItemView {
    background: #111827;
    color: #f3f4f6;
    selection-background-color: #2563eb;
}
QSpinBox::up-button, QSpinBox::down-button {
    width: 16px;
    background: #374151;
    border: none;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {
    background: #4b5563;
}
QSpinBox::up-arrow, QSpinBox::down-arrow {
    width: 8px;
    height: 8px;
}
QCheckBox {
    color: #e5e7eb;
    spacing: 6px;
    background: transparent;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #4b5563;
    border-radius: 3px;
    background: #111827;
}
QCheckBox::indicator:checked {
    background: #3b82f6;
    border-color: #3b82f6;
    image: none;
}
QPushButton {
    background: #374151;
    color: #f3f4f6;
    border: 1px solid #4b5563;
    border-radius: 4px;
    padding: 5px 12px;
    min-height: 22px;
}
QPushButton:hover {
    background: #4b5563;
}
QPushButton:pressed {
    background: #6b7280;
}
QPushButton:default {
    background: #2563eb;
    border-color: #1d4ed8;
}
QPushButton:default:hover {
    background: #1d4ed8;
}
QSlider::groove:horizontal {
    height: 4px;
    background: #374151;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #3b82f6;
    width: 14px;
    margin: -6px 0;
    border-radius: 7px;
}
QSlider::sub-page:horizontal {
    background: #3b82f6;
    border-radius: 2px;
}
"""


def _hint_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("hint", True)
    label.setOpenExternalLinks(True)
    label.setWordWrap(True)
    return label


class SettingsDialog(QDialog):
    sign_in_clicked = pyqtSignal(str)  # provider name
    paste_cookie_clicked = pyqtSignal(str)  # provider name

    def __init__(self, config: Config, parent=None):
        # Don't pass parent — avoids any cascading stylesheet issues.
        # Keep window centered relative to parent manually if needed later.
        super().__init__(None)
        # Intentionally NOT stays-on-top or app-modal: users may need to switch
        # to their normal browser, and clicking the status panel should bring
        # this existing Settings window back to the foreground.
        self.setWindowTitle("AI Gauge — Settings")
        self.setModal(False)
        self.resize(540, 470)
        self.setMinimumSize(460, 360)
        self.setStyleSheet(_DARK_STYLESHEET)
        self._config = config

        # ----- General -----
        general = QGroupBox("General")
        general_grid = QGridLayout(general)
        general_grid.setColumnStretch(1, 1)
        general_grid.setColumnStretch(3, 1)
        general_grid.setHorizontalSpacing(10)
        general_grid.setVerticalSpacing(8)

        self.active_refresh_spin = QSpinBox()
        self.active_refresh_spin.setRange(1, 180)
        self.active_refresh_spin.setSuffix(" min")
        self.active_refresh_spin.setValue(config.active_refresh_interval_minutes)
        self.active_refresh_spin.setMinimumWidth(110)
        self.active_refresh_spin.setToolTip(
            "Refresh cadence after a manual refresh or when usage is changing."
        )
        general_grid.addWidget(QLabel("Active refresh:"), 0, 0)
        general_grid.addWidget(self.active_refresh_spin, 0, 1)

        self.refresh_spin = QSpinBox()
        self.refresh_spin.setRange(1, 180)
        self.refresh_spin.setSuffix(" min")
        self.refresh_spin.setValue(config.refresh_interval_minutes)
        self.refresh_spin.setMinimumWidth(110)
        self.refresh_spin.setToolTip(
            "Slowest refresh cadence after repeated unchanged readings."
        )
        general_grid.addWidget(QLabel("Idle max:"), 0, 2)
        general_grid.addWidget(self.refresh_spin, 0, 3)

        self.always_on_top_cb = QCheckBox("Always on top")
        self.always_on_top_cb.setChecked(config.window.always_on_top)
        general_grid.addWidget(self.always_on_top_cb, 1, 0, 1, 2)

        self.startup_cb = QCheckBox("Start at login")
        self.startup_cb.setChecked(config.start_at_login)
        general_grid.addWidget(self.startup_cb, 1, 2, 1, 2)

        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(30, 100)
        self.opacity_slider.setValue(int(config.window.opacity * 100))
        self.opacity_value = QLabel(f"{int(config.window.opacity * 100)}%")
        self.opacity_value.setMinimumWidth(40)
        self.opacity_slider.valueChanged.connect(
            lambda v: self.opacity_value.setText(f"{v}%")
        )
        op_row = QHBoxLayout()
        op_row.setContentsMargins(0, 0, 0, 0)
        op_row.addWidget(self.opacity_slider, 1)
        op_row.addWidget(self.opacity_value)
        general_grid.addWidget(QLabel("Opacity:"), 2, 0)
        general_grid.addLayout(op_row, 2, 1, 1, 3)

        # ----- Providers -----
        providers = QGroupBox("Providers")
        providers_grid = QGridLayout(providers)
        providers_grid.setColumnStretch(0, 1)
        providers_grid.setHorizontalSpacing(8)
        providers_grid.setVerticalSpacing(6)

        providers_hint = _hint_label(
            "Uncheck a provider to hide its tile from the widget. The panel "
            "shrinks to fit only what's enabled."
        )
        providers_grid.addWidget(providers_hint, 0, 0, 1, 3)

        self.claude_cb = QCheckBox("Claude.ai")
        self.claude_cb.setToolTip("Show the Claude.ai usage tile in the panel.")
        self.claude_cb.setChecked(config.providers.claude)
        claude_signin = QPushButton("Sign in (email)")
        claude_signin.setObjectName("claude_signin_btn")
        claude_signin.setToolTip(
            "Open an embedded browser to sign in. Only works for email/password — "
            "Google sign-in is blocked in embedded browsers."
        )
        claude_signin.clicked.connect(lambda: self.sign_in_clicked.emit("claude"))
        claude_paste = QPushButton("Paste cookie")
        claude_paste.setObjectName("claude_paste_cookie_btn")
        claude_paste.setToolTip(
            "Paste the sessionKey cookie from your real browser (Google sign-in path)."
        )
        claude_paste.clicked.connect(lambda: self.paste_cookie_clicked.emit("claude"))
        providers_grid.addWidget(self.claude_cb, 1, 0)
        providers_grid.addWidget(claude_signin, 1, 1)
        providers_grid.addWidget(claude_paste, 1, 2)

        self.claude_design_cb = QCheckBox("Show Claude Design limit")
        self.claude_design_cb.setToolTip(
            "Show Claude's separate design-generation usage limit when Claude exposes it."
        )
        self.claude_design_cb.setChecked(config.providers.claude_design)
        providers_grid.addWidget(self.claude_design_cb, 2, 0, 1, 3)

        self.codex_cb = QCheckBox("ChatGPT Codex")
        self.codex_cb.setToolTip("Show the ChatGPT Codex usage tile in the panel.")
        self.codex_cb.setChecked(config.providers.codex)
        codex_signin = QPushButton("Sign in (email)")
        codex_signin.setObjectName("codex_signin_btn")
        codex_signin.setToolTip(
            "Open an embedded browser to sign in. Only works for email/password."
        )
        codex_signin.clicked.connect(lambda: self.sign_in_clicked.emit("codex"))
        codex_paste = QPushButton("Paste cookie")
        codex_paste.setObjectName("codex_paste_cookie_btn")
        codex_paste.setToolTip(
            "Paste the __Secure-next-auth.session-token cookie from your real browser."
        )
        codex_paste.clicked.connect(lambda: self.paste_cookie_clicked.emit("codex"))
        providers_grid.addWidget(self.codex_cb, 3, 0)
        providers_grid.addWidget(codex_signin, 3, 1)
        providers_grid.addWidget(codex_paste, 3, 2)

        google_hint = _hint_label(
            "If you sign in with <b>Google</b>, use <b>Paste cookie</b> — "
            "Google blocks embedded browsers."
        )
        providers_grid.addWidget(google_hint, 4, 0, 1, 3)

        self.copilot_cb = QCheckBox("GitHub Copilot")
        self.copilot_cb.setToolTip("Show the GitHub Copilot usage tile in the panel.")
        self.copilot_cb.setChecked(config.providers.copilot)
        providers_grid.addWidget(self.copilot_cb, 5, 0, 1, 3)

        self.openrouter_cb = QCheckBox("OpenRouter")
        self.openrouter_cb.setToolTip("Show the OpenRouter usage tile in the panel.")
        self.openrouter_cb.setChecked(config.providers.openrouter)
        providers_grid.addWidget(self.openrouter_cb, 6, 0, 1, 3)

        # ----- Copilot details -----
        copilot = QGroupBox("GitHub Copilot")
        copilot_form = QFormLayout(copilot)
        copilot_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        copilot_form.setHorizontalSpacing(12)
        copilot_form.setVerticalSpacing(8)
        copilot_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )

        self.gh_pat_edit = QLineEdit()
        self.gh_pat_edit.setEchoMode(QLineEdit.EchoMode.Password)
        existing_pat = get_github_pat()
        self._had_existing_pat = bool(existing_pat)
        if existing_pat:
            self.gh_pat_edit.setPlaceholderText(
                "•••••••••• (saved — leave blank to keep)"
            )
        else:
            self.gh_pat_edit.setPlaceholderText("ghp_... or github_pat_...")
        copilot_form.addRow("Personal Access Token:", self.gh_pat_edit)

        self.clear_pat_cb = QCheckBox("Clear saved GitHub PAT")
        self.clear_pat_cb.setToolTip(
            "Remove the token from the system keychain."
        )
        self.clear_pat_cb.setVisible(self._had_existing_pat)
        if self._had_existing_pat:
            copilot_form.addRow("", self.clear_pat_cb)

        pat_help = _hint_label(
            "Fine-grained PAT: add <b>Account permissions → Plan → Read</b>. "
            "<a style='color:#60a5fa;' "
            "href='https://github.com/settings/personal-access-tokens/new'>"
            "Create one →</a>"
        )
        copilot_form.addRow("", pat_help)

        self.gh_username = QLineEdit()
        self.gh_username.setPlaceholderText("(auto-detected from PAT if blank)")
        if config.copilot.username:
            self.gh_username.setText(config.copilot.username)
        copilot_form.addRow("Username:", self.gh_username)

        self.gh_billing_org = QLineEdit()
        self.gh_billing_org.setPlaceholderText("(blank for individual Pro/Pro+)")
        if config.copilot.billing_org:
            self.gh_billing_org.setText(config.copilot.billing_org)
        copilot_form.addRow("Billing org:", self.gh_billing_org)

        org_hint = _hint_label(
            "Only set this if Copilot is billed through an organization. The PAT "
            "must have organization <b>Administration → Read</b> permission and "
            "you must be allowed to view billing usage."
        )
        copilot_form.addRow("", org_hint)

        self.gh_plan = QComboBox()
        for plan, quota in _COPILOT_PLAN_QUOTAS:
            self.gh_plan.addItem(f"{plan} ({quota:,})", quota)
        self.gh_plan.addItem("Custom", None)
        self.gh_plan.currentIndexChanged.connect(self._sync_custom_quota_enabled)
        copilot_form.addRow("Plan / quota:", self.gh_plan)

        self.gh_quota = QSpinBox()
        self.gh_quota.setRange(1, 100000)
        self.gh_quota.setValue(config.copilot.monthly_quota)
        self.gh_quota.setMinimumWidth(110)
        self.gh_quota_label = QLabel("Custom quota:")
        copilot_form.addRow(self.gh_quota_label, self.gh_quota)
        self._set_quota_selection(config.copilot.monthly_quota)

        quota_hint = _hint_label(
            "GitHub does not expose a reliable personal-plan quota through the API; "
            "choose your plan here or Custom for a different allowance."
        )
        copilot_form.addRow("", quota_hint)

        # ----- OpenRouter details -----
        openrouter = QGroupBox("OpenRouter")
        openrouter_form = QFormLayout(openrouter)
        openrouter_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        openrouter_form.setHorizontalSpacing(12)
        openrouter_form.setVerticalSpacing(8)
        openrouter_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )

        self.or_key_edit = QLineEdit()
        self.or_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        existing_or_key = get_openrouter_key()
        self._had_existing_or_key = bool(existing_or_key)
        if existing_or_key:
            self.or_key_edit.setPlaceholderText(
                "•••••••••• (saved — leave blank to keep)"
            )
        else:
            self.or_key_edit.setPlaceholderText("sk-or-...")
        openrouter_form.addRow("Inference key:", self.or_key_edit)

        self.clear_or_key_cb = QCheckBox("Clear saved inference key")
        self.clear_or_key_cb.setToolTip(
            "Remove the inference key from the system keychain."
        )
        self.clear_or_key_cb.setVisible(self._had_existing_or_key)
        if self._had_existing_or_key:
            openrouter_form.addRow("", self.clear_or_key_cb)

        or_key_help = _hint_label(
            "Your regular API key from <a style='color:#60a5fa;' "
            "href='https://openrouter.ai/keys'>openrouter.ai/keys</a> — the same "
            "one your apps use for chat completions. <b>Required</b> for daily "
            "spend."
        )
        openrouter_form.addRow("", or_key_help)

        self.or_mgmt_key_edit = QLineEdit()
        self.or_mgmt_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        existing_or_mgmt_key = get_openrouter_mgmt_key()
        self._had_existing_or_mgmt_key = bool(existing_or_mgmt_key)
        if existing_or_mgmt_key:
            self.or_mgmt_key_edit.setPlaceholderText(
                "•••••••••• (saved — leave blank to keep)"
            )
        else:
            self.or_mgmt_key_edit.setPlaceholderText("sk-or-v1-... (optional)")
        openrouter_form.addRow("Management key:", self.or_mgmt_key_edit)

        self.clear_or_mgmt_key_cb = QCheckBox("Clear saved management key")
        self.clear_or_mgmt_key_cb.setToolTip(
            "Remove the management key from the system keychain."
        )
        self.clear_or_mgmt_key_cb.setVisible(self._had_existing_or_mgmt_key)
        if self._had_existing_or_mgmt_key:
            openrouter_form.addRow("", self.clear_or_mgmt_key_cb)

        or_mgmt_key_help = _hint_label(
            "<b>Optional</b>, but needed to show your <b>account-wide remaining "
            "balance</b> and <b>model activity</b>. Create a separate management key at "
            "<a style='color:#60a5fa;' "
            "href='https://openrouter.ai/settings/provisioning-keys'>"
            "openrouter.ai/settings/provisioning-keys</a>. Management keys can't "
            "make inference calls, so this is in addition to the inference key "
            "above, not a replacement."
        )
        openrouter_form.addRow("", or_mgmt_key_help)

        self.or_daily_budget = QDoubleSpinBox()
        self.or_daily_budget.setRange(0.0, 10000.0)
        self.or_daily_budget.setDecimals(2)
        self.or_daily_budget.setSingleStep(1.0)
        self.or_daily_budget.setPrefix("$ ")
        self.or_daily_budget.setSpecialValueText("(no gauge)")
        self.or_daily_budget.setValue(
            float(config.openrouter.daily_budget or 0.0)
        )
        openrouter_form.addRow("Daily budget:", self.or_daily_budget)

        budget_hint = _hint_label(
            "Optional. If set, the Daily row shows a colored gauge against this "
            "budget. Leave at $0.00 to show only the dollar amount."
        )
        openrouter_form.addRow("", budget_hint)

        general_tab = QWidget()
        general_tab_layout = QVBoxLayout(general_tab)
        general_tab_layout.setContentsMargins(10, 10, 10, 10)
        general_tab_layout.setSpacing(10)
        general_tab_layout.addWidget(general)
        general_tab_layout.addWidget(providers)
        general_tab_layout.addStretch(1)

        copilot_tab = QWidget()
        copilot_tab_layout = QVBoxLayout(copilot_tab)
        copilot_tab_layout.setContentsMargins(10, 10, 10, 10)
        copilot_tab_layout.setSpacing(10)
        copilot_tab_layout.addWidget(copilot)
        copilot_tab_layout.addStretch(1)

        openrouter_tab = QWidget()
        openrouter_tab_layout = QVBoxLayout(openrouter_tab)
        openrouter_tab_layout.setContentsMargins(10, 10, 10, 10)
        openrouter_tab_layout.setSpacing(10)
        openrouter_tab_layout.addWidget(openrouter)
        openrouter_tab_layout.addStretch(1)

        tabs = QTabWidget()
        tabs.addTab(general_tab, "General")
        tabs.addTab(copilot_tab, "GitHub Copilot")
        tabs.addTab(openrouter_tab, "OpenRouter")
        tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # ----- Buttons -----
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        log_btn = QPushButton("Open log folder")
        log_btn.setToolTip(
            "Reveal ai-gauge.log in Explorer — useful when reporting a problem."
        )
        log_btn.clicked.connect(lambda: reveal_path(log_path()))

        button_row = QHBoxLayout()
        button_row.addWidget(log_btn)
        button_row.addStretch(1)
        button_row.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(10)
        layout.addWidget(tabs, 1)
        layout.addLayout(button_row)

    def _accept(self) -> None:
        new_pat = self.gh_pat_edit.text().strip()
        if self.clear_pat_cb.isChecked() and not new_pat:
            try:
                set_github_pat(None)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(
                    self,
                    "PAT was not cleared",
                    f"The saved token could not be cleared:\n{exc}",
                )
                return
            if get_github_pat():
                QMessageBox.warning(
                    self,
                    "PAT was not cleared",
                    "The token still appears to be available after clearing. "
                    "Remove the 'ai-gauge' / 'github-pat' credential from "
                    "your system keychain.",
                )
                return
        if new_pat:
            try:
                set_github_pat(new_pat)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(
                    self,
                    "PAT was not saved",
                    f"The system keychain rejected the token:\n{exc}",
                )
                return
            if get_github_pat() != new_pat:
                QMessageBox.warning(
                    self,
                    "PAT was not saved",
                    "The token could not be read back from the system "
                    "keychain. Try running the app normally rather than as a "
                    "different user/elevated account.",
                )
                return
            log.info("Saved GitHub PAT to system keychain.")

        new_or_key = self.or_key_edit.text().strip()
        if self.clear_or_key_cb.isChecked() and not new_or_key:
            try:
                set_openrouter_key(None)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(
                    self,
                    "OpenRouter inference key was not cleared",
                    f"The saved key could not be cleared:\n{exc}",
                )
                return
            if get_openrouter_key():
                QMessageBox.warning(
                    self,
                    "OpenRouter inference key was not cleared",
                    "The key still appears to be available after clearing. "
                    "Remove the 'ai-gauge' / 'openrouter-key' credential from "
                    "your system keychain.",
                )
                return
        if new_or_key:
            try:
                set_openrouter_key(new_or_key)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(
                    self,
                    "OpenRouter inference key was not saved",
                    f"The system keychain rejected the key:\n{exc}",
                )
                return
            if get_openrouter_key() != new_or_key:
                QMessageBox.warning(
                    self,
                    "OpenRouter inference key was not saved",
                    "The key could not be read back from the system "
                    "keychain. Try running the app normally rather than as a "
                    "different user/elevated account.",
                )
                return
            log.info("Saved OpenRouter inference key to system keychain.")

        new_or_mgmt_key = self.or_mgmt_key_edit.text().strip()
        if self.clear_or_mgmt_key_cb.isChecked() and not new_or_mgmt_key:
            try:
                set_openrouter_mgmt_key(None)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(
                    self,
                    "OpenRouter management key was not cleared",
                    f"The saved key could not be cleared:\n{exc}",
                )
                return
            if get_openrouter_mgmt_key():
                QMessageBox.warning(
                    self,
                    "OpenRouter management key was not cleared",
                    "The key still appears to be available after clearing. "
                    "Remove the 'ai-gauge' / 'openrouter-mgmt-key' credential "
                    "from your system keychain.",
                )
                return
        if new_or_mgmt_key:
            try:
                set_openrouter_mgmt_key(new_or_mgmt_key)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(
                    self,
                    "OpenRouter management key was not saved",
                    f"The system keychain rejected the key:\n{exc}",
                )
                return
            if get_openrouter_mgmt_key() != new_or_mgmt_key:
                QMessageBox.warning(
                    self,
                    "OpenRouter management key was not saved",
                    "The key could not be read back from the system "
                    "keychain. Try running the app normally rather than as a "
                    "different user/elevated account.",
                )
                return
            log.info("Saved OpenRouter management key to system keychain.")

        self.accept()

    def _set_quota_selection(self, quota: int) -> None:
        for i in range(self.gh_plan.count()):
            if self.gh_plan.itemData(i) == quota:
                self.gh_plan.setCurrentIndex(i)
                self._sync_custom_quota_enabled()
                return
        self.gh_plan.setCurrentIndex(self.gh_plan.count() - 1)
        self.gh_quota.setValue(quota)
        self._sync_custom_quota_enabled()

    def _sync_custom_quota_enabled(self) -> None:
        is_custom = self.gh_plan.currentData() is None
        self.gh_quota.setVisible(is_custom)
        self.gh_quota_label.setVisible(is_custom)

    def apply_to(self, config: Config) -> None:
        config.refresh_interval_minutes = self.refresh_spin.value()
        config.active_refresh_interval_minutes = min(
            self.active_refresh_spin.value(),
            config.refresh_interval_minutes,
        )
        config.start_at_login = self.startup_cb.isChecked()
        config.window.always_on_top = self.always_on_top_cb.isChecked()
        config.window.opacity = self.opacity_slider.value() / 100.0
        config.providers.claude = self.claude_cb.isChecked()
        config.providers.claude_design = self.claude_design_cb.isChecked()
        config.providers.codex = self.codex_cb.isChecked()
        config.providers.copilot = self.copilot_cb.isChecked()
        config.providers.openrouter = self.openrouter_cb.isChecked()
        username = self.gh_username.text().strip()
        config.copilot.username = username or None
        billing_org = self.gh_billing_org.text().strip()
        config.copilot.billing_org = billing_org or None
        selected_quota = self.gh_plan.currentData()
        config.copilot.monthly_quota = (
            int(selected_quota) if selected_quota is not None else self.gh_quota.value()
        )
        budget = self.or_daily_budget.value()
        config.openrouter.daily_budget = budget if budget > 0 else None
        set_start_at_login(config.start_at_login)

        config.save()
