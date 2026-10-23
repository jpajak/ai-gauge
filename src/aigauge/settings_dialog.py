from __future__ import annotations

import logging
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QDir, QEvent, QPoint, QPointF, QRect, QRectF, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QDesktopServices,
    QDragMoveEvent,
    QDropEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QFrame,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .config import (
    BrowserAccount,
    ColorThresholds,
    Config,
    DEFAULT_PROVIDER_ORDER,
    account_display_name,
    app_data_dir,
    browser_accounts,
    get_account_api_key,
    get_github_pat,
    get_openrouter_key,
    get_openrouter_mgmt_key,
    provider_base_name,
    provider_order as config_provider_order,
    set_account_api_key,
    set_github_pat,
    set_openrouter_key,
    set_openrouter_mgmt_key,
)
from .error_dialog import reveal_path
from .icons import app_icon
from .local_usage.settings_panel import LocalUsagePanel
from .logging_setup import log_path
# The usage buttons read spec.usage_url; these stay importable from here for
# callers and tests that name the provider constants.
from .providers.claude import CLAUDE_USAGE_URL  # noqa: F401
from .providers.codex import CODEX_USAGE_URL  # noqa: F401
from .providers.minimax import MINIMAX_USAGE_URLS
from .providers.registry import (
    PROVIDER_SPECS,
    ProviderSpec,
    account_kinds,
    account_specs,
    provider_spec,
    single_specs,
)
from .providers.antigravity import (
    is_agy_file_name,
    normalize_cli_path,
    resolve_cli,
)
from .startup import set_start_at_login
from .webview.cookies import clear_browser_session  # lazy inside; no WebEngine at import

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

_SIGN_IN_BROWSER_CHOICES = (
    ("Ask on next sign-in", "ask"),
    ("Google Chrome", "chrome"),
    ("Microsoft Edge", "edge"),
    ("Brave", "brave"),
    ("Chromium", "chromium"),
    ("Embedded browser", "embedded"),
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
QListWidget#settings_nav {
    background: #111827;
    color: #cbd5e1;
    border: 1px solid #374151;
    border-radius: 6px;
    padding: 4px;
    outline: none;
}
QListWidget#settings_nav::item {
    padding: 4px 10px;
    border-radius: 4px;
}
QListWidget#settings_nav::item:selected {
    background: #2563eb;
    color: #f9fafb;
}
QListWidget#settings_nav::item:hover:!selected {
    background: #1f2937;
}
QStackedWidget#settings_pages {
    border: 1px solid #374151;
    border-radius: 6px;
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


