from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

import keyring
from pydantic import BaseModel, Field

from .platforms import APP_NAME, get_platform

KEYRING_SERVICE = "ai-gauge"
KEYRING_GITHUB_PAT = "github-pat"
KEYRING_OPENROUTER_KEY = "openrouter-key"
KEYRING_OPENROUTER_MGMT_KEY = "openrouter-mgmt-key"
WINDOW_WIDTH = 340
WINDOW_MIN_HEIGHT = 80
WINDOW_MAX_HEIGHT = 420
WINDOW_COLLAPSED_HEIGHT = 58

# Per-provider session cookie names (HttpOnly cookies you can't read via JS).
# COOKIE_NAMES is the primary name shown in the UI. COOKIE_NAME_ALIASES covers
# provider auth migrations and split cookies copied from browser DevTools.
COOKIE_NAMES = {
    "claude": "sessionKey",
    "codex": "next-auth.session-token",
}
COOKIE_NAME_ALIASES = {
    "claude": ("sessionKey",),
    "codex": (
        "next-auth.session-token",
        "__Secure-next-auth.session-token",
        "next-auth.session-token.0",
        "next-auth.session-token.1",
        "__Secure-next-auth.session-token.0",
        "__Secure-next-auth.session-token.1",
    ),
}
COOKIE_DOMAINS = {
    "claude": ".claude.ai",
    "codex": ".chatgpt.com",
}


def app_data_dir() -> Path:
    """Per-OS config / log / secrets directory.

    - Windows: ``%APPDATA%/ai-gauge``
    - macOS:   ``~/Library/Application Support/ai-gauge``
    - Linux:   ``$XDG_CONFIG_HOME/ai-gauge`` (or ``~/.config/ai-gauge``)
    """
    return get_platform().app_data_dir()


def webview_profile_dir(provider: str) -> Path:
    return app_data_dir() / "profiles" / provider


def config_path() -> Path:
    return app_data_dir() / "config.json"


class WindowState(BaseModel):
    x: int | None = None
    y: int | None = None
    width: int = WINDOW_WIDTH
    height: int = Field(default=220, ge=WINDOW_MIN_HEIGHT, le=WINDOW_MAX_HEIGHT)
    collapsed: bool = False
    always_on_top: bool = True
    opacity: float = Field(default=1.0, ge=0.3, le=1.0)
    # Whole-widget zoom. >1 enlarges for high-resolution (4K) displays; <1
    # makes it more compact. Floor is 0.75 — below that the fixed 10-12px fonts
    # become illegible. Applied via Qt's QT_SCALE_FACTOR at launch — see
    # qt_scale_factor_env().
    ui_scale: float = Field(default=1.0, ge=0.75, le=4.0)


class ProviderToggles(BaseModel):
    claude: bool = True
    codex: bool = True
    copilot: bool = True
    openrouter: bool = False


class BrowserAccount(BaseModel):
    id: str
    kind: str
    name: str | None = None
    enabled: bool = True


class CopilotConfig(BaseModel):
    username: str | None = None
    billing_org: str | None = None
    monthly_quota: int = Field(default=1500, ge=1)  # AI credits; Pro=1500


class OpenRouterConfig(BaseModel):
    daily_budget: float | None = Field(default=None, ge=0)


