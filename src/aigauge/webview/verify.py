from __future__ import annotations

from typing import Any, Callable

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtWebEngineCore import QWebEngineSettings

from .page import QuietWebEnginePage
from .profile import get_profile

# Load the provider's actual usage page and check for text that only renders for
# a signed-in user. If the cookie is good the page renders inline; if not it
# either redirects to /login or shows an interstitial.
VERIFY_TARGETS = {
    "claude": (
        "https://claude.ai/settings/usage",
        "(() => document.body && document.body.innerText.includes('Plan usage limits'))()",
    ),
    "codex": (
        "https://chatgpt.com/codex/cloud/settings/analytics#personal-usage",
        r"""(() => {
          const visibleText = el => ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
          const text = visibleText(document.body);
          if (/5 hour usage limit/i.test(text) && /Weekly usage limit/i.test(text) && /\d+(?:\.\d+)?\s*%/.test(text)) {
            return true;
          }
          const labels = Array.from(document.querySelectorAll('button,a,[role="tab"],[role="button"],div,span,p'));
          const label = labels.find(el => visibleText(el).toLowerCase() === 'personal usage');
          const target = label && (label.closest('button,a,[role="tab"],[role="button"]') || label);
          if (target) target.click();
          return false;
        })()""",
    ),
}


class SessionVerifier(QObject):
    """Loads the provider's usage page off-screen and runs a JS check.

    Emits ``done(ok, error)``:
    - ``ok=True`` — page loaded as a signed-in user.
    - ``ok=False, error=""`` — page loaded but the signed-in marker was missing
      (typical when the cookie is expired or incomplete).
    - ``ok=False, error=<reason>`` — load failure or timeout; verification was
      inconclusive rather than negative.
    """

    done = pyqtSignal(bool, str)

    def __init__(
        self,
        provider: str,
        account_id: str | None = None,
        timeout_ms: int = 20000,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._finished = False

        target = VERIFY_TARGETS.get(provider)
        if target is None:
            QTimer.singleShot(0, lambda: self._finish(False, f"no verify target for {provider}"))
            return
        url, check_js = target
        self._check_js = check_js
        self._check_attempts = 0

        profile = get_profile(account_id or provider)
        self._page = QuietWebEnginePage(profile, self)
        s = self._page.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)

        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(lambda: self._finish(False, "timeout"))
        self._timeout.start(timeout_ms)

        self._page.loadFinished.connect(self._on_load_finished)
        self._page.load(QUrl(url))

    def _on_load_finished(self, ok: bool) -> None:
        if self._finished:
            return
        if not ok:
            self._finish(False, "page failed to load")
            return
        # Give React/SSR a beat to render before the check runs.
        QTimer.singleShot(2000, self._run_check)

    def _run_check(self) -> None:
        if self._finished:
            return
        self._page.runJavaScript(self._check_js, self._on_js_result)

    def _on_js_result(self, result: Any) -> None:
        if self._finished:
            return
        if result is True:
            self._finish(True, "")
            return
        self._check_attempts += 1
        if self._check_attempts >= 12:
            self._finish(False, "")
            return
        QTimer.singleShot(1000, self._run_check)

    def _finish(self, ok: bool, error: str) -> None:
        if self._finished:
            return
        self._finished = True
        self._timeout.stop()
        self.done.emit(ok, error)
        QTimer.singleShot(0, self._cleanup)

    def _cleanup(self) -> None:
        try:
            self._page.loadFinished.disconnect(self._on_load_finished)
        except (TypeError, RuntimeError):
            pass
        try:
            self._page.setLifecycleState(self._page.LifecycleState.Discarded)
        except RuntimeError:
            pass
        self._page.deleteLater()
        self.deleteLater()


def verify_session(
    provider: str,
    on_done: Callable[[bool, str], None],
    *,
    account_id: str | None = None,
    parent: QObject | None = None,
) -> SessionVerifier:
    """Convenience wrapper. Returns the verifier so the caller can keep a ref."""
    verifier = SessionVerifier(provider, account_id=account_id, parent=parent)
    verifier.done.connect(on_done)
    return verifier