_MINIMAX_REGION_CHOICES = (
    ("International (api.minimax.io)", "international"),
    ("China (api.minimax.cn)", "china"),
)


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
        self.region = account.region
        self.api_key_edit: QLineEdit | None = None
        self.clear_api_key_cb: QCheckBox | None = None
        self._had_existing_api_key = False

        self.name_edit = QLineEdit()
        self.name_edit.setText(account.name or "")
        # Each kind's default account has the kind as its id.
        self.name_edit.setPlaceholderText(
            "Default account"
            if account.id in account_kinds()
            else "Account name"
        )
        self.name_edit.setMinimumWidth(120)

        colors = QPushButton("Colors…")
        colors.setFixedWidth(68)
        colors.setToolTip("Set gauge color thresholds for this account.")
        colors.clicked.connect(self._edit_colors)

        remove = QPushButton("Remove")
        remove.setFixedWidth(72)
        remove.setVisible(removable)
        remove.setToolTip("Remove this account and its saved credentials.")
        remove.clicked.connect(lambda: self.remove_clicked.emit(self.account_id))

        account_actions = QHBoxLayout()
        account_actions.setContentsMargins(0, 0, 0, 0)
        account_actions.setSpacing(6)
        account_actions.addWidget(self.name_edit, 1)
        spec = provider_spec(account.kind)
        is_api_key = spec is not None and spec.auth == "api_key"
        if not is_api_key:
            sign_in = QPushButton("Sign in")
            sign_in.setObjectName(f"{account.id}_signin_btn")
            sign_in.setFixedWidth(68)
            sign_in.setToolTip(
                "Sign in with your installed browser; Google and passkeys are supported."
            )
            sign_in.clicked.connect(
                lambda: self.sign_in_clicked.emit(self.account_id)
            )
            account_actions.addWidget(sign_in)

            if spec is not None and spec.paste_cookie:
                paste = QPushButton("Paste cookie")
                paste.setObjectName(f"{account.id}_paste_cookie_btn")
                paste.setFixedWidth(92)
                paste.setToolTip("Paste a session cookie for this account.")
                paste.clicked.connect(
                    lambda: self.paste_cookie_clicked.emit(self.account_id)
                )
                account_actions.addWidget(paste)

            clear = QPushButton("Clear sign-in")
            clear.setObjectName(f"{account.id}_clear_signin_btn")
            clear.setFixedWidth(92)
            clear.setToolTip("Remove this account's saved sign-in from AI Gauge.")
            clear.clicked.connect(
                lambda: self.clear_sign_in_clicked.emit(self.account_id)
            )
            account_actions.addWidget(clear)
        account_actions.addWidget(colors)
        account_actions.addWidget(remove)

        self.fable_cb: QCheckBox | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addLayout(account_actions)
        if account.kind == "claude":
            self.fable_cb = QCheckBox("Show weekly Fable limit")
            self.fable_cb.setObjectName(f"{account.id}_fable_cb")
            self.fable_cb.setChecked(account.show_fable)
            self.fable_cb.setToolTip(
                "Add this account's separate weekly Fable limit as its own "
                "gauge. Only Max plans publish that limit; enabling it on "
                "another plan has no effect."
            )
            fable_row = QHBoxLayout()
            fable_row.setContentsMargins(12, 0, 0, 0)
            fable_row.setSpacing(6)
            fable_row.addWidget(self.fable_cb)
            fable_row.addStretch(1)
            layout.addLayout(fable_row)
        self.region_combo: QComboBox | None = None
        if account.kind == "minimax":
            self.region_combo = QComboBox()
            self.region_combo.setObjectName(f"{account.id}_region_combo")
            for label, value in _MINIMAX_REGION_CHOICES:
                self.region_combo.addItem(label, value)
            index = self.region_combo.findData(account.region)
            self.region_combo.setCurrentIndex(max(index, 0))
            self.region_combo.setToolTip(
                "The API key is sent only to this region's MiniMax host."
            )
            region_row = QHBoxLayout()
            region_row.setContentsMargins(0, 0, 0, 0)
            region_row.setSpacing(6)
            region_row.addWidget(QLabel("Region:"))
            region_row.addWidget(self.region_combo)
            region_row.addStretch(1)
            layout.addLayout(region_row)
        if is_api_key:
            self.api_key_edit = QLineEdit()
            self.api_key_edit.setObjectName(f"{account.id}_api_key_edit")
            self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self._had_existing_api_key = bool(
                get_account_api_key(account.kind, account.id)
            )
            self.api_key_edit.setPlaceholderText(
                "•••••••••• (saved — leave blank to keep)"
                if self._had_existing_api_key
                else spec.api_key_placeholder
            )
            self.api_key_edit.setToolTip(
                f"{spec.display_name} API key for this subscription. The key "
                "is stored in the system keychain."
            )
            key_row = QHBoxLayout()
            key_row.setContentsMargins(0, 0, 0, 0)
            key_row.setSpacing(6)
            key_row.addWidget(QLabel("API key:"))
            key_row.addWidget(self.api_key_edit, 1)
            self.clear_api_key_cb = QCheckBox("Clear saved key")
            self.clear_api_key_cb.setObjectName(f"{account.id}_clear_api_key_cb")
            self.clear_api_key_cb.setVisible(self._had_existing_api_key)
            self.clear_api_key_cb.setToolTip(
                "Remove this account's API key from the system keychain."
            )
            key_row.addWidget(self.clear_api_key_cb)
            layout.addLayout(key_row)

    def _edit_colors(self) -> None:
        dialog = _ColorThresholdDialog(self.colors, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.colors = dialog.editor.value()

    def to_account(self) -> BrowserAccount:
        name = self.name_edit.text().strip() or None
        return BrowserAccount(
            id=self.account_id,
            kind=self.kind,
            name=name,
            enabled=True,
            colors=self.colors.model_copy(deep=True),
            show_fable=self.fable_cb is not None and self.fable_cb.isChecked(),
            region=(
                self.region_combo.currentData()
                if self.region_combo is not None
                else self.region
            ),
        )


_NAV_WIDTH = 150
# (key, label) rows; (None, text) is a caption row that cannot be selected.
_NAV_ENTRIES: tuple[tuple[str | None, str], ...] = (
    ("general", "General"),
    (None, "PROVIDERS"),
    *((spec.id, spec.settings_label) for spec in PROVIDER_SPECS),
    (None, "TOOLS"),
    ("mcp", "MCP"),
    ("local_usage", "Local usage"),
)
_PROVIDER_PAGE_KEYS = frozenset(spec.id for spec in PROVIDER_SPECS)
_PROVIDER_LABELS = {
    key: label for key, label in _NAV_ENTRIES if key in _PROVIDER_PAGE_KEYS
}
_NAV_DIM_COLOR = "#6b7280"
# Dot colours of the drag grip on a provider row, by row state.
_NAV_GRIP_COLOR = "#6b7280"
_NAV_GRIP_HOVER_COLOR = "#cbd5e1"
_NAV_GRIP_SELECTED_COLOR = "#bfdbfe"
# The grip is 2 columns by 3 rows of dots, centres 4 px apart.
_NAV_GRIP_DOT_RADIUS = 1.1
_NAV_GRIP_SPACING = 4
_NAV_GRIP_SIZE = (7, 11)  # width, height of the dots' bounding box, rounded out
_NAV_GRIP_RIGHT_INSET = 12  # grip's right edge, inside the row's right edge
_NAV_GRIP_ZONE_WIDTH = 28  # rightmost part of a row that shows the hand cursor
_NAV_REORDER_TIP = "Drag to reorder, or press Alt+Up / Alt+Down."


def _add_provider_enable_row(layout: QVBoxLayout, checkbox: QCheckBox, kind: str) -> None:
    """Put a provider's enable checkbox and its hint at the top of its page."""
    layout.addWidget(checkbox)
    layout.addWidget(
        _hint_label(
            f"When off, AI Gauge does not poll {_PROVIDER_LABELS[kind]} and hides "
            "it from the widget. Drag providers in the sidebar to change their "
            "order."
        )
    )


def _nav_grip_rect(row_rect: QRect) -> QRect:
    """Bounding rect of the drag grip on a provider row whose rect is `row_rect`."""
    width, height = _NAV_GRIP_SIZE
    return QRect(
        row_rect.right() + 1 - _NAV_GRIP_RIGHT_INSET - width,
        row_rect.top() + (row_rect.height() - height) // 2,
        width,
        height,
    )


class _NavDelegate(QStyledItemDelegate):
    """Draws the stylesheet's row, then a drag grip on provider rows."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        super().paint(painter, option, index)
        if index.data(Qt.ItemDataRole.UserRole) not in _PROVIDER_PAGE_KEYS:
            return
        if option.state & QStyle.StateFlag.State_Selected:
            color = _NAV_GRIP_SELECTED_COLOR
        elif option.state & QStyle.StateFlag.State_MouseOver:
            color = _NAV_GRIP_HOVER_COLOR
        else:
            color = _NAV_GRIP_COLOR
        center = QRectF(_nav_grip_rect(option.rect)).center()
        half = _NAV_GRIP_SPACING / 2
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        for dx in (-half, half):
            for dy in (-_NAV_GRIP_SPACING, 0, _NAV_GRIP_SPACING):
                painter.drawEllipse(
                    QPointF(center.x() + dx, center.y() + dy),
                    _NAV_GRIP_DOT_RADIUS,
                    _NAV_GRIP_DOT_RADIUS,
                )
        painter.restore()


class _NavList(QListWidget):
    """The settings sidebar: provider rows can be dragged into a new order.

    Only provider rows are draggable and no row accepts a drop, so a drop
    always lands between rows. Qt's own InternalMove would remove and re-add
    the item; instead the drop is turned into `move_provider` and reported as
    ignored so Qt leaves the source row alone.
    """

    def __init__(self, move_provider: Callable[[str, int], None], parent=None):
        super().__init__(parent)
        self._move_provider = move_provider
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDropIndicatorShown(True)
        self.setItemDelegate(_NavDelegate(self))
        # Move events without a button held, for the grip's hand cursor.
        self.setMouseTracking(True)

    def _in_grip_zone(self, pos: QPoint) -> bool:
        item = self.itemAt(pos)
        if item is None or item.data(Qt.ItemDataRole.UserRole) not in _PROVIDER_PAGE_KEYS:
            return False
        return pos.x() > self.visualItemRect(item).right() - _NAV_GRIP_ZONE_WIDTH

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._in_grip_zone(event.position().toPoint()):
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.viewport().unsetCursor()
        super().mouseMoveEvent(event)

    def viewportEvent(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Leave:
            self.viewport().unsetCursor()
        return super().viewportEvent(event)

    def _row_key(self, row: int):
        return self.item(row).data(Qt.ItemDataRole.UserRole)

    def _provider_row_range(self) -> tuple[int, int] | None:
        rows = [
            row
            for row in range(self.count())
            if self._row_key(row) in _PROVIDER_PAGE_KEYS
        ]
        return (rows[0], rows[-1]) if rows else None

    def drop_position(self, pos: QPoint) -> int | None:
        """Where a drop at `pos` would insert, as a position among the providers.

        0 means before the first provider and the provider count means after
        the last. None when `pos` is outside the provider section.
        """
        bounds = self._provider_row_range()
        if bounds is None:
            return None
        first, last = bounds
        if (
            pos.y() < self.visualItemRect(self.item(first)).top()
            or pos.y() > self.visualItemRect(self.item(last)).bottom()
        ):
            return None
        item = self.itemAt(pos)
        if item is None:
            return None
        row = self.row(item)
        if pos.y() > self.visualItemRect(item).center().y():
            row += 1
        return row - first

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        super().dragMoveEvent(event)
        if self.drop_position(event.position().toPoint()) is None:
            event.ignore()
        else:
            event.accept()

    def dropEvent(self, event: QDropEvent) -> None:
        item = self.currentItem()
        kind = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        position = self.drop_position(event.position().toPoint())
        if kind in _PROVIDER_PAGE_KEYS and position is not None:
            bounds = self._provider_row_range()
            source = self.row(item) - bounds[0]
            # `position` counts the dragged row itself; the index is where it
            # ends up once it has been taken out.
            self._move_provider(kind, position - 1 if position > source else position)
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        # macOS reports the arrow keys with the keypad modifier set.
        modifiers = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if modifiers == Qt.KeyboardModifier.AltModifier and event.key() in (
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
        ):
            item = self.currentItem()
            kind = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            if kind in _PROVIDER_PAGE_KEYS:
                bounds = self._provider_row_range()
                step = -1 if event.key() == Qt.Key.Key_Up else 1
                self._move_provider(kind, self.row(item) - bounds[0] + step)
            event.accept()
            return
        super().keyPressEvent(event)


class SettingsDialog(QDialog):
    sign_in_clicked = pyqtSignal(str)  # provider name
    paste_cookie_clicked = pyqtSignal(str)  # provider name
    clear_sign_in_clicked = pyqtSignal(str)  # account id

    def __init__(self, config: Config, parent=None, local_usage_service=None):
        # Don't pass parent — avoids any cascading stylesheet issues.
        # Keep window centered relative to parent manually if needed later.
        super().__init__(None)
        # Intentionally NOT stays-on-top or app-modal: users may need to switch
        # to their normal browser, and clicking the status panel should bring
        # this existing Settings window back to the foreground.
        self.setWindowTitle("AI Gauge — Settings")
        self.setModal(False)
        # The layout minimum is the real floor (Claude is the widest page):
        # 150 px list + 10 gap + a page area as wide as a tab page was (about
        # 615 px) + margins. The height is what GitHub Copilot, the tallest
        # page, needs to show its wrapped hints in full.
        self.resize(800, 680)
        self.setMinimumSize(790, 420)
        self.setStyleSheet(_build_stylesheet())
        self._config = config
        self._browser_account_rows: list[_BrowserAccountRow] = []
        self._removed_browser_accounts: dict[str, str] = {}
        # Unsaved API-key field text and clear-checkbox state by account id,
        # kept across rebuilds of the account rows.
        self._api_key_drafts: dict[str, tuple[str, bool]] = {}
        self._account_layouts: dict[str, QVBoxLayout] = {}
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
        general_grid.addWidget(self.fade_when_inactive_cb, 2, 0, 1, 2)

        self.square_corners_cb = QCheckBox("Square corners")
        self.square_corners_cb.setChecked(config.window.square_corners)
        self.square_corners_cb.setToolTip(
            "Draw the widget with square corners instead of rounded ones. "
            "Useful on remote desktops that render the rounded outline poorly."
        )
        general_grid.addWidget(self.square_corners_cb, 2, 2, 1, 2)

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

        self.sign_in_browser_combo = QComboBox()
        self.sign_in_browser_combo.setMinimumWidth(180)
        self.sign_in_browser_combo.setToolTip(
            "Choose the isolated browser AI Gauge uses for Claude and Codex "
            "sign-in. Automatic import supports Chrome-family browsers; "
            "embedded sign-in may not support Google or passkeys."
        )
        for label, browser_id in _SIGN_IN_BROWSER_CHOICES:
            self.sign_in_browser_combo.addItem(label, browser_id)
        self.set_sign_in_browser(getattr(config, "sign_in_browser", "ask"))
        general_grid.addWidget(QLabel("Sign-in browser:"), 5, 0)
        general_grid.addWidget(self.sign_in_browser_combo, 5, 1, 1, 3)

        # ----- Providers -----
        # The enable checkboxes live on each provider's own page; they are
        # created here, ahead of the pages that hold them.
        # Each is also an attribute named after its spec: claude_cb, copilot_cb.
        self._provider_checkboxes: dict[str, QCheckBox] = {}
        for spec in PROVIDER_SPECS:
            checkbox = QCheckBox(f"Enable {spec.settings_label}")
            checkbox.setToolTip(
                f"Show {spec.display_name} accounts in the panel."
                if spec.multi_account
                else f"Show the {spec.settings_label} usage tile in the panel."
            )
            checkbox.setChecked(bool(getattr(config.providers, spec.id)))
            self._provider_checkboxes[spec.id] = checkbox
            setattr(self, f"{spec.id}_cb", checkbox)

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

        # ----- Antigravity details -----
        antigravity = QGroupBox("Antigravity")
        antigravity_form = QFormLayout(antigravity)
        antigravity_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        antigravity_form.setHorizontalSpacing(12)
        antigravity_form.setVerticalSpacing(8)
        antigravity_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        self.antigravity_show_gemini_cb = QCheckBox("Gemini models")
        self.antigravity_show_gemini_cb.setChecked(config.antigravity.show_gemini)
        antigravity_form.addRow("Show:", self.antigravity_show_gemini_cb)
        # "&&" is a literal ampersand; a single one is a mnemonic and vanishes.
        self.antigravity_show_third_party_cb = QCheckBox("Claude && GPT models")
        self.antigravity_show_third_party_cb.setChecked(
            config.antigravity.show_third_party
        )
        antigravity_form.addRow("", self.antigravity_show_third_party_cb)
        self.antigravity_cli_path_edit = QLineEdit(config.antigravity.cli_path or "")
        # Looked up once, when the dialog opens; the status line reuses it.
        try:
            self._antigravity_detected_cli = resolve_cli(None)
        except OSError:
            self._antigravity_detected_cli = None
        self.antigravity_cli_path_edit.setPlaceholderText(
            f"Auto-detect ({self._antigravity_detected_cli})"
            if self._antigravity_detected_cli
            else "Auto-detect"
        )
        cli_path_row = QWidget()
        cli_path_layout = QHBoxLayout(cli_path_row)
        cli_path_layout.setContentsMargins(0, 0, 0, 0)
        cli_path_layout.setSpacing(8)
        cli_path_layout.addWidget(self.antigravity_cli_path_edit, 1)
        self.antigravity_cli_browse_button = QPushButton("Browse…")
        self.antigravity_cli_browse_button.clicked.connect(
            self._browse_antigravity_cli
        )
        cli_path_layout.addWidget(self.antigravity_cli_browse_button)
        antigravity_form.addRow("agy CLI:", cli_path_row)
        self.antigravity_cli_path_status = QLabel()
        self.antigravity_cli_path_status.setWordWrap(True)
        self.antigravity_cli_path_status.setMaximumWidth(460)
        antigravity_form.addRow("", self.antigravity_cli_path_status)
        # The check touches the filesystem, so it runs when editing ends (and
        # after Browse), never per keystroke on the GUI thread.
        self.antigravity_cli_path_edit.editingFinished.connect(
            self._update_antigravity_cli_status
        )
        self.antigravity_cli_path_edit.textEdited.connect(
            self._mark_antigravity_cli_unchecked
        )
        self._update_antigravity_cli_status()
        antigravity_form.addRow(
            "",
            _hint_label(
                "Usage comes from the local Antigravity CLI (agy --print /quota). "
                "No account or API key is needed. Leave the path empty to "
                "auto-detect agy. A path you set must be a file named agy: that "
                "catches a mistyped or wrong file, it does not check what the "
                "program is."
            ),
        )
        self.antigravity_colors = _ColorThresholdLauncher(config.antigravity.colors)
        antigravity_form.addRow("Gauge colors:", self.antigravity_colors)

        # ----- Account pages (Claude, Codex, OpenCode, ...) -----
        provider_pages: dict[str, QWidget] = {
            spec.id: self._build_account_family_page(spec)
            for spec in account_specs()
        }
        self._rebuild_browser_account_rows()
        general_tab = QWidget()
        general_tab_layout = QVBoxLayout(general_tab)
        general_tab_layout.setContentsMargins(10, 10, 10, 10)
        general_tab_layout.setSpacing(10)
        general_tab_layout.addWidget(general)
        general_tab_layout.addStretch(1)

        # Single-instance providers keep bespoke pages, keyed by spec id.
        copilot_tab = QWidget()
        copilot_tab_layout = QVBoxLayout(copilot_tab)
        copilot_tab_layout.setContentsMargins(10, 10, 10, 10)
        copilot_tab_layout.setSpacing(10)
        _add_provider_enable_row(copilot_tab_layout, self.copilot_cb, "copilot")
        copilot_tab_layout.addWidget(copilot)
        copilot_tab_layout.addStretch(1)

        openrouter_tab = QWidget()
        openrouter_tab_layout = QVBoxLayout(openrouter_tab)
        openrouter_tab_layout.setContentsMargins(10, 10, 10, 10)
        openrouter_tab_layout.setSpacing(10)
        _add_provider_enable_row(
            openrouter_tab_layout, self.openrouter_cb, "openrouter"
        )
        openrouter_tab_layout.addWidget(openrouter)
        openrouter_tab_layout.addStretch(1)

        antigravity_tab = QWidget()
        antigravity_tab_layout = QVBoxLayout(antigravity_tab)
        antigravity_tab_layout.setContentsMargins(10, 10, 10, 10)
        antigravity_tab_layout.setSpacing(10)
        _add_provider_enable_row(
            antigravity_tab_layout, self.antigravity_cb, "antigravity"
        )
        antigravity_tab_layout.addWidget(antigravity)
        antigravity_tab_layout.addStretch(1)
        provider_pages.update(
            copilot=copilot_tab,
            openrouter=openrouter_tab,
            antigravity=antigravity_tab,
        )

        mcp_tab = QWidget()
        mcp_layout = QVBoxLayout(mcp_tab)
        mcp_layout.setContentsMargins(10, 10, 10, 10)
        mcp_layout.setSpacing(10)
        mcp_intro = QLabel(
            "AI Gauge can share sanitized usage with MCP clients. A pause policy "
            "returns a blocked guard result when an account reaches its threshold. "
            "The connected client must be configured to obey that result."
        )
        mcp_intro.setWordWrap(True)
        mcp_layout.addWidget(mcp_intro)
        self.mcp_enabled_cb = QCheckBox("Enable MCP integration")
        self.mcp_enabled_cb.setChecked(config.mcp_enabled)
        self.mcp_enabled_cb.setToolTip(
            "Publish sanitized local usage for explicitly configured MCP clients."
        )
        mcp_layout.addWidget(self.mcp_enabled_cb)
        policies_box = QGroupBox("Cooperative pause policies")
        policies_form = QFormLayout(policies_box)
        self.mcp_policy_controls: dict[str, tuple[QCheckBox, QSpinBox]] = {}
        mcp_accounts = [
            (account.id, account_display_name(account))
            for account in self._browser_accounts
            if (spec := provider_spec(account.kind)) is None or spec.usage_guard
        ]
        mcp_accounts.extend(
            (spec.id, spec.settings_label)
            for spec in single_specs()
            if spec.usage_guard and getattr(config.providers, spec.id, False)
        )
        for account_id, label in mcp_accounts:
            enabled = QCheckBox("Pause at")
            threshold = QSpinBox()
            threshold.setRange(1, 100)
            threshold.setSuffix("%")
            threshold.setValue(config.mcp_pause_policies.get(account_id, 90))
            threshold.setToolTip(f"MCP account ID: {account_id}")
            enabled.setChecked(account_id in config.mcp_pause_policies)
            threshold.setEnabled(enabled.isChecked())
            enabled.toggled.connect(threshold.setEnabled)
            row = QHBoxLayout()
            row.addWidget(enabled)
            row.addWidget(threshold)
            row.addStretch(1)
            policies_form.addRow(f"{label} ({account_id}):", row)
            self.mcp_policy_controls[account_id] = (enabled, threshold)
        policies_box.setEnabled(self.mcp_enabled_cb.isChecked())
        self.mcp_enabled_cb.toggled.connect(policies_box.setEnabled)
        mcp_layout.addWidget(policies_box)
        command = QLabel(
            "Bind each client profile to the account it actually uses:<br>"
            "<code>ai-gauge-mcp --account-id &lt;account-id&gt;</code><br>"
            "Guard instruction: Call <code>check_current_account_usage</code> "
            "before costly work and stop whenever <code>allowed</code> is false."
        )
        command.setTextFormat(Qt.TextFormat.RichText)
        command.setWordWrap(True)
        mcp_layout.addWidget(command)
        mcp_layout.addStretch(1)

        self.local_usage_panel = LocalUsagePanel(
            config, self._browser_accounts, local_usage_service
        )
        local_usage_scroll = QScrollArea()
        local_usage_scroll.setWidgetResizable(True)
        local_usage_scroll.setFrameShape(QFrame.Shape.NoFrame)
        local_usage_scroll.setWidget(self.local_usage_panel)

        pages = {
            "general": general_tab,
            # A spec with neither an account page nor a bespoke page fails
            # here, when Settings is built, rather than at first navigation.
            **{spec.id: provider_pages[spec.id] for spec in PROVIDER_SPECS},
            "mcp": mcp_tab,
            "local_usage": local_usage_scroll,
        }
        self.nav_list = _NavList(self.move_provider)
        self.nav_list.setObjectName("settings_nav")
        self.nav_list.setFixedWidth(_NAV_WIDTH)
        self.nav_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.pages = QStackedWidget()
        self.pages.setObjectName("settings_pages")
        self.pages.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._page_rows: dict[str, int] = {}
        self._page_indexes: dict[str, int] = {}
        caption_font = self.nav_list.font()
        caption_font.setPointSizeF(max(6.0, caption_font.pointSizeF() * 0.8))
        caption_font.setBold(True)
        # The provider slots in _NAV_ENTRIES are filled in the saved order;
        # every other row keeps its place.
        saved_order = iter(config_provider_order(config))
        for entry in _NAV_ENTRIES:
            if entry[0] is None:
                caption = QListWidgetItem(entry[1])
                caption.setFlags(Qt.ItemFlag.NoItemFlags)
                caption.setFont(caption_font)
                caption.setForeground(QColor(_NAV_DIM_COLOR))
                self.nav_list.addItem(caption)
                continue
            key, label = entry
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if key in _PROVIDER_PAGE_KEYS:
                key = next(saved_order)
                label = _PROVIDER_LABELS[key]
                flags |= Qt.ItemFlag.ItemIsDragEnabled
            item = QListWidgetItem(label)
            item.setFlags(flags)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.nav_list.addItem(item)
            self._page_rows[key] = self.nav_list.count() - 1
            self._page_indexes[key] = self.pages.addWidget(pages[key])
        for checkbox in self._provider_checkboxes.values():
            checkbox.toggled.connect(self._refresh_provider_rows)
        self._refresh_provider_rows()
        self.nav_list.currentRowChanged.connect(self._on_nav_row_changed)
        self.show_page("general")

        # ----- Brand header -----
        # Settings is the roomiest recurring surface in the app, so it can
        # show the detailed application artwork at a size where the gauge
        # remains recognizable instead of reducing it to window-chrome scale.
        self.brand_icon = QLabel()
        self.brand_icon.setObjectName("brand_icon")
        self.brand_icon.setPixmap(app_icon().pixmap(56, 56))
        self.brand_icon.setFixedSize(56, 56)
        self.brand_icon.setToolTip("AI Gauge")

        self.brand_title = QLabel("AI Gauge")
        self.brand_title.setObjectName("brand_title")
        self.brand_title.setStyleSheet(
            "color:#f9fafb; font-size:22px; font-weight:700;"
        )
        self.brand_subtitle = QLabel(
            f"Your AI usage at a glance  ·  Version {__version__}"
        )
        self.brand_subtitle.setObjectName("brand_subtitle")
        self.brand_subtitle.setStyleSheet("color:#9ca3af; font-size:11px;")

        brand_text = QVBoxLayout()
        brand_text.setContentsMargins(0, 2, 0, 2)
        brand_text.setSpacing(2)
        brand_text.addStretch(1)
        brand_text.addWidget(self.brand_title)
        brand_text.addWidget(self.brand_subtitle)
        brand_text.addStretch(1)

        brand_header = QHBoxLayout()
        brand_header.setContentsMargins(4, 0, 4, 0)
        brand_header.setSpacing(12)
        brand_header.addWidget(self.brand_icon)
        brand_header.addLayout(brand_text)
        brand_header.addStretch(1)

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
        layout.addLayout(brand_header)
        body = QHBoxLayout()
        body.setSpacing(10)
        body.addWidget(self.nav_list)
        body.addWidget(self.pages, 1)
        layout.addLayout(body, 1)
        layout.addLayout(button_row)

    def _open_usage(self, spec: ProviderSpec) -> None:
        """Open the provider's usage page. MiniMax has one console per region,
        so it opens the console of each region its rows select."""
        urls = [spec.usage_url]
        if spec.id == "minimax":
            regions = {
                row.region_combo.currentData()
                for row in self._browser_account_rows
                if row.region_combo is not None
            }
            urls = [
                url for region, url in MINIMAX_USAGE_URLS.items() if region in regions
            ] or urls
        for url in urls:
            _open_in_browser(url)

    def _build_account_family_page(self, spec: ProviderSpec) -> QWidget:
        """The Settings page of a multi-account provider.

        Enable checkbox and hint, then a group box with the spec's hint, an
        optional "Open usage in browser" button and the account rows, which
        _rebuild_browser_account_rows() fills.
        """
        accounts = QGroupBox(f"{spec.display_name} Accounts")
        accounts_layout = QVBoxLayout(accounts)
        accounts_layout.setSpacing(8)
        accounts_layout.addWidget(_hint_label(spec.settings_hint))
        if spec.usage_url:
            usage_btn = QPushButton("Open usage in browser")
            usage_btn.setObjectName(f"{spec.id}_open_usage_btn")
            usage_btn.setToolTip(
                f"Open {spec.display_name}'s usage page in your default browser."
            )
            usage_btn.clicked.connect(
                lambda _checked=False, spec=spec: self._open_usage(spec)
            )
            accounts_layout.addWidget(
                usage_btn,
                alignment=Qt.AlignmentFlag.AlignLeft,
            )
        rows_layout = QVBoxLayout()
        # API-key rows have a second line for the key, so sit further apart.
        rows_layout.setSpacing(8 if spec.auth == "api_key" else 6)
        accounts_layout.addLayout(rows_layout)
        self._account_layouts[spec.id] = rows_layout

        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(10, 10, 10, 10)
        page_layout.setSpacing(10)
        _add_provider_enable_row(
            page_layout, self._provider_checkboxes[spec.id], spec.id
        )
        page_layout.addWidget(accounts)
        page_layout.addStretch(1)
        return page

    def page_keys(self) -> list[str]:
        return list(self._page_rows)

    def _provider_rows(self) -> list[int]:
        """Sidebar rows that hold a provider, top to bottom."""
        return [
            row
            for row in range(self.nav_list.count())
            if self.nav_list.item(row).data(Qt.ItemDataRole.UserRole)
            in _PROVIDER_PAGE_KEYS
        ]

    def provider_order(self) -> list[str]:
        """Provider kinds in their current sidebar order."""
        return [
            self.nav_list.item(row).data(Qt.ItemDataRole.UserRole)
            for row in self._provider_rows()
        ]

    def move_provider(self, kind: str, index: int) -> None:
        """Move `kind` to position `index` among the providers (0 is first).

        `index` is clamped to the provider section. The shown page and the
        selected row do not change; only the sidebar order does.
        """
        order = self.provider_order()
        if kind not in order:
            return
        rows = self._provider_rows()
        index = max(0, min(index, len(rows) - 1))
        source = rows[order.index(kind)]
        target = rows[index]
        if source == target:
            return
        current_key = self.current_page_key()
        blocked = self.nav_list.blockSignals(True)
        try:
            item = self.nav_list.takeItem(source)
            self.nav_list.insertItem(target, item)
            self._rebuild_page_rows()
            if current_key:
                self.nav_list.setCurrentRow(self._page_rows[current_key])
        finally:
            self.nav_list.blockSignals(blocked)

    def _rebuild_page_rows(self) -> None:
        rows: dict[str, int] = {}
        for row in range(self.nav_list.count()):
            key = self.nav_list.item(row).data(Qt.ItemDataRole.UserRole)
            if key is not None:
                rows[key] = row
        self._page_rows = rows

    def _refresh_provider_rows(self) -> None:
        """Dim the rows of providers that are off and set every row's tooltip."""
        for kind, checkbox in self._provider_checkboxes.items():
            item = self.nav_list.item(self._page_rows[kind])
            if checkbox.isChecked():
                item.setData(Qt.ItemDataRole.ForegroundRole, None)
                item.setToolTip(_NAV_REORDER_TIP)
            else:
                item.setForeground(QColor(_NAV_DIM_COLOR))
                item.setToolTip(
                    f"{_PROVIDER_LABELS[kind]} is off.\n{_NAV_REORDER_TIP}"
                )

    def current_page_key(self) -> str:
        item = self.nav_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else ""

    def page_widget(self, key: str) -> QWidget:
        return self.pages.widget(self._page_indexes[key])

    def show_page(self, key: str) -> None:
        """Select the row and show the page for `key`."""
        row = self._page_rows[key]
        if self.nav_list.currentRow() != row:
            self.nav_list.setCurrentRow(row)  # the row signal shows the page
        else:
            self.pages.setCurrentIndex(self._page_indexes[key])

    def _on_nav_row_changed(self, row: int) -> None:
        item = self.nav_list.item(row)
        key = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if key in self._page_indexes:
            self.pages.setCurrentIndex(self._page_indexes[key])

    def show_provider(self, kind: str, account_id: str | None = None) -> None:
        if kind in _PROVIDER_PAGE_KEYS:
            self.show_page(kind)
        if account_id is None:
            return
        row = next(
            (
                item
                for item in self._browser_account_rows
                if item.account_id == account_id
            ),
            None,
        )
        if row is not None and row.api_key_edit is not None:
            row.api_key_edit.setFocus()

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
        self._capture_api_key_drafts()
        self._browser_accounts = self._current_browser_accounts()
        self._browser_accounts.append(
            BrowserAccount(
                id=self._new_account_id(kind),
                kind=kind,
                name=self._next_account_name(kind),
                enabled=True,
            )
        )
        self._rebuild_browser_account_rows()

    def _remove_browser_account(self, account_id: str) -> None:
        self._capture_api_key_drafts()
        removed = next(
            (account for account in self._browser_accounts if account.id == account_id),
            None,
        )
        self._browser_accounts = [
            account
            for account in self._current_browser_accounts()
            if account.id != account_id
        ]
        if removed is not None:
            self._removed_browser_accounts[account_id] = removed.kind
        self._api_key_drafts.pop(account_id, None)
        self._rebuild_browser_account_rows()

    def _capture_api_key_drafts(self) -> None:
        for row in self._browser_account_rows:
            # Only API-key rows have a key field.
            if row.api_key_edit is None:
                continue
            self._api_key_drafts[row.account_id] = (
                row.api_key_edit.text(),
                bool(
                    row.clear_api_key_cb is not None
                    and row.clear_api_key_cb.isChecked()
                ),
            )

    def _rebuild_browser_account_rows(self) -> None:
        for layout in self._account_layouts.values():
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
        self._browser_account_rows = []
        for kind, layout in self._account_layouts.items():
            for account in [a for a in self._browser_accounts if a.kind == kind]:
                row = _BrowserAccountRow(
                    account,
                    removable=True,
                )
                row.sign_in_clicked.connect(self.sign_in_clicked.emit)
                row.paste_cookie_clicked.connect(self.paste_cookie_clicked.emit)
                row.clear_sign_in_clicked.connect(self.clear_sign_in_clicked.emit)
                row.remove_clicked.connect(self._remove_browser_account)
                if row.api_key_edit is not None:
                    draft = self._api_key_drafts.get(row.account_id)
                    if draft is not None:
                        row.api_key_edit.setText(draft[0])
                        if row.clear_api_key_cb is not None:
                            row.clear_api_key_cb.setChecked(draft[1])
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

    def _write_secret(
        self,
        value: str | None,
        *,
        setter: Callable[[str | None], None],
        getter: Callable[[], str | None],
        title: str,
        write_error: str,
        verify_error: str,
    ) -> bool:
        """Write a keychain secret and verify the backend accepted the change."""
        try:
            setter(value)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, title, f"{write_error}:\n{exc}")
            return False

        stored = getter()
        verified = stored == value if value is not None else not stored
        if not verified:
            QMessageBox.warning(self, title, verify_error)
            return False
        return True

    def _accept(self) -> None:
        if not self._validate_browser_accounts():
            return
        new_pat = self.gh_pat_edit.text().strip()
        if self.clear_pat_cb.isChecked() and not new_pat:
            if not self._write_secret(
                None,
                setter=set_github_pat,
                getter=get_github_pat,
                title="PAT was not cleared",
                write_error="The saved token could not be cleared",
                verify_error=(
                    "The token still appears to be available after clearing. "
                    "Remove the 'ai-gauge' / 'github-pat' credential from "
                    "your system keychain."
                ),
            ):
                return
        if new_pat:
            if not self._write_secret(
                new_pat,
                setter=set_github_pat,
                getter=get_github_pat,
                title="PAT was not saved",
                write_error="The system keychain rejected the token",
                verify_error=(
                    "The token could not be read back from the system "
                    "keychain. Try running the app normally rather than as a "
                    "different user/elevated account."
                ),
            ):
                return
            log.info("Saved GitHub PAT to system keychain.")

        new_or_key = self.or_key_edit.text().strip()
        if self.clear_or_key_cb.isChecked() and not new_or_key:
            if not self._write_secret(
                None,
                setter=set_openrouter_key,
                getter=get_openrouter_key,
                title="OpenRouter inference key was not cleared",
                write_error="The saved key could not be cleared",
                verify_error=(
                    "The key still appears to be available after clearing. "
                    "Remove the 'ai-gauge' / 'openrouter-key' credential from "
                    "your system keychain."
                ),
            ):
                return
        if new_or_key:
            if not self._write_secret(
                new_or_key,
                setter=set_openrouter_key,
                getter=get_openrouter_key,
                title="OpenRouter inference key was not saved",
                write_error="The system keychain rejected the key",
                verify_error=(
                    "The key could not be read back from the system "
                    "keychain. Try running the app normally rather than as a "
                    "different user/elevated account."
                ),
            ):
                return
            log.info("Saved OpenRouter inference key to system keychain.")

        new_or_mgmt_key = self.or_mgmt_key_edit.text().strip()
        if self.clear_or_mgmt_key_cb.isChecked() and not new_or_mgmt_key:
            if not self._write_secret(
                None,
                setter=set_openrouter_mgmt_key,
                getter=get_openrouter_mgmt_key,
                title="OpenRouter management key was not cleared",
                write_error="The saved key could not be cleared",
                verify_error=(
                    "The key still appears to be available after clearing. "
                    "Remove the 'ai-gauge' / 'openrouter-mgmt-key' credential "
                    "from your system keychain."
                ),
            ):
                return
        if new_or_mgmt_key:
            if not self._write_secret(
                new_or_mgmt_key,
                setter=set_openrouter_mgmt_key,
                getter=get_openrouter_mgmt_key,
                title="OpenRouter management key was not saved",
                write_error="The system keychain rejected the key",
                verify_error=(
                    "The key could not be read back from the system "
                    "keychain. Try running the app normally rather than as a "
                    "different user/elevated account."
                ),
            ):
                return
            log.info("Saved OpenRouter management key to system keychain.")

        for account_id, kind in self._removed_browser_accounts.items():
            spec = provider_spec(kind)
            if spec is None or spec.auth != "api_key":
                continue
            if not self._write_secret(
                None,
                setter=lambda value, kind=kind, account_id=account_id: (
                    set_account_api_key(kind, account_id, value)
                ),
                getter=lambda kind=kind, account_id=account_id: (
                    get_account_api_key(kind, account_id)
                ),
                title=f"{spec.display_name} API key was not cleared",
                write_error=f"The saved key for {account_id} could not be cleared",
                verify_error=(
                    f"The saved key for {account_id} still appears to be "
                    "available in the system keychain."
                ),
            ):
                return

        for row in self._browser_account_rows:
            # Only API-key rows have a key field.
            if row.api_key_edit is None:
                continue
            key_name = f"{provider_base_name(row.kind)} API key"
            new_key = row.api_key_edit.text().strip()
            clear_key = bool(
                row.clear_api_key_cb is not None
                and row.clear_api_key_cb.isChecked()
            )
            if clear_key and not new_key:
                if not self._write_secret(
                    None,
                    setter=lambda value, kind=row.kind, account_id=row.account_id: (
                        set_account_api_key(kind, account_id, value)
                    ),
                    getter=lambda kind=row.kind, account_id=row.account_id: (
                        get_account_api_key(kind, account_id)
                    ),
                    title=f"{key_name} was not cleared",
                    write_error="The saved key could not be cleared",
                    verify_error=(
                        "The key still appears to be available in the system keychain."
                    ),
                ):
                    return
            if new_key:
                if not self._write_secret(
                    new_key,
                    setter=lambda value, kind=row.kind, account_id=row.account_id: (
                        set_account_api_key(kind, account_id, value)
                    ),
                    getter=lambda kind=row.kind, account_id=row.account_id: (
                        get_account_api_key(kind, account_id)
                    ),
                    title=f"{key_name} was not saved",
                    write_error="The system keychain rejected the key",
                    verify_error=(
                        "The key could not be read back from the system keychain. "
                        "Try running the app normally rather than as a different "
                        "user/elevated account."
                    ),
                ):
                    return
                log.info(
                    "Saved %s to system keychain for account=%s.",
                    key_name,
                    row.account_id,
                )

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

    def set_sign_in_browser(self, browser_id: str) -> None:
        """Keep the settings draft aligned with a choice made during sign-in."""
        index = self.sign_in_browser_combo.findData(browser_id)
        self.sign_in_browser_combo.setCurrentIndex(index if index >= 0 else 0)

    def _browse_antigravity_cli(self) -> None:
        file_filter = (
            "agy (agy.exe);;Programs (*.exe);;All files (*)"
            if sys.platform == "win32"
            else "All files (*)"
        )
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Select Antigravity CLI",
            normalize_cli_path(self.antigravity_cli_path_edit.text()) or "",
            file_filter,
        )
        if selected:
            self.antigravity_cli_path_edit.setText(QDir.toNativeSeparators(selected))
            self._update_antigravity_cli_status()

    def _mark_antigravity_cli_unchecked(self) -> None:
        self.antigravity_cli_path_status.setText(
            "The path is checked when you leave the field."
        )

    def _update_antigravity_cli_status(self) -> None:
        configured_path = normalize_cli_path(self.antigravity_cli_path_edit.text())
        if configured_path:
            if not Path(configured_path).is_file():
                status = f"Not found: {configured_path}"
            elif not is_agy_file_name(configured_path):
                status = f"File is not named agy: {configured_path}"
            else:
                status = f"Using: {configured_path}"
        elif self._antigravity_detected_cli:
            status = f"Using auto-detected: {self._antigravity_detected_cli}"
        else:
            status = "agy CLI not found. Install the Antigravity CLI or set its path."
        self.antigravity_cli_path_status.setText(status)

    def apply_to(self, config: Config) -> None:
        config.refresh_interval_minutes = self.refresh_spin.value()
        config.active_refresh_interval_minutes = min(
            self.active_refresh_spin.value(),
            config.refresh_interval_minutes,
        )
        config.start_at_login = self.startup_cb.isChecked()
        config.window.always_on_top = self.always_on_top_cb.isChecked()
        config.window.fade_when_inactive = self.fade_when_inactive_cb.isChecked()
        config.window.square_corners = self.square_corners_cb.isChecked()
        config.window.opacity = self.opacity_slider.value() / 100.0
        new_ui_scale = float(self.ui_scale_combo.currentData())
        self.ui_scale_changed = abs(new_ui_scale - self._initial_ui_scale) > 1e-3
        config.window.ui_scale = new_ui_scale
        config.sign_in_browser = str(self.sign_in_browser_combo.currentData())
        config.mcp_enabled = self.mcp_enabled_cb.isChecked()
        accounts = self._current_browser_accounts()
        # The MCP tab's rows are built once, from the accounts present when the
        # dialog opened. An account removed since then still has a control here,
        # so filter against what is actually being saved rather than persisting
        # a policy for an account that no longer exists.
        live_policy_ids = {account.id for account in accounts}
        live_policy_ids.update(
            spec.id
            for spec in single_specs()
            if self._provider_checkboxes[spec.id].isChecked()
        )
        config.mcp_pause_policies = {
            account_id: threshold.value()
            for account_id, (enabled, threshold) in self.mcp_policy_controls.items()
            if enabled.isChecked() and account_id in live_policy_ids
        }
        config.browser_accounts = accounts
        for kind, checkbox in self._provider_checkboxes.items():
            setattr(config.providers, kind, checkbox.isChecked())
        order = self.provider_order()
        config.provider_order = (
            [] if tuple(order) == DEFAULT_PROVIDER_ORDER else order
        )
        for account_id, kind in self._removed_browser_accounts.items():
            spec = provider_spec(kind)
            # API-key accounts have no browser session; _accept cleared the key.
            if spec is not None and spec.auth == "api_key":
                continue
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
        config.antigravity.show_gemini = self.antigravity_show_gemini_cb.isChecked()
        config.antigravity.show_third_party = (
            self.antigravity_show_third_party_cb.isChecked()
        )
        config.antigravity.cli_path = normalize_cli_path(
            self.antigravity_cli_path_edit.text()
        )
        config.antigravity.colors = self.antigravity_colors.value()
        # Keep the legacy singleton fields synchronized for downgrade safety;
        # account rows are the source of truth from schema version 2 onward.
        first_opencode = next(
            (account for account in accounts if account.kind == "opencode_go"),
            None,
        )
        if first_opencode is not None:
            config.opencode_go.colors = first_opencode.colors.model_copy(deep=True)
        self.local_usage_panel.apply_to(config)
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
