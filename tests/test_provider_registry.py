"""Provider registry: consistency, and characterisation of the derived tables.

The ``EXPECTED_*`` literals below are the provider tables as they stood on
``main`` at f87036c, copied before any call site moved to the registry. Each
test checks both the registry derivation and the module-level name the rest of
the app imports, so the refactor is proven to change no value.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from aigauge import config as config_module
from aigauge.config import BrowserAccount, Config, ProviderToggles
from aigauge.providers import registry
from aigauge.providers.registry import (
    PROVIDER_SPECS,
    account_family,
    account_kinds,
    api_key_kinds,
    browser_session_kinds,
    has_auth_cookie,
    provider_spec,
    sign_in_hosts,
    single_kinds,
)

SRC = Path(__file__).resolve().parents[1] / "src"

# ----- Literals captured from main at f87036c --------------------------------

EXPECTED_COOKIE_NAMES = {
    "claude": "sessionKey",
    "claude_api": "sessionKey",
    "codex": "next-auth.session-token",
}
EXPECTED_COOKIE_NAME_ALIASES = {
    "claude": ("sessionKey",),
    "claude_api": ("sessionKey", "sessionKeyV3"),
    "codex": (
        "next-auth.session-token",
        "__Secure-next-auth.session-token",
        "next-auth.session-token.0",
        "next-auth.session-token.1",
        "__Secure-next-auth.session-token.0",
        "__Secure-next-auth.session-token.1",
    ),
}
EXPECTED_COOKIE_DOMAINS = {
    "claude": ".claude.ai",
    "claude_api": "platform.claude.com",
    "codex": ".chatgpt.com",
}
EXPECTED_DEFAULT_PROVIDER_ORDER = (
    "claude",
    "claude_api",
    "codex",
    "opencode_go",
    "minimax",
    "copilot",
    "openrouter",
    "antigravity",
)
EXPECTED_TOGGLE_DEFAULTS = {
    "claude": True,
    "codex": True,
    "copilot": True,
    "openrouter": False,
    "antigravity": False,
    "opencode_go": False,
    "minimax": False,
    "claude_api": False,
}
EXPECTED_DEFAULT_ACCOUNTS = [
    ("claude", "claude"),
    ("claude_api", "claude_api"),
    ("codex", "codex"),
    ("opencode_go", "opencode_go"),
    ("minimax", "minimax"),
]
EXPECTED_LOGIN_URLS = {
    "claude": ("https://claude.ai/login", "Sign in to Claude"),
    "claude_api": (
        "https://platform.claude.com/login",
        "Sign in to Claude Console",
    ),
    "codex": ("https://chatgpt.com/auth/login", "Sign in to ChatGPT"),
}
EXPECTED_MENUBAR_LABELS = {
    "claude": "Cl",
    "claude_api": "CA",
    "codex": "Cx",
    "opencode_go": "Go",
    "minimax": "Mm",
    "copilot": "Cp",
    "openrouter": "OR",
    "antigravity": "Ag",
}
EXPECTED_NAV_ENTRIES = (
    ("general", "General"),
    (None, "PROVIDERS"),
    ("claude", "Claude"),
    ("claude_api", "Claude API"),
    ("codex", "Codex"),
    ("opencode_go", "OpenCode"),
    ("minimax", "MiniMax"),
    ("copilot", "GitHub Copilot"),
    ("openrouter", "OpenRouter"),
    ("antigravity", "Antigravity"),
    (None, "TOOLS"),
    ("mcp", "MCP"),
    ("local_usage", "Local usage"),
)
EXPECTED_PROVIDER_PAGE_KEYS = frozenset(
    {
        "claude",
        "claude_api",
        "codex",
        "opencode_go",
        "minimax",
        "copilot",
        "openrouter",
        "antigravity",
    }
)
# provider_base_name's explicit map; any other kind falls back to kind.title().
EXPECTED_BASE_NAMES = {
    "claude": "Claude",
    "claude_api": "Claude API",
    "codex": "Codex",
    "opencode_go": "OpenCode",
    "minimax": "MiniMax",
    "antigravity": "Antigravity",
}
# display_name_for_account's fallback for ids that are not configured accounts.
EXPECTED_DISPLAY_NAMES = {
    "claude": "Claude",
    "claude_api": "Claude API",
    "codex": "Codex",
    "copilot": "Copilot",
    "openrouter": "OpenRouter",
    "antigravity": "Antigravity",
    "opencode_go": "OpenCode",
    "minimax": "MiniMax",
}
EXPECTED_ACCOUNT_KINDS = ("claude", "claude_api", "codex", "opencode_go", "minimax")
EXPECTED_BROWSER_SESSION_KINDS = ("claude", "claude_api", "codex")
EXPECTED_API_KEY_KINDS = ("opencode_go", "minimax")
EXPECTED_SINGLE_KINDS = ("copilot", "openrouter", "antigravity")
EXPECTED_AUTH_HOST_ALLOWLIST = (
    "claude.ai",
    "anthropic.com",
    "claude.com",
    "chatgpt.com",
    "openai.com",
    "oaistatic.com",
    "oaiusercontent.com",
    "auth0.com",
    "google.com",
    "github.com",
    "youtube.com",
    "appleid.apple.com",
    "apple.com",
    "icloud.com",
    "microsoftonline.com",
    "microsoft.com",
    "live.com",
)
EXPECTED_SIGN_IN_PROMPT_NAMES = {
    "claude": "Claude",
    "claude_api": "Claude Console",
    "codex": "ChatGPT",
}
EXPECTED_OPENCODE_KEY_ENTRY = "opencode-go-key-{account_id}"


# ----- Consistency -----------------------------------------------------------


def test_ids_and_default_order_slots_are_unique():
    ids = [spec.id for spec in PROVIDER_SPECS]
    orders = [spec.default_order for spec in PROVIDER_SPECS]
    assert len(ids) == len(set(ids))
    assert len(orders) == len(set(orders))
    assert orders == sorted(orders)


def test_every_spec_has_a_toggle_field_and_every_toggle_has_a_spec():
    assert set(ProviderToggles.model_fields) == {spec.id for spec in PROVIDER_SPECS}
    toggles = ProviderToggles()
    for spec in PROVIDER_SPECS:
        assert getattr(toggles, spec.id) is spec.default_enabled, spec.id


def test_browser_session_specs_have_login_cookies_and_keyed_tables():
    from aigauge.cookie_dialog import INSTRUCTIONS
    from aigauge.webview.targets import VERIFY_TARGETS

    for spec in PROVIDER_SPECS:
        if spec.auth != "browser_session":
            continue
        assert spec.multi_account, spec.id
        assert spec.login is not None, spec.id
        assert spec.login.hosts, spec.id
        assert spec.cookies is not None, spec.id
        assert spec.cookies.domain, spec.id
        assert spec.id in VERIFY_TARGETS, spec.id
        # Pasting a cookie is the only path that reads a dotted cookie domain
        # (cookies._set_cookie) or opens the instructions dialog.
        assert (spec.id in INSTRUCTIONS) == spec.paste_cookie, spec.id
        if spec.paste_cookie:
            assert spec.cookies.domain.startswith("."), spec.id
        assert spec.api_key_entry is None, spec.id


def test_api_key_specs_have_no_browser_sign_in_and_a_keyring_entry():
    for spec in PROVIDER_SPECS:
        if spec.auth != "api_key":
            continue
        assert spec.multi_account, spec.id
        assert spec.login is None, spec.id
        assert spec.cookies is None, spec.id
        assert not spec.paste_cookie, spec.id
        assert spec.api_key_entry is not None, spec.id
        assert "{account_id}" in spec.api_key_entry, spec.id
        assert spec.api_key_placeholder, spec.id


def test_single_specs_have_no_accounts_sign_in_or_keys():
    for spec in PROVIDER_SPECS:
        if spec.auth != "single":
            continue
        assert not spec.multi_account, spec.id
        assert spec.login is None and spec.cookies is None, spec.id
        assert spec.api_key_entry is None, spec.id
        assert not spec.paste_cookie and not spec.compact_collapse, spec.id


def test_multi_account_specs_carry_a_settings_hint():
    for spec in PROVIDER_SPECS:
        if spec.multi_account:
            assert spec.settings_hint, spec.id


def test_usage_urls_match_the_provider_modules():
    from aigauge.providers.claude import CLAUDE_USAGE_URL
    from aigauge.providers.codex import CODEX_USAGE_URL

    assert provider_spec("claude").usage_url == CLAUDE_USAGE_URL
    assert provider_spec("codex").usage_url == CODEX_USAGE_URL
    assert provider_spec("opencode_go").usage_url is None
    from aigauge.providers.minimax import MINIMAX_USAGE_URL

    assert provider_spec("minimax").usage_url == MINIMAX_USAGE_URL
    assert provider_spec("claude_api").usage_url == (
        "https://platform.claude.com/settings/billing"
    )
    from aigauge.webview.targets import VERIFY_TARGETS

    assert VERIFY_TARGETS["claude_api"][0] == provider_spec("claude_api").usage_url


def test_registry_is_a_leaf_module():
    script = (
        "import sys\n"
        "import aigauge.providers.registry\n"
        "loaded = [m for m in sys.modules if m.startswith('PyQt6')"
        " or m == 'aigauge.config' or m.startswith('aigauge.webview')]\n"
        "assert not loaded, loaded\n"
    )
    env = dict(os.environ, PYTHONPATH=str(SRC))
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_provider_spec_lookup():
    assert provider_spec("claude").display_name == "Claude"
    assert provider_spec("claude-abc") is None
    assert provider_spec(None) is None
    assert provider_spec("nope") is None


# ----- Characterisation: registry derivations equal today's literals ---------


def test_cookie_tables():
    assert {
        spec.id: spec.cookies.name
        for spec in PROVIDER_SPECS
        if spec.cookies is not None and spec.cookies.name
    } == EXPECTED_COOKIE_NAMES
    assert {
        spec.id: spec.cookies.aliases
        for spec in PROVIDER_SPECS
        if spec.cookies is not None
    } == EXPECTED_COOKIE_NAME_ALIASES
    assert {
        spec.id: spec.cookies.domain
        for spec in PROVIDER_SPECS
        if spec.cookies is not None
    } == EXPECTED_COOKIE_DOMAINS
    assert config_module.COOKIE_NAMES == EXPECTED_COOKIE_NAMES
    assert config_module.COOKIE_NAME_ALIASES == EXPECTED_COOKIE_NAME_ALIASES
    assert config_module.COOKIE_DOMAINS == EXPECTED_COOKIE_DOMAINS


def test_default_order_toggles_and_default_accounts():
    assert tuple(spec.id for spec in PROVIDER_SPECS) == EXPECTED_DEFAULT_PROVIDER_ORDER
    assert config_module.DEFAULT_PROVIDER_ORDER == EXPECTED_DEFAULT_PROVIDER_ORDER
    assert ProviderToggles().model_dump() == EXPECTED_TOGGLE_DEFAULTS
    # Field order is part of the saved config file's layout.
    assert list(ProviderToggles.model_fields) == list(EXPECTED_TOGGLE_DEFAULTS)
    assert [
        (account.id, account.kind) for account in Config().browser_accounts
    ] == EXPECTED_DEFAULT_ACCOUNTS
    assert [(kind, kind) for kind in account_kinds()] == EXPECTED_DEFAULT_ACCOUNTS


def test_login_urls():
    from aigauge.app import LOGIN_URLS

    assert {
        spec.id: (spec.login.url, spec.login.window_title)
        for spec in PROVIDER_SPECS
        if spec.login is not None
    } == EXPECTED_LOGIN_URLS
    assert LOGIN_URLS == EXPECTED_LOGIN_URLS


def test_menubar_labels():
    from aigauge.menubar import PROVIDER_LABELS, _provider_label

    assert {spec.id: spec.short_label for spec in PROVIDER_SPECS} == (
        EXPECTED_MENUBAR_LABELS
    )
    assert PROVIDER_LABELS == EXPECTED_MENUBAR_LABELS
    assert _provider_label("claude-1a2b") == "Cl"
    assert _provider_label("claude_api") == "CA"
    assert _provider_label("claude_api-work") == "CA"
    assert _provider_label("codex-1a2b") == "Cx"
    assert _provider_label("opencode_go-work") == "Go"
    assert _provider_label("minimax-work") == "Mm"
    assert _provider_label("copilot") == "Cp"
    assert _provider_label("antigravity") == "Ag"
    assert _provider_label("mystery") == "My"


def test_settings_nav_entries_and_page_keys():
    from aigauge import settings_dialog

    assert (
        (("general", "General"), (None, "PROVIDERS"))
        + tuple((spec.id, spec.settings_label) for spec in PROVIDER_SPECS)
        + ((None, "TOOLS"), ("mcp", "MCP"), ("local_usage", "Local usage"))
    ) == EXPECTED_NAV_ENTRIES
    assert settings_dialog._NAV_ENTRIES == EXPECTED_NAV_ENTRIES  # noqa: SLF001
    assert frozenset(spec.id for spec in PROVIDER_SPECS) == EXPECTED_PROVIDER_PAGE_KEYS
    assert settings_dialog._PROVIDER_PAGE_KEYS == EXPECTED_PROVIDER_PAGE_KEYS  # noqa: SLF001


def test_provider_base_name():
    for kind, name in EXPECTED_BASE_NAMES.items():
        assert provider_spec(kind).display_name == name
        assert config_module.provider_base_name(kind) == name
    assert config_module.provider_base_name("copilot") == "Copilot"
    assert config_module.provider_base_name("some_new_kind") == "Some_New_Kind"


def test_display_name_for_account_fallbacks():
    config = Config(browser_accounts=[])
    assert {spec.id: spec.display_name for spec in PROVIDER_SPECS} == (
        EXPECTED_DISPLAY_NAMES
    )
    for provider_id, name in EXPECTED_DISPLAY_NAMES.items():
        assert config_module.display_name_for_account(config, provider_id) == name
    assert config_module.display_name_for_account(config, "unknown") == "unknown"
    named = Config(
        browser_accounts=[BrowserAccount(id="codex-x", kind="codex", name="Work")]
    )
    assert config_module.display_name_for_account(named, "codex-x") == "Codex (Work)"


def test_kind_sets():
    assert account_kinds() == EXPECTED_ACCOUNT_KINDS
    assert browser_session_kinds() == EXPECTED_BROWSER_SESSION_KINDS
    assert api_key_kinds() == EXPECTED_API_KEY_KINDS
    assert single_kinds() == EXPECTED_SINGLE_KINDS


def test_browser_accounts_filters_to_account_kinds():
    config = Config(
        browser_accounts=[
            BrowserAccount(id=kind, kind=kind)
            for kind in (*EXPECTED_ACCOUNT_KINDS, *EXPECTED_SINGLE_KINDS, "other")
        ]
    )
    assert [
        account.kind for account in config_module.browser_accounts(config)
    ] == list(EXPECTED_ACCOUNT_KINDS)


def test_account_kind_and_family_by_id_shape():
    config = Config(browser_accounts=[])
    cases = {
        "claude": "claude",
        "claude-1a2b": "claude",
        "claude_api": "claude_api",
        "claude_api-2": "claude_api",
        "claude-2": "claude",
        "codex": "codex",
        "codex-1a2b": "codex",
        "opencode_go": "opencode_go",
        "opencode_go-work": "opencode_go",
        "minimax": "minimax",
        "minimax-work": "minimax",
        "copilot": None,
        "openrouter": None,
        "antigravity": None,
        "claudex": None,
        "opencode": None,
    }
    for account_id, kind in cases.items():
        assert config_module.account_kind(config, account_id) == kind, account_id
        assert account_family(account_id) == kind, account_id


def test_widget_provider_family():
    from aigauge.widget import _provider_family

    assert _provider_family("claude-1a2b") == "claude"
    assert _provider_family("claude_api") == "claude_api"
    assert _provider_family("claude_api-work") == "claude_api"
    assert _provider_family("codex") == "codex"
    assert _provider_family("opencode_go-work") == "opencode_go"
    assert _provider_family("minimax-work") == "minimax"
    assert _provider_family("copilot") == "copilot"
    assert _provider_family("mystery") == "mystery"


def test_auth_cookie_names():
    from aigauge.webview.cookies import _has_auth_cookie as pasted_has_auth
    from aigauge.webview.external_login import _has_auth_cookie as captured_has_auth

    cases = [
        ("claude", ["sessionKey"], True),
        ("claude", ["other"], False),
        ("codex", ["__Secure-next-auth.session-token.0"], True),
        ("codex", ["__Secure-oai-is"], True),
        ("codex", ["sessionKey"], False),
        ("claude", ["__Secure-oai-is"], False),
        ("claude_api", ["sessionKey"], True),
        ("claude_api", ["sessionKeyV3"], True),
        ("claude_api", ["sessionKeyLC"], False),
        ("claude", ["sessionKeyV3"], False),
        ("opencode_go", ["sessionKey"], False),
        ("minimax", ["sessionKey"], False),
        ("copilot", ["sessionKey"], False),
    ]
    for kind, names, expected in cases:
        assert has_auth_cookie(kind, names) is expected, (kind, names)
        assert pasted_has_auth(kind, [(name, "v") for name in names]) is expected
        assert (
            captured_has_auth(kind, [{"name": name, "value": "v"} for name in names])
            is expected
        )


def test_sign_in_host_allowlist():
    from aigauge.webview.login_window import AUTH_HOST_ALLOWLIST

    assert sign_in_hosts() == EXPECTED_AUTH_HOST_ALLOWLIST[:7]
    assert AUTH_HOST_ALLOWLIST == EXPECTED_AUTH_HOST_ALLOWLIST


def test_sign_in_prompt_names():
    from aigauge.webview.external_login import ExternalLoginWorker
    from aigauge.webview.login_window import LoginWindow

    assert {
        spec.id: spec.login.prompt_name
        for spec in PROVIDER_SPECS
        if spec.login is not None
    } == EXPECTED_SIGN_IN_PROMPT_NAMES
    for provider, name in {**EXPECTED_SIGN_IN_PROMPT_NAMES, "other": "other"}.items():
        assert LoginWindow._provider_name(SimpleNamespace(_provider=provider)) == name  # noqa: SLF001
        worker = ExternalLoginWorker(provider, "https://example.test/", provider)
        assert worker._provider_name() == name  # noqa: SLF001


def test_opencode_keyring_entry_is_unchanged():
    spec = provider_spec("opencode_go")
    assert spec.api_key_entry == EXPECTED_OPENCODE_KEY_ENTRY
    assert spec.api_key_entry.format(account_id="opencode_go-work") == (
        "opencode-go-key-opencode_go-work"
    )


def test_minimax_keyring_entry_is_distinct_from_opencode():
    spec = provider_spec("minimax")
    assert spec.api_key_entry == "minimax-key-{account_id}"
    assert spec.api_key_entry.format(account_id="minimax-work") == (
        "minimax-key-minimax-work"
    )
    assert spec.api_key_placeholder == "sk-cp-..."
    entries = {
        other.api_key_entry for other in PROVIDER_SPECS if other.api_key_entry
    }
    assert len(entries) == len(api_key_kinds())


@pytest.mark.parametrize("spec", PROVIDER_SPECS, ids=lambda spec: spec.id)
def test_factories_are_callable(spec):
    assert callable(spec.make)


# ----- Generic API-key helpers -----------------------------------------------


def _fake_keyring(monkeypatch) -> dict:
    secrets: dict = {}
    monkeypatch.setattr(
        config_module.keyring,
        "get_password",
        lambda service, key: secrets.get((service, key)),
    )
    monkeypatch.setattr(
        config_module.keyring,
        "set_password",
        lambda service, key, value: secrets.__setitem__((service, key), value),
    )
    monkeypatch.setattr(
        config_module.keyring,
        "delete_password",
        lambda service, key: secrets.pop((service, key), None),
    )
    return secrets


def test_account_api_key_uses_the_spec_keyring_entry(monkeypatch):
    secrets = _fake_keyring(monkeypatch)

    config_module.set_account_api_key("opencode_go", "opencode_go-work", "k1")

    assert secrets == {("ai-gauge", "opencode-go-key-opencode_go-work"): "k1"}
    assert config_module.get_account_api_key("opencode_go", "opencode_go-work") == "k1"
    # The OpenCode wrappers read and write the same entry.
    assert config_module.get_opencode_go_key("opencode_go-work") == "k1"
    config_module.set_opencode_go_key("opencode_go", "k0")
    assert config_module.get_account_api_key("opencode_go", "opencode_go") == "k0"
    config_module.set_account_api_key("opencode_go", "opencode_go-work", None)
    assert config_module.get_account_api_key("opencode_go", "opencode_go-work") is None
    config_module.set_account_api_key("opencode_go", "opencode_go", "")
    assert secrets == {}


def test_account_api_key_rejects_kinds_without_an_entry(monkeypatch):
    _fake_keyring(monkeypatch)
    for kind in ("claude", "copilot", "unknown"):
        with pytest.raises(ValueError):
            config_module.get_account_api_key(kind, kind)
        with pytest.raises(ValueError):
            config_module.set_account_api_key(kind, kind, "x")


def test_account_api_key_read_tolerates_keyring_errors(monkeypatch):
    def broken(service, key):
        raise config_module.keyring.errors.KeyringError("locked")

    monkeypatch.setattr(config_module.keyring, "get_password", broken)
    monkeypatch.setattr(config_module.keyring, "delete_password", broken)
    assert config_module.get_account_api_key("opencode_go", "opencode_go") is None
    config_module.set_account_api_key("opencode_go", "opencode_go", None)


def test_registry_lookup_reads_the_live_table(monkeypatch):
    extra = registry.ProviderSpec(
        id="test_only",
        display_name="Test",
        settings_label="Test",
        short_label="Te",
        auth="single",
        multi_account=False,
        default_enabled=False,
        default_order=99,
        make=lambda config, account, parent: None,
    )
    monkeypatch.setitem(registry._SPECS_BY_ID, "test_only", extra)  # noqa: SLF001
    assert provider_spec("test_only") is extra


# ----- Call sites that moved to the registry ---------------------------------

COMPACT_COLLAPSIBLE_IDS = (
    "claude",
    "claude-1a2b",
    "codex",
    "codex-1a2b",
    "opencode_go",
    "opencode_go-work",
    "minimax",
    "minimax-work",
)
NOT_COMPACT_COLLAPSIBLE_IDS = (
    "claude_api",
    "claude_api-work",
    "copilot",
    "openrouter",
    "antigravity",
    "mystery",
)


def test_app_compact_collapse_memory_by_tile_id(monkeypatch):
    from aigauge.app import App

    monkeypatch.setattr(Config, "save", lambda self: None)
    app = App.__new__(App)
    app._config = Config()  # noqa: SLF001
    for provider in (*COMPACT_COLLAPSIBLE_IDS, *NOT_COMPACT_COLLAPSIBLE_IDS):
        app._on_tile_expanded_changed(provider, False)  # noqa: SLF001
    assert app._config.collapsed_tiles == list(COMPACT_COLLAPSIBLE_IDS)  # noqa: SLF001


def test_widget_tiles_by_kind(qtbot):
    from aigauge.widget import _ProviderTile, _is_api_key_tile

    assert [p for p in COMPACT_COLLAPSIBLE_IDS if _is_api_key_tile(p)] == [
        "opencode_go",
        "opencode_go-work",
        "minimax",
        "minimax-work",
    ]
    for provider in COMPACT_COLLAPSIBLE_IDS:
        tile = _ProviderTile.__new__(_ProviderTile)
        tile.provider = provider
        assert tile._supports_compact_collapse(), provider  # noqa: SLF001
    for provider in NOT_COMPACT_COLLAPSIBLE_IDS:
        tile = _ProviderTile.__new__(_ProviderTile)
        tile.provider = provider
        assert not tile._supports_compact_collapse(), provider  # noqa: SLF001


def test_enabled_and_mcp_account_ids_include_enabled_single_providers():
    from aigauge.app import _enabled_providers
    from aigauge.mcp_server import _configured_account_ids

    config = Config(browser_accounts=[])
    for kind in EXPECTED_SINGLE_KINDS:
        setattr(config.providers, kind, True)
    assert _enabled_providers(config) == EXPECTED_SINGLE_KINDS
    assert _configured_account_ids(config) == set(EXPECTED_SINGLE_KINDS)
    config.providers.openrouter = False
    assert _enabled_providers(config) == ("copilot", "antigravity")
    assert _configured_account_ids(config) == {"copilot", "antigravity"}


def test_enabled_providers_without_account_list_uses_one_account_per_kind():
    from aigauge.app import _enabled_providers

    config = SimpleNamespace(
        providers=SimpleNamespace(
            claude=True, codex=False, opencode_go=True, copilot=False
        ),
    )
    assert _enabled_providers(config) == ("claude", "opencode_go")


def test_cookie_paste_is_offered_only_for_paste_cookie_kinds(monkeypatch):
    from aigauge import app as app_module
    from aigauge.app import App

    opened = []

    class FakeCookieDialog:
        def __init__(self, kind, **kwargs):
            opened.append(kind)

        def exec(self):
            return 0

    monkeypatch.setattr(app_module, "CookieDialog", FakeCookieDialog)
    app = App.__new__(App)
    app._config = Config(  # noqa: SLF001
        browser_accounts=[
            BrowserAccount(id="claude", kind="claude"),
            BrowserAccount(id="codex-x", kind="codex"),
            BrowserAccount(id="opencode_go", kind="opencode_go"),
            BrowserAccount(id="minimax", kind="minimax"),
            BrowserAccount(id="claude_api", kind="claude_api"),
        ]
    )
    app._settings_dialog = None  # noqa: SLF001
    app._widget = SimpleNamespace(  # noqa: SLF001
        suspend_always_on_top=lambda: None,
        restore_always_on_top=lambda: None,
    )
    for provider in (
        "claude",
        "codex-x",
        "opencode_go",
        "minimax",
        "claude_api",
        "copilot",
        "nobody",
    ):
        app.open_cookie_paste(provider)
    assert opened == ["claude", "codex"]


def test_settings_builds_one_account_page_per_multi_account_spec(qtbot, monkeypatch):
    from PyQt6.QtWidgets import QGroupBox, QPushButton

    from aigauge import settings_dialog
    from aigauge.settings_dialog import SettingsDialog

    reads = []
    monkeypatch.setattr(
        settings_dialog,
        "get_account_api_key",
        lambda kind, account_id: reads.append((kind, account_id)),
    )
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)

    assert reads == [("opencode_go", "opencode_go"), ("minimax", "minimax")]
    claude_api_page = dialog.page_widget("claude_api")
    assert claude_api_page.findChild(QPushButton, "claude_api_signin_btn") is not None
    assert claude_api_page.findChild(QPushButton, "claude_api_paste_cookie_btn") is None
    for spec in PROVIDER_SPECS:
        page = dialog.page_widget(spec.id)
        assert page.isAncestorOf(getattr(dialog, f"{spec.id}_cb")), spec.id
        if not spec.multi_account:
            continue
        titles = [box.title() for box in page.findChildren(QGroupBox)]
        assert titles == [f"{spec.display_name} Accounts"], spec.id
        usage = page.findChild(QPushButton, f"{spec.id}_open_usage_btn")
        assert (usage is not None) == (spec.usage_url is not None), spec.id
        rows = [r for r in dialog._browser_account_rows if r.kind == spec.id]  # noqa: SLF001
        assert rows, spec.id
        for row in rows:
            assert (row.api_key_edit is not None) == (spec.auth == "api_key")
            signin = page.findChild(QPushButton, f"{row.account_id}_signin_btn")
            assert (signin is not None) == (spec.auth == "browser_session")


def test_claude_auth_redirect_applies_to_every_claude_account_only():
    from aigauge.webview.scraper import _claude_auth_redirect

    url = "https://claude.ai/login"
    for provider in ("claude", "claude-1a2b"):
        assert _claude_auth_redirect(url, provider) == {"logged_out": True, "url": url}
    for provider in (
        "codex",
        "claudex",
        "opencode_go",
        "minimax",
        "copilot",
        "claude_api",
        "claude_api-2",
    ):
        assert _claude_auth_redirect(url, provider) is None


def test_minimax_provider_is_built_for_the_accounts_region():
    from aigauge.config import BrowserAccount, Config
    from aigauge.providers.minimax import MINIMAX_REGIONS

    spec = provider_spec("minimax")
    cn = spec.make(
        Config(), BrowserAccount(id="minimax-cn", kind="minimax", region="china"), None
    )
    intl = spec.make(Config(), BrowserAccount(id="minimax", kind="minimax"), None)

    assert cn._region == "china"  # noqa: SLF001
    assert MINIMAX_REGIONS[cn._region][1] == "https://api.minimax.cn"  # noqa: SLF001
    assert intl._region == "international"  # noqa: SLF001


def test_claude_api_spec_matches_the_brief():
    spec = provider_spec("claude_api")

    assert (spec.display_name, spec.settings_label, spec.short_label) == (
        "Claude API",
        "Claude API",
        "CA",
    )
    assert spec.auth == "browser_session"
    assert spec.multi_account and not spec.default_enabled
    assert not spec.usage_guard and not spec.paste_cookie
    assert PROVIDER_SPECS[1] is spec
    assert spec.login.hosts == ("claude.com", "anthropic.com")
    assert "Owner, Admin or Billing role" in spec.settings_hint
    assert "Claude API is enabled above" in spec.settings_hint
    # Every other kind keeps its guard row.
    assert [s.id for s in PROVIDER_SPECS if not s.usage_guard] == ["claude_api"]


def test_claude_api_factory_builds_a_provider_for_the_account():
    from aigauge.providers.claude_api import ClaudeApiProvider

    provider = provider_spec("claude_api").make(
        Config(), BrowserAccount(id="claude_api-work", kind="claude_api"), None
    )

    assert isinstance(provider, ClaudeApiProvider)
    assert provider._account_id == "claude_api-work"  # noqa: SLF001