class Config(BaseModel):
    active_refresh_interval_minutes: int = Field(default=5, ge=1, le=180)
    refresh_interval_minutes: int = Field(default=60, ge=1, le=180)
    start_at_login: bool = False
    providers: ProviderToggles = Field(default_factory=ProviderToggles)
    browser_accounts: list[BrowserAccount] = Field(
        default_factory=lambda: [
            BrowserAccount(id="claude", kind="claude"),
            BrowserAccount(id="codex", kind="codex"),
        ]
    )
    copilot: CopilotConfig = Field(default_factory=CopilotConfig)
    openrouter: OpenRouterConfig = Field(default_factory=OpenRouterConfig)
    expanded_tiles: list[str] = Field(default_factory=list)
    window: WindowState = Field(default_factory=WindowState)

    @classmethod
    def load(cls) -> Config:
        path = config_path()
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cls._migrate(data)
            return cls.model_validate(data)
        except Exception:
            return cls()

    @staticmethod
    def _migrate(data: dict[str, Any]) -> None:
        # 0.1.x had a single refresh_interval_minutes value. Preserve that as
        # the active cadence and let the new idle cap default to 60 minutes.
        if "active_refresh_interval_minutes" not in data:
            old_interval = data.get("refresh_interval_minutes")
            if isinstance(old_interval, int):
                data["active_refresh_interval_minutes"] = old_interval
                data["refresh_interval_minutes"] = 60
        # 0.5.x renamed start_with_windows to start_at_login (cross-platform).
        if "start_at_login" not in data and "start_with_windows" in data:
            data["start_at_login"] = bool(data.pop("start_with_windows"))
        providers = data.get("providers")
        if not isinstance(providers, dict):
            providers = {}
        if "browser_accounts" not in data:
            data["browser_accounts"] = [
                {
                    "id": "claude",
                    "kind": "claude",
                    "name": None,
                    "enabled": bool(providers.get("claude", True)),
                },
                {
                    "id": "codex",
                    "kind": "codex",
                    "name": None,
                    "enabled": bool(providers.get("codex", True)),
                },
            ]
        elif isinstance(data.get("browser_accounts"), list):
            accounts = [
                item for item in data["browser_accounts"] if isinstance(item, dict)
            ]
            ids = {str(item.get("id") or "") for item in accounts}
            if "claude" not in ids:
                accounts.insert(
                    0,
                    {
                        "id": "claude",
                        "kind": "claude",
                        "name": None,
                        "enabled": bool(providers.get("claude", True)),
                    },
                )
            if "codex" not in ids:
                accounts.append(
                    {
                        "id": "codex",
                        "kind": "codex",
                        "name": None,
                        "enabled": bool(providers.get("codex", True)),
                    }
                )
            data["browser_accounts"] = accounts
        window = data.get("window")
        if isinstance(window, dict):
            width = window.get("width")
            height = window.get("height")
            if isinstance(width, int):
                window["width"] = WINDOW_WIDTH
            if isinstance(height, int):
                window["height"] = max(WINDOW_MIN_HEIGHT, min(height, WINDOW_MAX_HEIGHT))
        copilot = data.get("copilot")
        if isinstance(copilot, dict) and copilot.get("monthly_quota") == 300:
            copilot["monthly_quota"] = 1500

    def save(self) -> None:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.model_dump(), indent=2, default=str),
            encoding="utf-8",
        )


def qt_scale_factor_env(config: Config) -> str | None:
    """QT_SCALE_FACTOR string for the configured UI scale, or None at 1.0.

    Qt reads QT_SCALE_FACTOR once, before QApplication is constructed, and uses
    it to scale the whole (otherwise fixed-pixel) widget — the lever behind the
    Settings "UI scale" option. Returns None when the scale is effectively 1.0
    so Qt's own per-monitor DPI handling is left untouched.
    """
    scale = float(getattr(config.window, "ui_scale", 1.0) or 1.0)
    if abs(scale - 1.0) <= 1e-3:
        return None
    return f"{scale:g}"


def provider_base_name(kind: str) -> str:
    return {"claude": "Claude", "codex": "Codex"}.get(kind, kind.title())


def account_display_name(account: BrowserAccount) -> str:
    base = provider_base_name(account.kind)
    label = (account.name or "").strip()
    return f"{base} ({label})" if label else base


def browser_accounts(
    config: Config,
    *,
    kind: str | None = None,
    enabled_only: bool = False,
) -> list[BrowserAccount]:
    accounts = [
        account
        for account in getattr(config, "browser_accounts", [])
        if account.kind in ("claude", "codex")
    ]
    if kind is not None:
        accounts = [account for account in accounts if account.kind == kind]
    if enabled_only:
        accounts = [account for account in accounts if account.enabled]
    return accounts


