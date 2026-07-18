from aigauge.config import BrowserAccount, Config
from aigauge.webview.cookies import _parse_cookie_pairs
from aigauge.webview import cookies


def test_parse_codex_full_cookie_header_keeps_related_cookies():
    pasted = (
        "Cookie: __Host-next-auth.csrf-token=csrf; "
        "__Secure-oai-is=identity; "
        "__Secure-next-auth.session-token.0=first; "
        "__Secure-next-auth.session-token.1=second"
    )
    assert _parse_cookie_pairs("codex", pasted) == [
        ("__Host-next-auth.csrf-token", "csrf"),
        ("__Secure-oai-is", "identity"),
        ("__Secure-next-auth.session-token.0", "first"),
        ("__Secure-next-auth.session-token.1", "second"),
    ]


def test_parse_codex_full_cookie_header_with_json_cookie_keeps_related_cookies():
    pasted = (
        'Cookie: __cflb=load-balancer; g_state={"i_l":0}; '
        'oai-chat-web-route="route-value"; '
        "__Secure-next-auth.session-token=session"
    )
    assert _parse_cookie_pairs("codex", pasted) == [
        ("__cflb", "load-balancer"),
        ("g_state", '{"i_l":0}'),
        ("oai-chat-web-route", "route-value"),
        ("__Secure-next-auth.session-token", "session"),
    ]


def test_parse_codex_bare_cookie_header_value_with_json_cookie_keeps_related_cookies():
    pasted = (
        '__cflb=load-balancer; g_state={"i_l":0}; '
        'oai-chat-web-route="route-value"; '
        "__Secure-next-auth.session-token=session"
    )
    assert _parse_cookie_pairs("codex", pasted) == [
        ("__cflb", "load-balancer"),
        ("g_state", '{"i_l":0}'),
        ("oai-chat-web-route", "route-value"),
        ("__Secure-next-auth.session-token", "session"),
    ]


def test_parse_codex_split_name_value_lines():
    pasted = (
        "__Secure-next-auth.session-token.0=first\n"
        "__Secure-next-auth.session-token.1=second\n"
    )
    assert _parse_cookie_pairs("codex", pasted) == [
        ("__Secure-next-auth.session-token.0", "first"),
        ("__Secure-next-auth.session-token.1", "second"),
    ]


def test_parse_codex_split_raw_value_lines():
    assert _parse_cookie_pairs("codex", "first\nsecond") == [
        ("__Secure-next-auth.session-token.0", "first"),
        ("__Secure-next-auth.session-token.1", "second"),
    ]


def test_parse_codex_raw_value_uses_current_and_legacy_names():
    assert _parse_cookie_pairs("codex", "single-token") == [
        ("next-auth.session-token", "single-token"),
        ("__Secure-next-auth.session-token", "single-token"),
    ]



def test_parse_opencode_go_full_cookie_header_keeps_all_cookies():
    pasted = "Cookie: auth=session; other=value"

    assert _parse_cookie_pairs("opencode_go", pasted) == [
        ("auth", "session"),
        ("other", "value"),
    ]


def test_parse_opencode_go_rejects_bare_raw_value():
    assert _parse_cookie_pairs("opencode_go", "raw-session-value") == []
def test_parse_claude_raw_value():
    assert _parse_cookie_pairs("claude", "session-value") == [
        ("sessionKey", "session-value")
    ]


def test_parse_claude_full_cookie_header_requires_session_key():
    pasted = "Cookie: other=value; sessionKey=claude-session; another=thing"
    assert _parse_cookie_pairs("claude", pasted) == [
        ("other", "value"),
        ("sessionKey", "claude-session"),
        ("another", "thing"),
    ]


def test_parse_claude_rejects_chatgpt_cookie_header():
    pasted = (
        "Cookie: __Secure-oai-is=identity; "
        "__Secure-next-auth.session-token.0=first"
    )
    assert _parse_cookie_pairs("claude", pasted) == []


def test_parse_codex_rejects_unrelated_cookie_header():
    assert _parse_cookie_pairs("codex", "Cookie: unrelated=value; other=thing") == []


