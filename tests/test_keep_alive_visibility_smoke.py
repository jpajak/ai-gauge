"""Real-Qt smoke for the keep-alive approach.

Runs in a subprocess because Chromium aborts the whole process on a machine
with no usable GL (issue #7); a crash there must skip this test, not kill the
suite. It checks the two facts the keep-alive relies on, against the installed
QtWebEngine:

* a page detached from its view and made visible reads
  ``document.visibilityState === "visible"``, and
* doing so leaves no top-level widget visible (the view is never shown).

``setVisible`` only takes effect once the first ``load()`` has created the web
contents; called before it, the document reads ``hidden``. HeadlessScraper
therefore calls it right after the first ``load()``, which is what this does.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

_SCRIPT = r"""
import json, sys
from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)
profile = QWebEngineProfile(app)  # off the record: nothing on disk
page = QWebEnginePage(profile, app)
view = QWebEngineView()
view.setPage(page)
view.resize(1280, 900)
view.setPage(None)
out = {}


def finish():
    out["top_level_visible"] = [
        type(w).__name__ for w in QApplication.topLevelWidgets() if w.isVisible()
    ]
    out["view_visible"] = view.isVisible()
    print("RESULT " + json.dumps(out), flush=True)
    app.quit()


def on_loaded(ok):
    out["loaded"] = ok
    out["page_visible"] = page.isVisible()

    def got(value):
        out["visibility"] = value
        finish()

    page.runJavaScript("document.visibilityState", got)


page.loadFinished.connect(on_loaded)
page.load(QUrl("data:text/html,<p>hi</p>"))
page.setVisible(True)
page.setLifecycleState(QWebEnginePage.LifecycleState.Active)
QTimer.singleShot(20000, app.quit)
app.exec()
"""


def test_detached_visible_page_reads_visible_and_shows_no_window():
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-c", _SCRIPT],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        )
    except subprocess.TimeoutExpired:
        pytest.skip("WebEngine smoke timed out")
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    if not lines:
        pytest.skip(f"WebEngine unavailable here (exit {proc.returncode})")
    result = json.loads(lines[-1][len("RESULT "):])

    assert result["loaded"] is True
    assert result["visibility"] == "visible", result
    assert result["page_visible"] is True
    assert result["view_visible"] is False
    assert result["top_level_visible"] == []
