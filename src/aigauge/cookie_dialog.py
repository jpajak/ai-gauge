from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from .config import COOKIE_NAMES, set_provider_cookie
from .config import get_provider_cookie
from .webview.cookies import _parse_cookie_pairs, inject_session_cookie
from .webview.verify import VERIFY_TARGETS, verify_session

log = logging.getLogger("aigauge.cookie_dialog")

INSTRUCTIONS = {
    "claude": (
        "Claude.ai session cookie",
        f"""\
1. Open <a style='color:#60a5fa;' href='https://claude.ai/settings/usage'>claude.ai/settings/usage</a>
   in <b>Chrome / Edge / Firefox</b> (whichever you're already signed into).
2. Press <b>F12</b> → <b>Network</b>, then reload the page.
3. Click a <code>claude.ai</code> request.
4. In <b>Headers</b> → <b>Request Headers</b>, copy the full
   <code>Cookie:</code> header and paste it below. It must include
   <code>{COOKIE_NAMES['claude']}</code>.
""",
    ),
    "codex": (
        "ChatGPT session cookie",
        f"""\
1. Open <a style='color:#60a5fa;' href='https://chatgpt.com/codex/cloud/settings/analytics'>
   chatgpt.com/codex/cloud/settings/analytics</a> in your normal browser.
2. Press <b>F12</b> → <b>Network</b>, then reload the page.
3. Click a <code>chatgpt.com</code> request such as <code>analytics</code>,
   <code>backend-api</code>, or <code>accounts/check</code>.
4. In <b>Headers</b> → <b>Request Headers</b>, copy the full
   <code>Cookie:</code> header and paste it below.
   This is more reliable than copying individual split session-token rows.
""",
    ),
}


_DARK_STYLESHEET = """
QDialog { background:#1f2937; color:#e5e7eb; }
QLabel { color:#e5e7eb; background:transparent; }
QPlainTextEdit {
    background:#111827; color:#f3f4f6;
    border:1px solid #374151; border-radius:4px;
    padding:6px; selection-background-color:#2563eb;
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
QPushButton:default:hover { background:#1d4ed8; }
"""


class CookieDialog(QDialog):
    def __init__(
        self,
        provider: str,
        *,
        account_id: str | None = None,
        display_name: str | None = None,
    ):
        super().__init__(None)
        if provider not in INSTRUCTIONS:
            raise ValueError(f"unknown provider: {provider}")
        self._provider = provider
        self._account_id = account_id or provider
        self._verifier = None
        title, instructions_html = INSTRUCTIONS[provider]
        # Not stays-on-top: the user has to switch to their normal browser to
        # copy the cookie, and the main widget is suspended from always-on-top
        # by the caller while this is open.
        self.setWindowTitle(f"Paste {display_name or title}")
        self.resize(540, 460)
        self.setStyleSheet(_DARK_STYLESHEET)

        instructions = QLabel(instructions_html.replace("\n", "<br/>"))
        instructions.setWordWrap(True)
        instructions.setOpenExternalLinks(True)
        instructions.setTextFormat(Qt.TextFormat.RichText)
        instructions.setStyleSheet(
            "background:#111827; padding:10px; border-radius:6px; font-size:11px;"
        )

        self._cookie_input = QPlainTextEdit()
        self._cookie_input.setPlaceholderText("Paste cookie value here…")
        self._cookie_input.setMinimumHeight(80)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setTextFormat(Qt.TextFormat.RichText)
        self._status.setStyleSheet("color:#9ca3af; font-size:11px;")

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self._save)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)
        layout.addWidget(instructions)
        layout.addWidget(self._cookie_input, 1)
        layout.addWidget(self._status)
        layout.addWidget(self._buttons)

    def _set_busy(self, busy: bool) -> None:
        self._buttons.setEnabled(not busy)
        self._cookie_input.setReadOnly(busy)

    def _save(self) -> None:
        value = self._cookie_input.toPlainText().strip()
        if not value:
            self.reject()
            return
        pairs = _parse_cookie_pairs(self._provider, value)
        if not pairs:
            QMessageBox.warning(
                self,
                "No cookies recognized",
                "Nothing recognizable was pasted. Paste a cookie value, name=value "
                "row, or full Cookie: request header.",
            )
            return
        set_provider_cookie(self._account_id, value)
        persisted = get_provider_cookie(self._account_id)
        if persisted != value:
            QMessageBox.warning(
                self,
                "Cookie was not saved",
                "The cookie parsed correctly but did not persist to encrypted "
                "storage. Check terminal output for DPAPI/key storage errors.",
            )
            return
        inject_session_cookie(
            self._provider,
            value,
            account_id=self._account_id,
        )
        names = ", ".join(name for name, _ in pairs[:6])
        if len(pairs) > 6:
            names += f", and {len(pairs) - 6} more"
        log.info("saved %s cookies: %s", self._provider, names)

        if self._provider not in VERIFY_TARGETS:
            self.accept()
            return

        self._set_busy(True)
        self._status.setText(
            f"<span style='color:#9ca3af;'>Saved. Verifying that the cookie loads "
            f"a signed-in {self._provider} page…</span>"
        )
        self._verifier = verify_session(
            self._provider,
            self._on_verify_done,
            account_id=self._account_id,
            parent=self,
        )

    def _on_verify_done(self, ok: bool, error: str) -> None:
        if ok:
            log.info("cookie verify ok for %s", self._provider)
            self.accept()
            return

        if error:
            # Inconclusive (timeout / page failed to load). Cookie is saved;
            # accept anyway so the user can let the regular refresh retry.
            log.warning("cookie verify inconclusive for %s: %s", self._provider, error)
            QMessageBox.information(
                self,
                "Couldn't reach the verification page",
                f"Cookie was saved, but verification couldn't complete "
                f"({error}). The next refresh will try with the new cookie.",
            )
            self.accept()
            return

        # Loaded but the signed-in marker was missing — cookie is incomplete or
        # already invalid.
        log.warning("cookie verify failed for %s: page loaded but signed-out", self._provider)
        self._set_busy(False)
        self._status.setText(
            "<span style='color:#ef4444;'><b>Cookie didn't authenticate.</b> The page loaded "
            "but still showed signed-out. Common causes: you copied only part of the "
            "<code>Cookie:</code> header, the cookie has already expired, or the "
            "request you grabbed was an auth-less one. Try copying the full header "
            "from a request that returns user data and paste again.</span>"
        )
