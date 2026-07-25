from __future__ import annotations

import logging
import uuid

from PyQt6.QtCore import QPointF, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QDesktopServices,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
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
    BrowserAccount,
    ColorThresholds,
    Config,
    account_display_name,
    app_data_dir,
    browser_accounts,
    get_github_pat,
    get_openrouter_key,
    get_openrouter_mgmt_key,
    provider_base_name,
    set_github_pat,
    set_openrouter_key,
    set_openrouter_mgmt_key,
)
from .error_dialog import reveal_path
from .logging_setup import log_path
from .providers.claude import CLAUDE_USAGE_URL
from .providers.codex import CODEX_USAGE_URL
from .providers.opencode_go import OPENCODE_GO_USAGE_URL
from .startup import set_start_at_login
from .webview.cookies import clear_browser_session

log = logging.getLogger("aigauge.settings_dialog")

_COPILOT_PLAN_QUOTAS = (
    ("Pro", 1500),
    ("Pro+", 7000),
    ("Max", 20000),
    ("Business", 1900),
    ("Enterprise", 3900),
    ("Business promo", 3000),
    ("Enterprise promo", 7000),
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
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: #111827;
    color: #f3f4f6;
    border: 1px solid #374151;
    border-radius: 4px;
    padding: 4px 6px;
    selection-background-color: #2563eb;
    min-height: 22px;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border-color: #3b82f6;
}
QComboBox::drop-down {
    width: 20px;
    border: none;
    border-left: 1px solid #374151;
    background: transparent;
}
QComboBox::drop-down:hover {
    background: #374151;
}
QComboBox::down-arrow {
    image: url("__DOWN_ARROW__");
    width: 11px;
    height: 11px;
}
QComboBox QAbstractItemView {
    background: #111827;
    color: #f3f4f6;
    selection-background-color: #2563eb;
}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    width: 18px;
    background: transparent;
    border-left: 1px solid #374151;
}
QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    height: 12px;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    height: 12px;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {
    background: #374151;
}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
    image: url("__UP_ARROW__");
    width: 9px;
    height: 9px;
}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    image: url("__DOWN_ARROW__");
    width: 9px;
    height: 9px;
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


def _chevron_png_path(direction: str) -> str:
    """Render a chevron arrow and cache it on disk for QSS `image: url(...)`.

    Styling QComboBox::drop-down / QSpinBox buttons without supplying an arrow
    image leaves Qt drawing an empty grey block (the "bad" indicators), so we
    draw our own light chevron that reads cleanly on the dark field. Rendered
    larger than displayed so it stays crisp when the whole UI is scaled up.
    """
    src = 48
    pixmap = QPixmap(src, src)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#cbd5e1"))
    pen.setWidthF(src * 0.10)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    left, mid_x, right = src * 0.30, src * 0.5, src * 0.70
    if direction == "down":
        outer_y, inner_y = src * 0.42, src * 0.60
    else:
        outer_y, inner_y = src * 0.58, src * 0.40
    painter.drawPolyline(
        QPolygonF(
            [
                QPointF(left, outer_y),
                QPointF(mid_x, inner_y),
                QPointF(right, outer_y),
            ]
        )
    )
    painter.end()

    cache_dir = app_data_dir() / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"settings-chevron-{direction}.png"
    pixmap.save(str(path), "PNG")
    return path.as_posix()


def _build_stylesheet() -> str:
    """Inject the cached chevron paths into the static dark stylesheet."""
    return _DARK_STYLESHEET.replace(
        "__DOWN_ARROW__", _chevron_png_path("down")
    ).replace("__UP_ARROW__", _chevron_png_path("up"))


def _hint_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("hint", True)
    label.setOpenExternalLinks(True)
    label.setWordWrap(True)
    return label