def test_import_browser_cookies_preserves_attributes_and_saves_fallback(monkeypatch):
    imported = []
    saved = []
    cleared = []

    class FakeStore:
        def deleteAllCookies(self):  # noqa: N802 - Qt-shaped test double
            cleared.append(True)

        def setCookie(self, cookie, origin):  # noqa: N802 - Qt-shaped test double
            imported.append((cookie, origin))

    class FakeProfile:
        def cookieStore(self):  # noqa: N802 - Qt-shaped test double
            return FakeStore()

    monkeypatch.setattr(cookies, "get_profile", lambda account_id: FakeProfile())
    monkeypatch.setattr(
        cookies,
        "set_provider_cookie",
        lambda account_id, value: saved.append((account_id, value)),
    )

    assert cookies.import_browser_cookies(
        "codex",
        "codex-work",
        [
            {
                "name": "__Secure-next-auth.session-token",
                "value": "session-secret",
                "domain": ".chatgpt.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "expires": 2_000_000_000,
            },
            {
                "name": "google-cookie",
                "value": "must-not-import",
                "domain": ".google.com",
            },
        ],
    )

    assert len(imported) == 1
    assert cleared == [True]
    cookie, origin = imported[0]
    assert bytes(cookie.name()).decode() == "__Secure-next-auth.session-token"
    assert bytes(cookie.value()).decode() == "session-secret"
    assert cookie.isSecure()
    assert cookie.isHttpOnly()
    assert origin.host() == "chatgpt.com"
    assert saved == [
        (
            "codex-work",
            "__Secure-next-auth.session-token=session-secret",
        )
    ]


def test_import_browser_cookies_preserves_host_only_domains(monkeypatch):
    imported = []
    calls = []

    class FakeStore:
        def deleteAllCookies(self):  # noqa: N802 - Qt-shaped test double
            calls.append("clear")

        def setCookie(self, cookie, origin):  # noqa: N802 - Qt-shaped test double
            calls.append("set")
            imported.append((cookie, origin))

    class FakeProfile:
        def cookieStore(self):  # noqa: N802 - Qt-shaped test double
            return FakeStore()

    monkeypatch.setattr(cookies, "get_profile", lambda account_id: FakeProfile())
    monkeypatch.setattr(cookies, "set_provider_cookie", lambda account_id, value: None)

    assert cookies.import_browser_cookies(
        "opencode_go",
        "opencode_go",
        [
            {
                "name": "auth",
                "value": "session",
                "domain": "opencode.ai",
                "path": "/",
                "secure": False,
                "httpOnly": True,
            },
            {
                "name": "provider",
                "value": "oauth-provider",
                "domain": "auth.opencode.ai",
                "path": "/",
                "secure": True,
                "httpOnly": True,
            },
        ],
    )

    assert calls == ["clear", "set", "set"]
    assert [cookie.domain() for cookie, _origin in imported] == ["", ""]
    assert [origin.host() for _cookie, origin in imported] == [
        "opencode.ai",
        "auth.opencode.ai",
    ]


def test_clear_browser_session_removes_secret_cookies_and_cache(monkeypatch):
    calls = []

    class Store:
        def deleteAllCookies(self):  # noqa: N802 - Qt-shaped test double
            calls.append("cookies")

    class Profile:
        def cookieStore(self):  # noqa: N802 - Qt-shaped test double
            return Store()

        def clearHttpCache(self):  # noqa: N802 - Qt-shaped test double
            calls.append("cache")

    monkeypatch.setattr(
        cookies,
        "set_provider_cookie",
        lambda account_id, value: calls.append((account_id, value)),
    )
    monkeypatch.setattr(cookies, "get_profile", lambda account_id: Profile())

    cookies.clear_browser_session("codex-work")

    assert calls == [("codex-work", None), "cookies", "cache"]


def test_clear_browser_session_attempts_profile_cleanup_if_secret_fails(monkeypatch):
    calls = []

    class Store:
        def deleteAllCookies(self):  # noqa: N802 - Qt-shaped test double
            calls.append("cookies")

    class Profile:
        def cookieStore(self):  # noqa: N802 - Qt-shaped test double
            return Store()

        def clearHttpCache(self):  # noqa: N802 - Qt-shaped test double
            calls.append("cache")

    monkeypatch.setattr(
        cookies,
        "set_provider_cookie",
        lambda account_id, value: (_ for _ in ()).throw(RuntimeError("locked")),
    )
    monkeypatch.setattr(cookies, "get_profile", lambda account_id: Profile())

    try:
        cookies.clear_browser_session("claude")
    except RuntimeError as exc:
        assert "encrypted cookie: locked" in str(exc)
    else:
        raise AssertionError("partial cleanup failure was not reported")

    assert calls == ["cookies", "cache"]


