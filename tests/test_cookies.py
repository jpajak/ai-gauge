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
