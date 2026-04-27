from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from .logging_setup import log_path
from .models import UsageSnapshot

_DARK_STYLESHEET = """
QDialog { background:#1f2937; color:#e5e7eb; }
QLabel { color:#e5e7eb; background:transparent; }
QPlainTextEdit {
    background:#111827; color:#f3f4f6;
    border:1px solid #374151; border-radius:4px;
    padding:6px;
    font-family: Consolas, 'Courier New', monospace;
    font-size: 11px;
}
QPushButton {
    background:#374151; color:#f3f4f6;
    border:1px solid #4b5563; border-radius:4px;
    padding:5px 12px; min-height:22px;
}
QPushButton:hover { background:#4b5563; }
QPushButton:default { background:#2563eb; border-color:#1d4ed8; }
"""


def _format_diagnostics(provider: str, snapshot: UsageSnapshot) -> str:
    payload: dict[str, Any] = {
        "provider": provider,
        "status": snapshot.status.value,
        "fetched_at": snapshot.fetched_at.isoformat(timespec="seconds"),
        "error": snapshot.error,
        "raw": snapshot.raw,
    }
    return json.dumps(payload, indent=2, default=str)


def reveal_path(path) -> None:
    """Open the OS file browser at this file/directory."""
    try:
        if sys.platform.startswith("win"):
            target = str(path)
            if os.path.isfile(target):
                subprocess.Popen(["explorer", "/select,", target])
            else:
                os.startfile(target)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent if os.path.isfile(path) else path)])
    except Exception:  # noqa: BLE001
        pass


class ErrorDetailsDialog(QDialog):
    """Read-only window showing the last snapshot's error and raw payload.

    Provides Copy and "Open log folder" affordances so a user reporting a
    problem has something concrete to attach.
    """

    def __init__(self, provider: str, display_name: str, snapshot: UsageSnapshot, parent=None):
        super().__init__(None)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setWindowTitle(f"{display_name} — error details")
        self.resize(560, 460)
        self.setStyleSheet(_DARK_STYLESHEET)

        header = QLabel(
            f"<b>{display_name}</b> last refresh failed at "
            f"{snapshot.fetched_at.strftime('%Y-%m-%d %H:%M:%S')}.<br/>"
            f"<span style='color:#ef4444;'>{(snapshot.error or 'unknown error')}</span>"
        )
        header.setWordWrap(True)
        header.setTextFormat(Qt.TextFormat.RichText)

        hint = QLabel(
            "If this keeps happening, try <b>Refresh now</b> first. If it persists, "
            "use <b>Paste cookie</b> in Settings to refresh the session — Claude/ChatGPT "
            "expire tokens periodically. <b>Copy diagnostics</b> if you want to share "
            "what the page returned."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#9ca3af; font-size:11px;")

        diagnostics = _format_diagnostics(provider, snapshot)
        self._diagnostics_text = diagnostics
        body = QPlainTextEdit()
        body.setReadOnly(True)
        body.setPlainText(diagnostics)

        copy_btn = QPushButton("Copy diagnostics")
        copy_btn.clicked.connect(self._copy)
        log_btn = QPushButton("Open log folder")
        log_btn.clicked.connect(lambda: reveal_path(log_path()))

        close_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_box.rejected.connect(self.reject)
        close_box.accepted.connect(self.accept)

        action_row = QHBoxLayout()
        action_row.addWidget(copy_btn)
        action_row.addWidget(log_btn)
        action_row.addStretch(1)
        action_row.addWidget(close_box)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)
        layout.addWidget(header)
        layout.addWidget(hint)
        layout.addWidget(body, 1)
        layout.addLayout(action_row)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self._diagnostics_text)