def test_cookie_hydration_logs_names_without_values(monkeypatch, caplog):
    def fake_get_provider_cookie(provider):
        if provider == "claude":
            return "Cookie: other=value; sessionKey=secret-session"
        return None

    injected = []
    monkeypatch.setattr(cookies, "get_provider_cookie", fake_get_provider_cookie)
    monkeypatch.setattr(
        cookies,
        "inject_session_cookie",
        lambda provider, value: injected.append((provider, value)) or True,
    )
    caplog.set_level("INFO", logger="aigauge.webview.cookies")

    assert cookies.hydrate_all_from_keyring() == ["claude"]

    assert "provider=claude" in caplog.text
    assert "sessionKey" in caplog.text
    assert "secret-session" not in caplog.text
    assert injected == [("claude", "Cookie: other=value; sessionKey=secret-session")]


def test_cookie_hydration_uses_account_ids_for_secrets_and_profiles(monkeypatch):
    config = Config()
    config.browser_accounts.append(
        BrowserAccount(
            id="codex-work",
            kind="codex",
            name="Work",
            enabled=True,
        )
    )

    def fake_get_provider_cookie(account_id):
        if account_id == "codex-work":
            return "__Secure-next-auth.session-token=secret"
        return None

    injected = []
    monkeypatch.setattr(cookies, "get_provider_cookie", fake_get_provider_cookie)
    monkeypatch.setattr(
        cookies,
        "inject_session_cookie",
        lambda provider, value, *, account_id=None: injected.append(
            (provider, value, account_id)
        )
        or True,
    )

    assert cookies.hydrate_all_from_keyring(config) == ["codex-work"]
    assert injected == [
        ("codex", "__Secure-next-auth.session-token=secret", "codex-work")
    ]


def test_cookie_hydration_skips_injection_when_profile_has_persistent_cookies(
    monkeypatch, caplog
):
    # If Chromium has already persisted cookies for this profile, re-injecting
    # the stored blob would overwrite session tokens the site has rotated
    # since the original paste. Skip injection in that case.
    config = Config()
    config.browser_accounts.append(
        BrowserAccount(
            id="codex-home",
            kind="codex",
            name="Home",
            enabled=True,
        )
    )

    from aigauge.config import webview_profile_dir

    profile_dir = webview_profile_dir("codex-home")
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "Cookies").write_bytes(b"SQLite format 3\x00")

    monkeypatch.setattr(
        cookies,
        "get_provider_cookie",
        lambda account_id: (
            "__Secure-next-auth.session-token=stale" if account_id == "codex-home" else None
        ),
    )
    injected = []
    monkeypatch.setattr(
        cookies,
        "inject_session_cookie",
        lambda *a, **kw: injected.append((a, kw)) or True,
    )
    caplog.set_level("INFO", logger="aigauge.webview.cookies")

    assert cookies.hydrate_all_from_keyring(config) == []
    assert injected == []
    assert "profile_has_cookies=True" in caplog.text
    assert "skip_reason=profile_has_live_cookies" in caplog.text


def test_cookie_hydration_injects_when_profile_cookies_file_is_missing(
    monkeypatch,
):
    # Fresh profile (no Cookies file): hydration must seed it from the keyring
    # so the first scrape can sign in.
    config = Config()
    config.browser_accounts.append(
        BrowserAccount(
            id="codex-fresh",
            kind="codex",
            name="Fresh",
            enabled=True,
        )
    )

    monkeypatch.setattr(
        cookies,
        "get_provider_cookie",
        lambda account_id: (
            "__Secure-next-auth.session-token=fresh" if account_id == "codex-fresh" else None
        ),
    )
    injected = []
    monkeypatch.setattr(
        cookies,
        "inject_session_cookie",
        lambda provider, value, *, account_id=None: injected.append(
            (provider, account_id)
        )
        or True,
    )

    assert cookies.hydrate_all_from_keyring(config) == ["codex-fresh"]
    assert injected == [("codex", "codex-fresh")]
def test_cookie_hydration_includes_enabled_opencode_go(monkeypatch):
    config = Config()
    config.providers.opencode_go = True

    monkeypatch.setattr(
        cookies,
        "get_provider_cookie",
        lambda account_id: "Cookie: auth=fresh" if account_id == "opencode_go" else None,
    )
    injected = []
    monkeypatch.setattr(
        cookies,
        "inject_session_cookie",
        lambda provider, value, *, account_id=None: injected.append(
            (provider, value, account_id)
        )
        or True,
    )

    assert cookies.hydrate_all_from_keyring(config) == ["opencode_go"]
    assert injected == [("opencode_go", "Cookie: auth=fresh", None)]
