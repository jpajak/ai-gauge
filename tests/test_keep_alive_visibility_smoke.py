"""Real-Qt smoke for the keep-alive page hosting.

Runs in a subprocess because Chromium aborts the whole process on a machine
with no usable GL (issue #7); a crash there must skip this test, not kill the
suite. It drives the real ``HeadlessScraper(linger_ms=...)`` against a
``data:`` page, with an off-the-record profile in place of ``get_profile``, and
checks the facts the keep-alive relies on from its ``keep-alive end`` line:

* the page reads ``visibilityState === "visible"`` and draws (rAF ticks),
* it has a real 1280x900 viewport and ``document.hasFocus()`` is true,
* no native top-level window owned by the process is ever visible (checked on
  Windows only, by polling ``EnumWindows``), and no Qt top-level widget is left
  visible once the visit has been cleaned up.

The view is shown with ``WA_DontShowOnScreen``: Qt treats it as shown, but no
native window appears. A page detached from its view reported "visible" while
drawing nothing on the Claude page, which is why it is not used.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

_SCRIPT = r"""
import json, logging, os, re, sys, threading, time
from PyQt6.QtCore import QTimer
from PyQt6.QtWebEngineCore import QWebEngineProfile
from PyQt6.QtWidgets import QApplication

from aigauge.webview import scraper as scraper_module

app = QApplication(sys.argv)
app.setQuitOnLastWindowClosed(False)
profile = QWebEngineProfile(app)  # off the record: nothing on disk
scraper_module.get_profile = lambda provider: profile

lines = []
class Capture(logging.Handler):
    def emit(self, record):
        lines.append(record.getMessage())
logging.getLogger("aigauge.scraper").addHandler(Capture())
logging.getLogger("aigauge.scraper").setLevel(logging.INFO)

visible_windows = []
stop = threading.Event()

def poll_windows():
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    pid = os.getpid()
    Enum = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(hwnd, buf, 128)
            visible_windows.append(buf.value)
        return True
    while not stop.is_set():
        user32.EnumWindows(Enum(cb), 0)
        time.sleep(0.05)

if sys.platform == "win32" and os.environ.get("QT_QPA_PLATFORM") != "offscreen":
    threading.Thread(target=poll_windows, daemon=True).start()

done = []
scraper = scraper_module.HeadlessScraper(
    "claude_api",
    "data:text/html,<p>hi</p>",
    "1",
    wait_ms=300,
    linger_ms=4000,
)
scraper.done.connect(lambda result, error: done.append(error))
out = {}

def finish():
    stop.set()
    out["done"] = done
    out["lingering_left"] = len(scraper_module._LINGERING)
    out["top_level_visible"] = [
        type(w).__name__ for w in QApplication.topLevelWidgets() if w.isVisible()
    ]
    out["visible_native_windows"] = sorted(set(visible_windows))
    end = [m for m in lines if m.startswith("keep-alive end ")]
    out["end"] = end[-1] if end else None
    print("RESULT " + json.dumps(out), flush=True)
    app.quit()

def check():
    if scraper_module._LINGERING:
        QTimer.singleShot(200, check)
    else:
        finish()

QTimer.singleShot(200, check)
QTimer.singleShot(30000, finish)
app.exec()
"""


def _field(line: str, name: str) -> str:
    for part in line.split():
        if part.startswith(name + "="):
            return part.split("=", 1)[1]
    raise AssertionError(f"{name} missing from {line!r}")


def test_keep_alive_page_is_visible_drawing_focused_and_shows_no_window():
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _SCRIPT],
            capture_output=True,
            text=True,
            timeout=60,
            env={
                **os.environ,
                "QT_QPA_PLATFORM": os.environ.get("QT_QPA_PLATFORM", "offscreen"),
                "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
            },
        )
    except subprocess.TimeoutExpired:
        pytest.skip("WebEngine smoke timed out")
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    if not lines:
        pytest.skip(f"WebEngine unavailable here (exit {proc.returncode})")
    result = json.loads(lines[-1][len("RESULT "):])

    assert result["done"] == [""], result
    end = result["end"]
    assert end is not None, result
    assert _field(end, "visibility") == "visible", end
    assert _field(end, "has_focus") == "True", end
    assert int(_field(end, "raf_ticks")) > 0, end
    assert _field(end, "viewport") == "1280x900", end
    assert result["lingering_left"] == 0
    assert result["top_level_visible"] == []
    assert result["visible_native_windows"] == [], result