def browser_account(config: Config, account_id: str) -> BrowserAccount | None:
    for account in browser_accounts(config):
        if account.id == account_id:
            return account
    return None


def account_kind(config: Config, account_id: str) -> str | None:
    account = browser_account(config, account_id)
    if account is not None:
        return account.kind
    if account_id in ("claude", "codex"):
        return account_id
    if account_id.startswith("claude-"):
        return "claude"
    if account_id.startswith("codex-"):
        return "codex"
    return None


def display_name_for_account(config: Config, account_id: str) -> str:
    account = browser_account(config, account_id)
    if account is not None:
        return account_display_name(account)
    return {
        "claude": "Claude",
        "codex": "Codex",
        "copilot": "Copilot",
        "openrouter": "OpenRouter",
    }.get(account_id, account_id)


def generate_browser_account_id(config: Config, kind: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", kind.lower()).strip("-") or "account"
    existing = {account.id for account in config.browser_accounts}
    while True:
        candidate = f"{slug}-{uuid.uuid4().hex[:8]}"
        if candidate not in existing:
            return candidate


def get_github_pat() -> str | None:
    try:
        pat = keyring.get_password(KEYRING_SERVICE, KEYRING_GITHUB_PAT)
        if pat:
            return pat
    except keyring.errors.KeyringError:
        pass
    legacy_pat = _load_legacy_github_pat()
    if not legacy_pat:
        return None
    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_GITHUB_PAT, legacy_pat)
    except keyring.errors.KeyringError:
        return legacy_pat
    _delete_legacy_github_pat()
    return legacy_pat


def set_github_pat(pat: str | None) -> None:
    if pat:
        keyring.set_password(KEYRING_SERVICE, KEYRING_GITHUB_PAT, pat)
        _delete_legacy_github_pat()
    else:
        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_GITHUB_PAT)
        except keyring.errors.KeyringError:
            pass
        _delete_legacy_github_pat()


def _load_legacy_github_pat() -> str | None:
    from . import secret_storage

    return secret_storage.load_secret(KEYRING_GITHUB_PAT)


def _delete_legacy_github_pat() -> None:
    from . import secret_storage

    try:
        secret_storage.save_secret(KEYRING_GITHUB_PAT, None)
    except RuntimeError:
        # Non-Windows production hosts refuse to write plaintext secrets.dat.
        # PAT storage itself has already used the system keyring; this cleanup
        # is only for the old sidecar-file migration path.
        pass


def get_openrouter_key() -> str | None:
    try:
        key = keyring.get_password(KEYRING_SERVICE, KEYRING_OPENROUTER_KEY)
        if key:
            return key
    except keyring.errors.KeyringError:
        pass
    return None


def set_openrouter_key(key: str | None) -> None:
    if key:
        keyring.set_password(KEYRING_SERVICE, KEYRING_OPENROUTER_KEY, key)
    else:
        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_OPENROUTER_KEY)
        except keyring.errors.KeyringError:
            pass


def get_openrouter_mgmt_key() -> str | None:
    try:
        key = keyring.get_password(KEYRING_SERVICE, KEYRING_OPENROUTER_MGMT_KEY)
        if key:
            return key
    except keyring.errors.KeyringError:
        pass
    return None


def set_openrouter_mgmt_key(key: str | None) -> None:
    if key:
        keyring.set_password(KEYRING_SERVICE, KEYRING_OPENROUTER_MGMT_KEY, key)
    else:
        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_OPENROUTER_MGMT_KEY)
        except keyring.errors.KeyringError:
            pass


def _cookie_key(provider: str) -> str:
    return f"cookie-{provider}"


def get_provider_cookie(provider: str) -> str | None:
    return get_platform().load_secret(_cookie_key(provider))


def set_provider_cookie(provider: str, value: str | None) -> None:
    get_platform().save_secret(_cookie_key(provider), value)
