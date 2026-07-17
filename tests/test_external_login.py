from aigauge.webview.external_login import (
    _domain_matches,
    _has_auth_cookie,
    _provider_cookies,
)


def test_provider_cookie_filter_accepts_only_the_provider_domain():
    values = [
        {"name": "sessionKey", "value": "secret", "domain": ".claude.ai"},
        {"name": "pref", "value": "yes", "domain": "assets.claude.ai"},
        {"name": "sessionKey", "value": "evil", "domain": "evilclaude.ai"},
        {"name": "google", "value": "private", "domain": ".google.com"},
    ]

    filtered = _provider_cookies("claude", values)

    assert [item["value"] for item in filtered] == ["secret", "yes"]
    assert _domain_matches(".chatgpt.com", "codex")
    assert not _domain_matches("notchatgpt.com", "codex")


def test_auth_cookie_detection_handles_codex_split_tokens():
    cookies = [
        {
            "name": "__Secure-next-auth.session-token.0",
            "value": "first",
            "domain": ".chatgpt.com",
        }
    ]

    assert _has_auth_cookie("codex", cookies)
    assert not _has_auth_cookie(
        "claude",
        [{"name": "preference", "value": "x", "domain": ".claude.ai"}],
    )
