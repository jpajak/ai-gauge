"""Static provider registry: one ``ProviderSpec`` per provider.

Every place that used to enumerate providers by literal (cookie domains, login
URLs, settings pages, menu-bar labels, kind filters) derives its table from
``PROVIDER_SPECS`` instead. See ``docs/provider-registry-plan.md``.

This is a leaf module. It imports nothing from ``aigauge.config``, Qt or
QtWebEngine at module scope, so ``config`` can import it and so can processes
with no GL context (issue #7). Each factory imports its provider module inside
the function for the same reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Iterable, Literal

if TYPE_CHECKING:  # pragma: no cover - typing only
    from PyQt6.QtCore import QObject

    from ..config import BrowserAccount, Config
    from .base import Provider

AuthShape = Literal["browser_session", "api_key", "single"]
ProviderFactory = Callable[
    ["Config", "BrowserAccount | None", "QObject | None"], "Provider"
]


@dataclass(frozen=True)
class LoginSpec:
    """How a browser-session provider signs in."""

    url: str
    window_title: str
    # Name used in sign-in prompts ("Finish signing in to ChatGPT ..."). It is
    # the site's name, which can differ from the provider's display name.
    prompt_name: str
    # Host suffixes the embedded sign-in browser may navigate its top frame
    # to. Shared identity-provider hosts live in webview/login_window.py.
    hosts: tuple[str, ...]


@dataclass(frozen=True)
class CookieSpec:
    """The session cookies of a browser-session provider."""

    domain: str
    # Primary cookie name shown in the UI; None when no stable name exists.
    name: str | None
    # Every name that carries the session, including auth migrations and the
    # split ``.0`` / ``.1`` cookies DevTools shows for very large values.
    aliases: tuple[str, ...] = ()
    # Further cookie names whose presence alone proves a signed-in session but
    # which are not injected when a raw value is pasted.
    extra_auth_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    display_name: str
    # Label of the provider's Settings sidebar row and enable checkbox.
    settings_label: str
    # Two-character label for the macOS menu bar.
    short_label: str
    auth: AuthShape
    # True when the user can add several accounts of this kind; their
    # BrowserAccount rows use ``id`` as kind and ``id`` or ``id-<suffix>`` as
    # account id.
    multi_account: bool
    default_enabled: bool
    # Position in the default provider order; lower comes first.
    default_order: int
    make: ProviderFactory
    login: LoginSpec | None = None
    cookies: CookieSpec | None = None
    usage_url: str | None = None
    # Rich-text hint at the top of the account group on the provider's
    # Settings page. Bespoke (single-instance) pages carry their own text.
    settings_hint: str = ""
    paste_cookie: bool = False
    compact_collapse: bool = False
    # Keyring entry name for an account's API key, formatted with
    # ``account_id``. Only ``api_key`` specs set it.
    api_key_entry: str | None = None
    api_key_placeholder: str = ""


# ----- Factories -------------------------------------------------------------
# Each imports its provider inside the function: provider modules pull in Qt
# and, for scraping providers, the WebEngine runner (issue #7). PyInstaller's
# import scan follows function-level imports, so no hidden imports are needed.


def _make_claude(config, account, parent):
    from .claude import ClaudeProvider

    return ClaudeProvider(
        parent=parent,
        account_id=account.id,
        show_fable=account.show_fable,
    )


def _make_codex(config, account, parent):
    from .codex import CodexProvider

    return CodexProvider(parent=parent, account_id=account.id)


def _make_opencode_go(config, account, parent):
    from .opencode_go import OpenCodeGoProvider

    return OpenCodeGoProvider(config, parent=parent, account_id=account.id)


def _make_copilot(config, account, parent):
    from .copilot import CopilotProvider

    # Built without a Qt parent, as before the registry: it is not a QObject.
    return CopilotProvider(config)


def _make_openrouter(config, account, parent):
    from .openrouter import OpenRouterProvider

    return OpenRouterProvider(config)


def _make_antigravity(config, account, parent):
    from .antigravity import AntigravityProvider

    antigravity = config.antigravity
    return AntigravityProvider(
        cli_path=antigravity.cli_path,
        show_gemini=antigravity.show_gemini,
        show_third_party=antigravity.show_third_party,
    )


_BROWSER_SIGN_IN_HINT = (
    "Name each {name} subscription here. All accounts appear when "
    "{name} is enabled above. <b>Sign in</b> opens a "
    "real installed browser and supports Google and passkeys; the "
    "session is connected automatically."
)

_DECLARED_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        id="claude",
        display_name="Claude",
        settings_label="Claude",
        short_label="Cl",
        auth="browser_session",
        multi_account=True,
        default_enabled=True,
        default_order=0,
        make=_make_claude,
        login=LoginSpec(
            url="https://claude.ai/login",
            window_title="Sign in to Claude",
            prompt_name="Claude",
            hosts=("claude.ai", "anthropic.com"),
        ),
        cookies=CookieSpec(
            domain=".claude.ai",
            name="sessionKey",
            aliases=("sessionKey",),
        ),
        usage_url="https://claude.ai/new#settings/usage",
        settings_hint=_BROWSER_SIGN_IN_HINT.format(name="Claude"),
        paste_cookie=True,
        compact_collapse=True,
    ),
    ProviderSpec(
        id="codex",
        display_name="Codex",
        settings_label="Codex",
        short_label="Cx",
        auth="browser_session",
        multi_account=True,
        default_enabled=True,
        default_order=1,
        make=_make_codex,
        login=LoginSpec(
            url="https://chatgpt.com/auth/login",
            window_title="Sign in to ChatGPT",
            prompt_name="ChatGPT",
            hosts=(
                "chatgpt.com",
                "openai.com",
                "oaistatic.com",
                "oaiusercontent.com",
            ),
        ),
        cookies=CookieSpec(
            domain=".chatgpt.com",
            name="next-auth.session-token",
            aliases=(
                "next-auth.session-token",
                "__Secure-next-auth.session-token",
                "next-auth.session-token.0",
                "next-auth.session-token.1",
                "__Secure-next-auth.session-token.0",
                "__Secure-next-auth.session-token.1",
            ),
            extra_auth_names=("__Secure-oai-is",),
        ),
        usage_url="https://chatgpt.com/settings/usage?tab=overview",
        settings_hint=_BROWSER_SIGN_IN_HINT.format(name="Codex"),
        paste_cookie=True,
        compact_collapse=True,
    ),
    ProviderSpec(
        id="opencode_go",
        display_name="OpenCode",
        settings_label="OpenCode",
        short_label="Go",
        auth="api_key",
        multi_account=True,
        default_enabled=False,
        default_order=2,
        make=_make_opencode_go,
        settings_hint=(
            "Name each OpenCode Go subscription and enter its API key. "
            "Sign in and copy a key at <a style='color:#60a5fa;' "
            "href='https://opencode.ai/auth'>opencode.ai/auth</a>. Every "
            "row has an independent key, tile, and gauge colors; keys are "
            "stored in your system keychain."
        ),
        compact_collapse=True,
        api_key_entry="opencode-go-key-{account_id}",
        api_key_placeholder="oc_sk_...",
    ),
    ProviderSpec(
        id="copilot",
        display_name="Copilot",
        settings_label="GitHub Copilot",
        short_label="Cp",
        auth="single",
        multi_account=False,
        default_enabled=True,
        default_order=3,
        make=_make_copilot,
    ),
    ProviderSpec(
        id="openrouter",
        display_name="OpenRouter",
        settings_label="OpenRouter",
        short_label="OR",
        auth="single",
        multi_account=False,
        default_enabled=False,
        default_order=4,
        make=_make_openrouter,
    ),
    ProviderSpec(
        id="antigravity",
        display_name="Antigravity",
        settings_label="Antigravity",
        short_label="Ag",
        auth="single",
        multi_account=False,
        default_enabled=False,
        default_order=5,
        make=_make_antigravity,
    ),
)

# Every spec, in default provider order.
PROVIDER_SPECS: tuple[ProviderSpec, ...] = tuple(
    sorted(_DECLARED_SPECS, key=lambda spec: spec.default_order)
)
# Read through provider_spec() at call time, so a test can register a
# test-only spec with monkeypatch.setitem.
_SPECS_BY_ID: dict[str, ProviderSpec] = {spec.id: spec for spec in PROVIDER_SPECS}


def provider_spec(provider_id: str | None) -> ProviderSpec | None:
    """The spec registered under ``provider_id`` (a kind, not an account id)."""
    if provider_id is None:
        return None
    return _SPECS_BY_ID.get(provider_id)


def account_specs() -> tuple[ProviderSpec, ...]:
    """Specs whose accounts live in ``Config.browser_accounts``."""
    return tuple(spec for spec in PROVIDER_SPECS if spec.multi_account)


def single_specs() -> tuple[ProviderSpec, ...]:
    """Single-instance specs: one tile, keyed by the spec id."""
    return tuple(spec for spec in PROVIDER_SPECS if not spec.multi_account)


def account_kinds() -> tuple[str, ...]:
    return tuple(spec.id for spec in account_specs())


def browser_session_kinds() -> tuple[str, ...]:
    return tuple(
        spec.id for spec in PROVIDER_SPECS if spec.auth == "browser_session"
    )


def api_key_kinds() -> tuple[str, ...]:
    return tuple(spec.id for spec in PROVIDER_SPECS if spec.auth == "api_key")


def single_kinds() -> tuple[str, ...]:
    return tuple(spec.id for spec in single_specs())


def account_family(provider_id: str) -> str | None:
    """The account kind an id belongs to by its shape, or None.

    An account of kind ``k`` has id ``k`` (the default account) or
    ``k-<suffix>`` (accounts added later). Single-instance ids belong to no
    family.
    """
    for kind in account_kinds():
        if provider_id == kind or provider_id.startswith(f"{kind}-"):
            return kind
    return None


def has_auth_cookie(kind: str, names: Iterable[str]) -> bool:
    """Whether cookie ``names`` include one that proves a ``kind`` session."""
    spec = provider_spec(kind)
    if spec is None or spec.cookies is None:
        return False
    present = set(names)
    return bool(present & set(spec.cookies.aliases)) or bool(
        present & set(spec.cookies.extra_auth_names)
    )


def sign_in_hosts() -> tuple[str, ...]:
    """Provider host suffixes allowed in the embedded sign-in browser."""
    return tuple(
        host
        for spec in PROVIDER_SPECS
        if spec.login is not None
        for host in spec.login.hosts
    )
