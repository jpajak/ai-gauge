from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
)

from .config import Config, get_github_pat, set_github_pat


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
    font-size: 11px;
}
QGroupBox {
    color: #f3f4f6;
    font-weight: 600;
    border: 1px solid #374151;
    border-radius: 6px;
    margin-top: 14px;
    padding: 14px 10px 10px 10px;
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
QLineEdit, QSpinBox {
    background: #111827;
    color: #f3f4f6;
    border: 1px solid #374151;
    border-radius: 4px;
    padding: 4px 6px;
    selection-background-color: #2563eb;
    min-height: 22px;
}
QLineEdit:focus, QSpinBox:focus {
    border-color: #3b82f6;
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
        # Stays-on-top so we render above the main widget (which is itself stays-on-top).
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setWindowTitle("Usage View — Settings")
        self.setModal(True)
        self.resize(560, 650)
        self.setStyleSheet(_DARK_STYLESHEET)
        self._config = config

        # ----- General -----
        general = QGroupBox("General")
        general_form = QFormLayout(general)
        general_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        general_form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        general_form.setHorizontalSpacing(12)
        general_form.setVerticalSpacing(10)

        self.refresh_spin = QSpinBox()
        self.refresh_spin.setRange(1, 60)
        self.refresh_spin.setSuffix(" min")
        self.refresh_spin.setValue(config.refresh_interval_minutes)
        self.refresh_spin.setMinimumWidth(110)
        general_form.addRow("Auto-refresh every:", self.refresh_spin)

        self.always_on_top_cb = QCheckBox("Always on top")
        self.always_on_top_cb.setChecked(config.window.always_on_top)
        general_form.addRow("", self.always_on_top_cb)

        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(30, 100)
        self.opacity_slider.setValue(int(config.window.opacity * 100))
        self.opacity_value = QLabel(f"{int(config.window.opacity * 100)}%")
        self.opacity_value.setMinimumWidth(40)
        self.opacity_slider.valueChanged.connect(
            lambda v: self.opacity_value.setText(f"{v}%")
        )
        op_row = QHBoxLayout()
        op_row.addWidget(self.opacity_slider, 1)
        op_row.addWidget(self.opacity_value)
        general_form.addRow("Opacity:", op_row)

        # ----- Providers -----
        providers = QGroupBox("Providers")
        providers_layout = QVBoxLayout(providers)
        providers_layout.setSpacing(8)

        self.claude_cb = QCheckBox("Claude.ai")
        self.claude_cb.setChecked(config.providers.claude)
        claude_paste = QPushButton("Paste cookie")
        claude_paste.setToolTip(
            "Paste the sessionKey cookie from your real browser (Google sign-in path)."
        )
        claude_paste.clicked.connect(lambda: self.paste_cookie_clicked.emit("claude"))
        claude_signin = QPushButton("Sign in (email)")
        claude_signin.setToolTip(
            "Open an embedded browser to sign in. Only works for email/password — "
            "Google sign-in is blocked in embedded browsers."
        )
        claude_signin.clicked.connect(lambda: self.sign_in_clicked.emit("claude"))
        claude_row = QHBoxLayout()
        claude_row.addWidget(self.claude_cb, 1)
        claude_row.addWidget(claude_paste)
        claude_row.addWidget(claude_signin)
        providers_layout.addLayout(claude_row)

        self.codex_cb = QCheckBox("ChatGPT Codex")
        self.codex_cb.setChecked(config.providers.codex)
        codex_paste = QPushButton("Paste cookie")
        codex_paste.setToolTip(
            "Paste the __Secure-next-auth.session-token cookie from your real browser."
        )
        codex_paste.clicked.connect(lambda: self.paste_cookie_clicked.emit("codex"))
        codex_signin = QPushButton("Sign in (email)")
        codex_signin.setToolTip(
            "Open an embedded browser to sign in. Only works for email/password."
        )
        codex_signin.clicked.connect(lambda: self.sign_in_clicked.emit("codex"))
        codex_row = QHBoxLayout()
        codex_row.addWidget(self.codex_cb, 1)
        codex_row.addWidget(codex_paste)
        codex_row.addWidget(codex_signin)
        providers_layout.addLayout(codex_row)

        google_hint = _hint_label(
            "If you sign in with <b>Google</b>, use <b>Paste cookie</b> — "
            "Google blocks embedded browsers."
        )
        providers_layout.addWidget(google_hint)

        self.copilot_cb = QCheckBox("GitHub Copilot")
        self.copilot_cb.setChecked(config.providers.copilot)
        providers_layout.addWidget(self.copilot_cb)

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
        if existing_pat:
            self.gh_pat_edit.setPlaceholderText("•••••••••• (saved — leave blank to keep)")
        else:
            self.gh_pat_edit.setPlaceholderText("ghp_... or github_pat_...")
        copilot_form.addRow("Personal Access Token:", self.gh_pat_edit)

        pat_help = _hint_label(
            "Use a <b>fine-grained PAT</b>. Add <b>Account permissions → Plan → Read</b> "
            "(scroll down in the 'Add permissions' dropdown).<br/>"
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

        self.gh_quota = QSpinBox()
        self.gh_quota.setRange(1, 100000)
        self.gh_quota.setValue(config.copilot.monthly_quota)
        self.gh_quota.setMinimumWidth(110)
        copilot_form.addRow("Monthly quota:", self.gh_quota)

        quota_hint = _hint_label("Pro = 300, Pro+ = 1500, Business = 300, Enterprise = 1000")
        copilot_form.addRow("", quota_hint)

        # ----- Buttons -----
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(12)
        layout.addWidget(general)
        layout.addWidget(providers)
        layout.addWidget(copilot)
        layout.addStretch(1)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        new_pat = self.gh_pat_edit.text().strip()
        if new_pat:
            try:
                set_github_pat(new_pat)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(
                    self,
                    "PAT was not saved",
                    f"Windows Credential Manager rejected the token:\n{exc}",
                )
                return
            if get_github_pat() != new_pat:
                QMessageBox.warning(
                    self,
                    "PAT was not saved",
                    "The token could not be read back from Windows Credential "
                    "Manager. Try running the app normally rather than as a "
                    "different user/elevated account.",
                )
                return
            print("Saved GitHub PAT to Windows Credential Manager.")
        self.accept()

    def apply_to(self, config: Config) -> None:
        config.refresh_interval_minutes = self.refresh_spin.value()
        config.window.always_on_top = self.always_on_top_cb.isChecked()
        config.window.opacity = self.opacity_slider.value() / 100.0
        config.providers.claude = self.claude_cb.isChecked()
        config.providers.codex = self.codex_cb.isChecked()
        config.providers.copilot = self.copilot_cb.isChecked()
        username = self.gh_username.text().strip()
        config.copilot.username = username or None
        billing_org = self.gh_billing_org.text().strip()
        config.copilot.billing_org = billing_org or None
        config.copilot.monthly_quota = self.gh_quota.value()

        config.save()