def _open_in_browser(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


class _ColorThresholdEditor(QWidget):
    def __init__(self, colors: ColorThresholds, parent=None):
        super().__init__(parent)
        self.green = QSpinBox()
        self.yellow = QSpinBox()
        self.orange = QSpinBox()
        for spin in (self.green, self.yellow, self.orange):
            spin.setRange(0, 100)
            spin.setSuffix("%")
            spin.setFixedWidth(68)
        self.green.setValue(colors.green_max)
        self.yellow.setValue(colors.yellow_max)
        self.orange.setValue(colors.orange_max)
        self._colors = {
            "green": colors.green_color,
            "yellow": colors.yellow_color,
            "orange": colors.orange_color,
            "red": colors.red_color,
        }
        self._color_buttons: dict[str, QPushButton] = {}
        self.green.valueChanged.connect(self._sync_ranges)
        self.yellow.valueChanged.connect(self._sync_ranges)
        self.orange.valueChanged.connect(self._sync_ranges)

        limits = QHBoxLayout()
        limits.setContentsMargins(0, 0, 0, 0)
        limits.setSpacing(6)
        for label, spin in (
            ("First cutoff", self.green),
            ("Second", self.yellow),
            ("Third", self.orange),
        ):
            text = QLabel(label)
            text.setStyleSheet("color:#9ca3af; font-size:10px;")
            limits.addWidget(text)
            limits.addWidget(spin)
        limits.addStretch(1)

        bands = QHBoxLayout()
        bands.setContentsMargins(0, 0, 0, 0)
        bands.setSpacing(6)
        for key in ("green", "yellow", "orange", "red"):
            button = QPushButton()
            button.setFixedHeight(26)
            button.clicked.connect(
                lambda _checked=False, name=key: self._choose_color(name)
            )
            self._color_buttons[key] = button
            bands.addWidget(button, 1)
        reset = QPushButton("Reset defaults")
        reset.setToolTip("Restore the supplied green, yellow, orange, and red defaults.")
        reset.clicked.connect(self.reset_defaults)
        bands.addWidget(reset)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addLayout(limits)
        layout.addLayout(bands)
        self._sync_ranges()

    def _sync_ranges(self) -> None:
        self.green.setMaximum(max(0, self.yellow.value() - 1))
        self.yellow.setMinimum(min(100, self.green.value() + 1))
        self.yellow.setMaximum(max(self.yellow.minimum(), self.orange.value() - 1))
        self.orange.setMinimum(min(100, self.yellow.value() + 1))
        self._refresh_band_buttons()

    def _refresh_band_buttons(self) -> None:
        ranges = {
            "green": f"0–{self.green.value()}%",
            "yellow": f"{self.green.value() + 1}–{self.yellow.value()}%",
            "orange": f"{self.yellow.value() + 1}–{self.orange.value()}%",
            "red": f"{self.orange.value() + 1}%+",
        }
        for key, button in self._color_buttons.items():
            color = QColor(self._colors[key])
            foreground = "#111827" if color.lightness() > 150 else "#f9fafb"
            button.setText(f"{key.title()} · {ranges[key]}")
            button.setStyleSheet(
                f"QPushButton {{ background:{color.name()}; color:{foreground}; "
                "border:1px solid #4b5563; border-radius:4px; padding:3px 6px; }}"
            )

    def _choose_color(self, key: str) -> None:
        chosen = QColorDialog.getColor(
            QColor(self._colors[key]),
            self,
            f"Choose {key} gauge color",
        )
        if chosen.isValid():
            self._colors[key] = chosen.name()
            self._refresh_band_buttons()

    def reset_defaults(self) -> None:
        defaults = ColorThresholds()
        # Raise the dependent maxima from the outside in so Qt does not clamp
        # a restored lower cutoff against the editor's current upper cutoffs.
        self.orange.setValue(defaults.orange_max)
        self.yellow.setValue(defaults.yellow_max)
        self.green.setValue(defaults.green_max)
        self._colors = {
            "green": defaults.green_color,
            "yellow": defaults.yellow_color,
            "orange": defaults.orange_color,
            "red": defaults.red_color,
        }
        self._sync_ranges()

    def value(self) -> ColorThresholds:
        return ColorThresholds(
            green_max=self.green.value(),
            yellow_max=self.yellow.value(),
            orange_max=self.orange.value(),
            green_color=self._colors["green"],
            yellow_color=self._colors["yellow"],
            orange_color=self._colors["orange"],
            red_color=self._colors["red"],
        )


class _ColorThresholdDialog(QDialog):
    def __init__(self, colors: ColorThresholds, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Gauge colors")
        self.setStyleSheet(_build_stylesheet())
        self.editor = _ColorThresholdEditor(colors, self)
        hint = _hint_label(
            "Adjust the three cutoffs, then click any colored range to choose "
            "its color. The supplied defaults remain available with Reset defaults."
        )
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.editor)
        layout.addWidget(hint)
        layout.addWidget(buttons)


class _ColorThresholdLauncher(QWidget):
    def __init__(self, colors: ColorThresholds, parent=None):
        super().__init__(parent)
        self.colors = colors.model_copy(deep=True)
        self.summary = QLabel()
        self.summary.setStyleSheet("color:#9ca3af; font-size:10px;")
        button = QPushButton("Customize…")
        button.setToolTip("Adjust gauge ranges and all four colors.")
        button.clicked.connect(self._edit)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(button)
        layout.addWidget(self.summary)
        layout.addStretch(1)
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        self.summary.setText(
            f"0–{self.colors.green_max} / "
            f"{self.colors.green_max + 1}–{self.colors.yellow_max} / "
            f"{self.colors.yellow_max + 1}–{self.colors.orange_max} / "
            f"{self.colors.orange_max + 1}%+"
        )

    def _edit(self) -> None:
        dialog = _ColorThresholdDialog(self.colors, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.colors = dialog.editor.value()
            self._refresh_summary()

    def value(self) -> ColorThresholds:
        return self.colors.model_copy(deep=True)


class _BrowserAccountRow(QWidget):
    sign_in_clicked = pyqtSignal(str)
    paste_cookie_clicked = pyqtSignal(str)
    clear_sign_in_clicked = pyqtSignal(str)
    remove_clicked = pyqtSignal(str)

    def __init__(
        self,
        account: BrowserAccount,
        *,
        removable: bool,
        parent=None,
    ):
        super().__init__(parent)
        self.account_id = account.id
        self.kind = account.kind
        self.colors = account.colors.model_copy(deep=True)

        self.name_edit = QLineEdit()
        self.name_edit.setText(account.name or "")
        self.name_edit.setPlaceholderText(
            "Default account"
            if account.id in ("claude", "codex", "opencode_go")
            else "Account name"
        )
        self.name_edit.setMinimumWidth(120)

        sign_in = QPushButton("Sign in")
        sign_in.setObjectName(f"{account.id}_signin_btn")
        sign_in.setFixedWidth(68)
        sign_in.setToolTip(
            "Sign in with your installed browser; Google and passkeys are supported."
        )
        sign_in.clicked.connect(lambda: self.sign_in_clicked.emit(self.account_id))

        paste = QPushButton("Paste cookie")
        paste.setObjectName(f"{account.id}_paste_cookie_btn")
        paste.setFixedWidth(92)
        paste.setToolTip("Paste a session cookie for this account.")
        paste.clicked.connect(lambda: self.paste_cookie_clicked.emit(self.account_id))

        clear = QPushButton("Clear sign-in")
        clear.setObjectName(f"{account.id}_clear_signin_btn")
        clear.setFixedWidth(92)
        clear.setToolTip("Remove this account's saved sign-in from AI Gauge.")
        clear.clicked.connect(lambda: self.clear_sign_in_clicked.emit(self.account_id))

        colors = QPushButton("Colors…")
        colors.setFixedWidth(68)
        colors.setToolTip("Set gauge color thresholds for this account.")
        colors.clicked.connect(self._edit_colors)

        remove = QPushButton("Remove")
        remove.setFixedWidth(72)
        remove.setVisible(removable)
        remove.setToolTip("Remove this account and clear its saved cookie.")
        remove.clicked.connect(lambda: self.remove_clicked.emit(self.account_id))

        account_actions = QHBoxLayout()
        account_actions.setContentsMargins(0, 0, 0, 0)
        account_actions.setSpacing(6)
        account_actions.addWidget(self.name_edit, 1)
        account_actions.addWidget(sign_in)
        account_actions.addWidget(paste)
        account_actions.addWidget(clear)
        account_actions.addWidget(colors)
        account_actions.addWidget(remove)

        self.usage_url_edit: QLineEdit | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addLayout(account_actions)
        if account.kind == "opencode_go":
            self.usage_url_edit = QLineEdit()
            self.usage_url_edit.setText(account.usage_url or OPENCODE_GO_USAGE_URL)
            self.usage_url_edit.setPlaceholderText(OPENCODE_GO_USAGE_URL)
            self.usage_url_edit.setToolTip(
                "The workspace Go usage page for this OpenCode subscription."
            )
            open_usage = QPushButton("Open usage")
            open_usage.setObjectName(f"{account.id}_open_usage_btn")
            open_usage.setFixedWidth(92)
            open_usage.clicked.connect(
                lambda _checked=False: _open_in_browser(
                    self.usage_url_edit.text().strip() or OPENCODE_GO_USAGE_URL
                )
            )
            usage_row = QHBoxLayout()
            usage_row.setContentsMargins(0, 0, 0, 0)
            usage_row.setSpacing(6)
            usage_row.addWidget(QLabel("Usage URL:"))
            usage_row.addWidget(self.usage_url_edit, 1)
            usage_row.addWidget(open_usage)
            layout.addLayout(usage_row)

    def _edit_colors(self) -> None:
        dialog = _ColorThresholdDialog(self.colors, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.colors = dialog.editor.value()

    def to_account(self) -> BrowserAccount:
        name = self.name_edit.text().strip() or None
        usage_url = None
        if self.usage_url_edit is not None:
            usage_url = self.usage_url_edit.text().strip() or OPENCODE_GO_USAGE_URL
        return BrowserAccount(
            id=self.account_id,
            kind=self.kind,
            name=name,
            enabled=True,
            colors=self.colors.model_copy(deep=True),
            usage_url=usage_url,
        )


class SettingsDialog(QDialog):
    sign_in_clicked = pyqtSignal(str)  # provider name
    paste_cookie_clicked = pyqtSignal(str)  # provider name
    clear_sign_in_clicked = pyqtSignal(str)  # account id

    def __init__(self, config: Config, parent=None):
        # Don't pass parent — avoids any cascading stylesheet issues.
        # Keep window centered relative to parent manually if needed later.
        super().__init__(None)
        # Intentionally NOT stays-on-top or app-modal: users may need to switch
        # to their normal browser, and clicking the status panel should bring
        # this existing Settings window back to the foreground.
        self.setWindowTitle("AI Gauge — Settings")
        self.setModal(False)
        self.resize(620, 520)
        self.setMinimumSize(560, 420)
        self.setStyleSheet(_build_stylesheet())
        self._config = config
        self._browser_account_rows: list[_BrowserAccountRow] = []
        self._removed_browser_account_ids: list[str] = []
        self.browser_session_clear_errors: list[tuple[str, str]] = []
        self._browser_accounts = [
            account.model_copy(deep=True) for account in browser_accounts(config)
        ]

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

        self.fade_when_inactive_cb = QCheckBox("Fade when inactive")
        self.fade_when_inactive_cb.setChecked(config.window.fade_when_inactive)
        self.fade_when_inactive_cb.setToolTip(
            "Fade the widget when it is not focused and the mouse is away."
        )
        general_grid.addWidget(self.fade_when_inactive_cb, 2, 0, 1, 4)

        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(30, 100)
        self.opacity_slider.setValue(int(config.window.opacity * 100))
        self.opacity_slider.setEnabled(config.window.fade_when_inactive)
        self.opacity_slider.setToolTip("Opacity used while the widget is faded.")
        self.opacity_value = QLabel(f"{int(config.window.opacity * 100)}%")
        self.opacity_value.setMinimumWidth(40)
        self.opacity_slider.valueChanged.connect(
            lambda v: self.opacity_value.setText(f"{v}%")
        )
        self.fade_when_inactive_cb.toggled.connect(self.opacity_slider.setEnabled)
        op_row = QHBoxLayout()
        op_row.setContentsMargins(0, 0, 0, 0)
        op_row.addWidget(self.opacity_slider, 1)
        op_row.addWidget(self.opacity_value)
        general_grid.addWidget(QLabel("Faded opacity:"), 3, 0)
        general_grid.addLayout(op_row, 3, 1, 1, 3)

        self.ui_scale_changed = False
        self.start_at_login_error = False
        self._initial_ui_scale = float(getattr(config.window, "ui_scale", 1.0))
        self.ui_scale_combo = QComboBox()
        self.ui_scale_combo.setMinimumWidth(110)
        self.ui_scale_combo.setToolTip(
            "Enlarge the whole widget, e.g. on a high-resolution (4K) display. "
            "Takes effect after restarting AI Gauge."
        )
        scale_values = [0.75, 0.9, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0]
        if self._initial_ui_scale not in scale_values:
            scale_values = sorted(set(scale_values) | {self._initial_ui_scale})
        for value in scale_values:
            self.ui_scale_combo.addItem(f"{round(value * 100)}%", value)
        current_index = self.ui_scale_combo.findData(self._initial_ui_scale)
        if current_index >= 0:
            self.ui_scale_combo.setCurrentIndex(current_index)
        scale_hint = QLabel("applies after restart")
        scale_hint.setStyleSheet("color:#6b7280; font-size:11px; font-style:italic;")
        scale_row = QHBoxLayout()
        scale_row.setContentsMargins(0, 0, 0, 0)
        scale_row.addWidget(self.ui_scale_combo)
        scale_row.addWidget(scale_hint)
        scale_row.addStretch(1)
        general_grid.addWidget(QLabel("UI scale:"), 4, 0)
        general_grid.addLayout(scale_row, 4, 1, 1, 3)

        # ----- Providers -----
        providers = QGroupBox("Providers")
        providers_layout = QVBoxLayout(providers)
        providers_layout.setSpacing(8)

        providers_hint = _hint_label(
            "Show or hide provider groups in the widget. Manage multiple Claude, "
            "Codex, or OpenCode accounts from their tabs."
        )
        providers_layout.addWidget(providers_hint)

        self.claude_cb = QCheckBox("Claude")
        self.claude_cb.setToolTip("Show Claude accounts in the panel.")
        self.claude_cb.setChecked(config.providers.claude)
        providers_layout.addWidget(self.claude_cb)

        self.codex_cb = QCheckBox("Codex")
        self.codex_cb.setToolTip("Show Codex accounts in the panel.")
        self.codex_cb.setChecked(config.providers.codex)
        providers_layout.addWidget(self.codex_cb)

        self.opencode_go_cb = QCheckBox("OpenCode")
        self.opencode_go_cb.setToolTip("Show OpenCode accounts in the panel.")
        self.opencode_go_cb.setChecked(config.providers.opencode_go)
        providers_layout.addWidget(self.opencode_go_cb)

        self.copilot_cb = QCheckBox("GitHub Copilot")
        self.copilot_cb.setToolTip("Show the GitHub Copilot usage tile in the panel.")
        self.copilot_cb.setChecked(config.providers.copilot)
        providers_layout.addWidget(self.copilot_cb)

        self.openrouter_cb = QCheckBox("OpenRouter")
        self.openrouter_cb.setToolTip("Show the OpenRouter usage tile in the panel.")
        self.openrouter_cb.setChecked(config.providers.openrouter)
        providers_layout.addWidget(self.openrouter_cb)

        # ----- Claude accounts -----
        claude_accounts = QGroupBox("Claude Accounts")
        claude_accounts_layout = QVBoxLayout(claude_accounts)
        claude_accounts_layout.setSpacing(8)
        claude_accounts_layout.addWidget(
            _hint_label(
                "Name each Claude subscription here. All accounts appear when "
                "Claude is enabled on the General tab. <b>Sign in</b> opens a "
                "real installed browser and supports Google and passkeys; the "
                "session is connected automatically."
            )
        )
        claude_usage_btn = QPushButton("Open usage in browser")
        claude_usage_btn.setObjectName("claude_open_usage_btn")
        claude_usage_btn.setToolTip("Open Claude's usage page in your default browser.")
        claude_usage_btn.clicked.connect(
            lambda _checked=False: _open_in_browser(CLAUDE_USAGE_URL)
        )
        claude_accounts_layout.addWidget(
            claude_usage_btn,
            alignment=Qt.AlignmentFlag.AlignLeft,
        )
        self._claude_accounts_layout = QVBoxLayout()
        self._claude_accounts_layout.setSpacing(6)
        claude_accounts_layout.addLayout(self._claude_accounts_layout)

        # ----- Codex accounts -----
        codex_accounts = QGroupBox("Codex Accounts")
        codex_accounts_layout = QVBoxLayout(codex_accounts)
        codex_accounts_layout.setSpacing(8)
        codex_accounts_layout.addWidget(
            _hint_label(
                "Name each Codex subscription here. All accounts appear when "
                "Codex is enabled on the General tab. <b>Sign in</b> opens a "
                "real installed browser and supports Google and passkeys; the "
                "session is connected automatically."
            )
        )
        codex_usage_btn = QPushButton("Open usage in browser")
        codex_usage_btn.setObjectName("codex_open_usage_btn")
        codex_usage_btn.setToolTip("Open Codex's usage page in your default browser.")
        codex_usage_btn.clicked.connect(
            lambda _checked=False: _open_in_browser(CODEX_USAGE_URL)
        )
        codex_accounts_layout.addWidget(
            codex_usage_btn,
            alignment=Qt.AlignmentFlag.AlignLeft,
        )
        self._codex_accounts_layout = QVBoxLayout()
        self._codex_accounts_layout.setSpacing(6)
        codex_accounts_layout.addLayout(self._codex_accounts_layout)

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
        self.clear_pat_cb.setToolTip("Remove the token from the system keychain.")
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
        copilot_form.addRow("Plan / credits:", self.gh_plan)

        self.gh_quota = QSpinBox()
        self.gh_quota.setRange(1, 100000)
        self.gh_quota.setValue(config.copilot.monthly_quota)
        self.gh_quota.setMinimumWidth(110)
        self.gh_quota_label = QLabel("Custom credits:")
        copilot_form.addRow(self.gh_quota_label, self.gh_quota)
        self._set_quota_selection(config.copilot.monthly_quota)

        quota_hint = _hint_label(
            "GitHub reports usage, not a reliable personal-plan allowance through "
            "the API; choose your plan here or Custom for a different monthly "
            "AI credit allowance."
        )
        copilot_form.addRow("", quota_hint)

        self.copilot_colors = _ColorThresholdLauncher(config.copilot.colors)
        copilot_form.addRow("Gauge colors:", self.copilot_colors)

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
        self.or_daily_budget.setValue(float(config.openrouter.daily_budget or 0.0))
        openrouter_form.addRow("Daily budget:", self.or_daily_budget)

        budget_hint = _hint_label(
            "Optional. If set, the Daily row shows a colored gauge against this "
            "budget. Leave at $0.00 to show only the dollar amount."
        )
        openrouter_form.addRow("", budget_hint)

        self.openrouter_colors = _ColorThresholdLauncher(config.openrouter.colors)
        openrouter_form.addRow("Gauge colors:", self.openrouter_colors)

        # ----- OpenCode accounts -----
        opencode_go = QGroupBox("OpenCode Accounts")
        opencode_go_layout = QVBoxLayout(opencode_go)
        opencode_go_layout.setSpacing(8)
        opencode_go_layout.addWidget(
            _hint_label(
                "Name each OpenCode subscription and paste its workspace <b>Go</b> "
                "usage-page URL. Every row has an independent browser session, "
                "tile, and gauge colors. <b>Sign in</b> opens a real installed "
                "browser and connects that subscription automatically."
            )
        )
        self._opencode_go_accounts_layout = QVBoxLayout()
        self._opencode_go_accounts_layout.setSpacing(8)
        opencode_go_layout.addLayout(self._opencode_go_accounts_layout)
        self._rebuild_browser_account_rows()
        general_tab = QWidget()
        general_tab_layout = QVBoxLayout(general_tab)
        general_tab_layout.setContentsMargins(10, 10, 10, 10)
        general_tab_layout.setSpacing(10)
        general_tab_layout.addWidget(general)
        general_tab_layout.addWidget(providers)
        general_tab_layout.addStretch(1)

        claude_tab = QWidget()
        claude_tab_layout = QVBoxLayout(claude_tab)
        claude_tab_layout.setContentsMargins(10, 10, 10, 10)
        claude_tab_layout.setSpacing(10)
        claude_tab_layout.addWidget(claude_accounts)
        claude_tab_layout.addStretch(1)

        codex_tab = QWidget()
        codex_tab_layout = QVBoxLayout(codex_tab)
        codex_tab_layout.setContentsMargins(10, 10, 10, 10)
        codex_tab_layout.setSpacing(10)
        codex_tab_layout.addWidget(codex_accounts)
        codex_tab_layout.addStretch(1)

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

        opencode_go_tab = QWidget()
        opencode_go_tab_layout = QVBoxLayout(opencode_go_tab)
        opencode_go_tab_layout.setContentsMargins(10, 10, 10, 10)
        opencode_go_tab_layout.setSpacing(10)
        opencode_go_tab_layout.addWidget(opencode_go)
        opencode_go_tab_layout.addStretch(1)

        tabs = QTabWidget()
        tabs.addTab(general_tab, "General")
        tabs.addTab(claude_tab, "Claude")
        tabs.addTab(codex_tab, "Codex")
        tabs.addTab(opencode_go_tab, "OpenCode")
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

    def _new_account_id(self, kind: str) -> str:
        existing = {account.id for account in self._browser_accounts}
        while True:
            account_id = f"{kind}-{uuid.uuid4().hex[:8]}"
            if account_id not in existing:
                return account_id

    def _next_account_name(self, kind: str) -> str:
        count = sum(1 for account in self._browser_accounts if account.kind == kind)
        return f"Account {count + 1}"

    def _add_browser_account(self, kind: str) -> None:
        self._browser_accounts = self._current_browser_accounts()
        self._browser_accounts.append(
            BrowserAccount(
                id=self._new_account_id(kind),
                kind=kind,
                name=self._next_account_name(kind),
                enabled=True,
                usage_url=(
                    OPENCODE_GO_USAGE_URL if kind == "opencode_go" else None
                ),
            )
        )
        self._rebuild_browser_account_rows()

    def _remove_browser_account(self, account_id: str) -> None:
        self._browser_accounts = [
            account
            for account in self._current_browser_accounts()
            if account.id != account_id
        ]
        self._removed_browser_account_ids.append(account_id)
        self._rebuild_browser_account_rows()

    def _rebuild_browser_account_rows(self) -> None:
        for layout in (
            self._claude_accounts_layout,
            self._codex_accounts_layout,
            self._opencode_go_accounts_layout,
        ):
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
        self._browser_account_rows = []
        for kind, layout in (
            ("claude", self._claude_accounts_layout),
            ("codex", self._codex_accounts_layout),
            ("opencode_go", self._opencode_go_accounts_layout),
        ):
            for account in [a for a in self._browser_accounts if a.kind == kind]:
                row = _BrowserAccountRow(
                    account,
                    removable=True,
                )
                row.sign_in_clicked.connect(self.sign_in_clicked.emit)
                row.paste_cookie_clicked.connect(self.paste_cookie_clicked.emit)
                row.clear_sign_in_clicked.connect(self.clear_sign_in_clicked.emit)
                row.remove_clicked.connect(self._remove_browser_account)
                self._browser_account_rows.append(row)
                layout.addWidget(row)
            add_btn = QPushButton(f"Add another {provider_base_name(kind)}")
            add_btn.clicked.connect(
                lambda _checked=False, k=kind: self._add_browser_account(k)
            )
            add_btn.setFixedWidth(150)
            layout.addWidget(add_btn, alignment=Qt.AlignmentFlag.AlignLeft)

    def _current_browser_accounts(self) -> list[BrowserAccount]:
        return [row.to_account() for row in self._browser_account_rows]

    def draft_browser_account(self, account_id: str) -> BrowserAccount | None:
        return next(
            (
                row.to_account()
                for row in self._browser_account_rows
                if row.account_id == account_id
            ),
            None,
        )

    def _validate_browser_accounts(self) -> bool:
        seen: set[tuple[str, str]] = set()
        for account in self._current_browser_accounts():
            key = (account.kind, account_display_name(account).lower())
            if key in seen:
                QMessageBox.warning(
                    self,
                    "Duplicate account name",
                    f"Each {provider_base_name(account.kind)} account needs a unique display name.",
                )
                return False
            seen.add(key)
        return True

    def _accept(self) -> None:
        if not self._validate_browser_accounts():
            return
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
        config.window.fade_when_inactive = self.fade_when_inactive_cb.isChecked()
        config.window.opacity = self.opacity_slider.value() / 100.0
        new_ui_scale = float(self.ui_scale_combo.currentData())
        self.ui_scale_changed = abs(new_ui_scale - self._initial_ui_scale) > 1e-3
        config.window.ui_scale = new_ui_scale
        accounts = self._current_browser_accounts()
        config.browser_accounts = accounts
        config.providers.claude = self.claude_cb.isChecked()
        config.providers.codex = self.codex_cb.isChecked()
        config.providers.copilot = self.copilot_cb.isChecked()
        config.providers.openrouter = self.openrouter_cb.isChecked()
        config.providers.opencode_go = self.opencode_go_cb.isChecked()
        for account_id in self._removed_browser_account_ids:
            try:
                clear_browser_session(account_id)
            except RuntimeError as exc:
                log.exception(
                    "failed to clear removed browser account session provider=%s",
                    account_id,
                )
                self.browser_session_clear_errors.append((account_id, str(exc)))
        username = self.gh_username.text().strip()
        config.copilot.username = username or None
        billing_org = self.gh_billing_org.text().strip()
        config.copilot.billing_org = billing_org or None
        selected_quota = self.gh_plan.currentData()
        config.copilot.monthly_quota = (
            int(selected_quota) if selected_quota is not None else self.gh_quota.value()
        )
        config.copilot.colors = self.copilot_colors.value()
        budget = self.or_daily_budget.value()
        config.openrouter.daily_budget = budget if budget > 0 else None
        config.openrouter.colors = self.openrouter_colors.value()
        # Keep the legacy singleton fields synchronized for downgrade safety;
        # account rows are the source of truth from schema version 2 onward.
        first_opencode = next(
            (account for account in accounts if account.kind == "opencode_go"),
            None,
        )
        if first_opencode is not None:
            config.opencode_go.usage_url = (
                first_opencode.usage_url or OPENCODE_GO_USAGE_URL
            )
            config.opencode_go.colors = first_opencode.colors.model_copy(deep=True)
        # Persist all settings first: wiring up OS autostart can fail (e.g. a
        # rejected Task Scheduler entry), and that must neither lose the user's
        # other changes nor crash the app via an exception escaping this slot.
        config.save()
        self.start_at_login_error = False
        try:
            set_start_at_login(config.start_at_login)
        except Exception:  # noqa: BLE001
            log.exception("failed to update start-at-login")
            self.start_at_login_error = True
