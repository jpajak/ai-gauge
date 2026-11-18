"""Which cookies sign-in keeps, and that a .claude.com cookie imports cleanly."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aigauge.config import COOKIE_DOMAINS
from aigauge.webview import cookies
from aigauge.webview.external_login import _domain_matches, _provider_cookies


def _kept_domains(provider: str, domains: list[str]) -> list[str]:
    values = [
        {"name": f"c{index}", "value": "v", "domain": domain}
        for index, domain in enumerate(domains)
    ]
    return [item["domain"] for item in _provider_cookies(provider, values)]


def test_claude_api_keeps_the_domain_cookies_the_browser_sends_to_the_console():
    domains = [
        "platform.claude.com",
        ".platform.claude.com",
        "api.platform.claude.com",
        ".claude.com",
        "claude.com",  # host-only: Chrome does not send it to platform.claude.com
        ".anthropic.com",
        ".claude.ai",
        ".com",
        "com",
        "evilclaude.com",
        ".evilclaude.com",
        ".platform.claude.com.evil.test",
        "",
    ]

    assert _kept_domains("claude_api", domains) == [
        "platform.claude.com",
        ".platform.claude.com",
        "api.platform.claude.com",
        ".claude.com",
    ]


def test_apex_domain_providers_keep_exactly_what_they_kept_before():
    domains = [
        ".claude.ai",
        "claude.ai",
        "assets.claude.ai",
        ".ai",
        ".chatgpt.com",
        ".com",
        ".claude.com",
        "evilclaude.ai",
    ]

    assert _kept_domains("claude", domains) == [
        ".claude.ai",
        "claude.ai",
        "assets.claude.ai",
    ]
    assert _kept_domains("codex", domains) == [".chatgpt.com"]


def test_every_provider_keeps_at_least_what_domain_matches_accepted():
    domains = [
        "platform.claude.com",
        ".platform.claude.com",
        "claude.ai",
        ".claude.ai",
        "chatgpt.com",
        ".chatgpt.com",
        "x.chatgpt.com",
    ]
    for provider in COOKIE_DOMAINS:
        for domain in domains:
            if _domain_matches(domain, provider):
                assert _kept_domains(provider, [domain]) == [domain], (provider, domain)


def test_only_providers_on_a_subdomain_gain_cookies():
    # A parent domain needs a dot of its own, so a provider on an apex domain
    # (claude.ai, chatgpt.com) has no parent to gain.
    parents = [".claude.com", ".claude.ai", ".chatgpt.com", ".anthropic.com", ".com", ".ai"]
    gained = {}
    for provider in COOKIE_DOMAINS:
        extra = [
            domain
            for domain in parents
            if cookies.cookie_domain_in_scope(domain, provider)
            and not _domain_matches(domain, provider)
        ]
        if extra:
            gained[provider] = extra

    assert gained == {"claude_api": [".claude.com"]}


def test_import_keeps_the_claude_com_domain_cookie_and_drops_the_rest(monkeypatch):
    imported = []
    saved = []

    class FakeStore:
        def deleteAllCookies(self):  # noqa: N802 - Qt-shaped test double
            pass

        def setCookie(self, cookie, origin):  # noqa: N802 - Qt-shaped test double
            imported.append((cookie, origin))

    class FakeProfile:
        def cookieStore(self):  # noqa: N802 - Qt-shaped test double
            return FakeStore()

    monkeypatch.setattr(cookies, "get_profile", lambda account_id: FakeProfile())
    monkeypatch.setattr(
        cookies, "set_provider_cookie", lambda account_id, value: saved.append(value)
    )

    assert cookies.import_browser_cookies(
        "claude_api",
        "claude_api",
        [
            {"name": "sessionKeyV3", "value": "s", "domain": "platform.claude.com"},
            {"name": "lastActiveOrg", "value": "org", "domain": ".claude.com"},
            {"name": "host-only", "value": "x", "domain": "claude.com"},
            {"name": "other", "value": "y", "domain": ".anthropic.com"},
        ],
    )

    by_name = {bytes(c.name()).decode(): (c, origin) for c, origin in imported}
    assert sorted(by_name) == ["lastActiveOrg", "sessionKeyV3"]
    shared, origin = by_name["lastActiveOrg"]
    assert shared.domain() == ".claude.com"
    assert origin.host() == "claude.com"
    host_only, origin = by_name["sessionKeyV3"]
    assert host_only.domain() == ""
    assert origin.host() == "platform.claude.com"
    assert saved == ["sessionKeyV3=s; lastActiveOrg=org"]


_IMPORT_SCRIPT = r"""
import json, sys
from PyQt6.QtCore import QEventLoop, QTimer, QUrl
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PyQt6.QtWidgets import QApplication

from aigauge.webview import cookies

app = QApplication(sys.argv)
profile = QWebEngineProfile(app)  # off the record: nothing on disk
# The cookie store only reports once a page has started the browser context.
page = QWebEnginePage(profile)
page.load(QUrl("data:text/html,hi"))
seen = []
profile.cookieStore().cookieAdded.connect(
    lambda c: seen.append(
        [bytes(c.name()).decode(), c.domain(), c.path(), c.isSecure(), c.isHttpOnly()]
    )
)
profile.cookieStore().loadAllCookies()
cookies.get_profile = lambda account_id: profile
cookies.set_provider_cookie = lambda account_id, value: None
ok = cookies.import_browser_cookies(
    "claude_api",
    "claude_api",
    [
        {"name": "sessionKeyV3", "value": "s", "domain": "platform.claude.com",
         "path": "/", "secure": True, "httpOnly": True},
        {"name": "lastActiveOrg", "value": "org", "domain": ".claude.com",
         "path": "/", "secure": True, "httpOnly": False},
    ],
)
loop = QEventLoop()
QTimer.singleShot(1500, loop.quit)
loop.exec()
print("RESULT " + json.dumps({"ok": ok, "seen": sorted(seen)}))
"""


def test_a_claude_com_domain_cookie_lands_in_a_real_profile():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    src = str(Path(cookies.__file__).resolve().parents[2])
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _IMPORT_SCRIPT],
            capture_output=True,
            text=True,
            timeout=90,
            env=env,
        )
    except subprocess.TimeoutExpired:
        pytest.skip("Qt WebEngine did not start")
    line = next(
        (item for item in proc.stdout.splitlines() if item.startswith("RESULT ")), None
    )
    if line is None:
        pytest.skip(f"Qt WebEngine unavailable here: {proc.stderr[-300:]}")
    result = json.loads(line[len("RESULT "):])

    assert result["ok"] is True
    # A host-only cookie reports its host; the domain cookie keeps its dot.
    assert result["seen"] == [
        ["lastActiveOrg", ".claude.com", "/", True, False],
        ["sessionKeyV3", "platform.claude.com", "/", True, True],
    ]
