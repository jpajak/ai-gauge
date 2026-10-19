from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal

import keyring
from pydantic import BaseModel, Field

from .platforms import APP_NAME, get_platform
from .providers.registry import (
    PROVIDER_SPECS,
    account_family,
    account_kinds,
    provider_spec,
)

log = logging.getLogger("aigauge.config")

KEYRING_SERVICE = "ai-gauge"
KEYRING_GITHUB_PAT = "github-pat"
KEYRING_OPENROUTER_KEY = "openrouter-key"
KEYRING_OPENROUTER_MGMT_KEY = "openrouter-mgmt-key"
WINDOW_WIDTH = 340
WINDOW_MIN_WIDTH = 240
WINDOW_MAX_WIDTH = 900
WINDOW_MIN_HEIGHT = 80
WINDOW_MAX_HEIGHT = 900
# Compact mode's true floor: one chip row (18px plus its 4px pace notch) and
# the pill's 4/6 vertical margins. Anything above that — the header, a second
# wrapped chip row — is added by the content itself, so a pill wide enough for
# a single row is only one row tall.
WINDOW_COLLAPSED_MIN_HEIGHT = 32
# The collapsed pill sizes itself to its own content and can be dragged
# narrower than the expanded panel's floor — a single-provider user should not
# be stuck with a mostly-empty 340px strip (issue #7).
WINDOW_COLLAPSED_MIN_WIDTH = 150

SnapCorner = Literal["top_left", "top_right", "bottom_left", "bottom_right"]

# Per-provider session cookie names (HttpOnly cookies you can't read via JS),
# derived from the provider registry. COOKIE_NAMES is the primary name shown
# in the UI. COOKIE_NAME_ALIASES covers provider auth migrations and split
# cookies copied from browser DevTools.
COOKIE_NAMES = {
    spec.id: spec.cookies.name
    for spec in PROVIDER_SPECS
    if spec.cookies is not None and spec.cookies.name
}
COOKIE_NAME_ALIASES = {
    spec.id: spec.cookies.aliases
    for spec in PROVIDER_SPECS
    if spec.cookies is not None
}
COOKIE_DOMAINS = {
    spec.id: spec.cookies.domain
    for spec in PROVIDER_SPECS
    if spec.cookies is not None
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
    width: int = Field(default=WINDOW_WIDTH, ge=WINDOW_MIN_WIDTH, le=WINDOW_MAX_WIDTH)
    # Collapsed-mode width, tracked separately from the expanded width so the
    # two modes don't fight over one number. None means "fit to content".
    collapsed_width: int | None = Field(
        default=None, ge=WINDOW_COLLAPSED_MIN_WIDTH, le=WINDOW_MAX_WIDTH
    )
    # Legacy 0.7.0 fields retained for config compatibility. Expanded height is
    # content-owned; only width is restored or changed by the resize grip.
    height: int = Field(default=220, ge=WINDOW_MIN_HEIGHT, le=WINDOW_MAX_HEIGHT)
    manually_resized: bool = False
    collapsed: bool = False
    show_header: bool = True
    always_on_top: bool = True
    snap_to_corners: bool = True
    snap_corner: SnapCorner | None = None
    opacity: float = Field(default=0.8, ge=0.3, le=1.0)
    fade_when_inactive: bool = False
    # Draw the panel with square corners instead of an 8px radius. Rounded
    # corners are masked out of the window shape, which is correct everywhere
    # but leaves an aliased edge; square corners avoid the mask entirely and
    # are the safest choice on remote desktops that render masks poorly.
    square_corners: bool = False
    # Whole-widget zoom. >1 enlarges for high-resolution (4K) displays; <1
    # makes it more compact. Floor is 0.75 — below that the fixed 10-12px fonts
    # become illegible. Applied via Qt's QT_SCALE_FACTOR at launch — see
    # qt_scale_factor_env().
    ui_scale: float = Field(default=1.0, ge=0.75, le=4.0)


# PROVIDER_SPECS is sorted by each spec's default_order.
DEFAULT_PROVIDER_ORDER: tuple[str, ...] = tuple(spec.id for spec in PROVIDER_SPECS)


class ProviderToggles(BaseModel):
    # Explicit fields, one per registry spec, so the config schema and its
    # migrations stay static. tests/test_provider_registry.py checks the two
    # stay in step.
    claude: bool = True
    codex: bool = True
    copilot: bool = True
    openrouter: bool = False
    antigravity: bool = False
    opencode_go: bool = False
    minimax: bool = False


class ColorThresholds(BaseModel):
    # Inclusive integer bands matching AI Gauge's original severity behavior:
    # green below 60%, yellow at 60-79%, orange at 80-94%, red at 95%+.
    green_max: int = Field(default=59, ge=0, le=100)
    yellow_max: int = Field(default=79, ge=0, le=100)
    orange_max: int = Field(default=94, ge=0, le=100)
    green_color: str = "#22c55e"
    yellow_color: str = "#f59e0b"
    orange_color: str = "#f97316"
    red_color: str = "#ef4444"


class BrowserAccount(BaseModel):
    id: str
    kind: str
    name: str | None = None
    enabled: bool = True
    colors: ColorThresholds = Field(default_factory=ColorThresholds)
    # Claude Max plans expose a separate weekly Fable limit. This is per
    # account rather than per provider: plan tier belongs to the subscription,
    # and one signed-in account may be Max while another is not. On by default
    # — plans without the limit report no such row, so the gauge simply does
    # not appear rather than showing a wrong value. Only Claude accounts read
    # this; it is inert on other kinds.
    show_fable: bool = True


class CopilotConfig(BaseModel):
    username: str | None = None
    billing_org: str | None = None
    monthly_quota: int = Field(default=1500, ge=1)  # AI credits; Pro=1500
    colors: ColorThresholds = Field(default_factory=ColorThresholds)


class OpenRouterConfig(BaseModel):
    daily_budget: float | None = Field(default=None, ge=0)
    colors: ColorThresholds = Field(default_factory=ColorThresholds)


class AntigravityConfig(BaseModel):
    colors: ColorThresholds = Field(default_factory=ColorThresholds)
    cli_path: str | None = None
    show_gemini: bool = True
    show_third_party: bool = True


class OpenCodeGoConfig(BaseModel):
    colors: ColorThresholds = Field(default_factory=ColorThresholds)


MCP_PAUSE_MIN = 1
MCP_PAUSE_MAX = 100
McpPauseThreshold = Annotated[int, Field(ge=MCP_PAUSE_MIN, le=MCP_PAUSE_MAX)]


class LocalUsageProviderConfig(BaseModel):
    """Local log tracking for one provider (Claude Code or Codex)."""

    enabled: bool = True
    # All of this provider's local logs count against exactly this account,
    # chosen by stable id. None, or an id that no longer exists, pauses
    # tracking for the provider; it never falls over to another account.
    account_id: str | None = None
    # Override for the detected log folder. Claude: a `projects` folder.
    # Codex: the Codex home folder that holds `sessions`.
    log_root: str | None = None
    # UTC ISO time set by "Start from now"; nothing older is imported.
    start_from: str | None = None
    # Local dates (YYYY-MM-DD) the provider changed its usage limits. The
    # allowance trend uses them as before/after comparison boundaries.
    limit_changes: list[str] = Field(default_factory=list)


class LocalUsageConfig(BaseModel):
    # Off by default. While off, no log file is read and nothing is written.
    enabled: bool = False
    claude: LocalUsageProviderConfig = Field(default_factory=LocalUsageProviderConfig)
    codex: LocalUsageProviderConfig = Field(default_factory=LocalUsageProviderConfig)
    details_view: Literal["model", "day", "trend"] = "model"
    details_range: Literal["week", "session", "today", "7d", "30d"] = "week"


class Config(BaseModel):
    active_refresh_interval_minutes: int = Field(default=5, ge=1, le=180)
    refresh_interval_minutes: int = Field(default=60, ge=1, le=180)
    start_at_login: bool = False
    # Automatic cookie import requires Chromium's DevTools protocol, so the
    # chosen executable must be explicit rather than silently treating Edge as
    # the system default. "ask" is replaced with the user's first selection.
    sign_in_browser: Literal[
        "ask", "chrome", "edge", "brave", "chromium", "embedded"
    ] = "ask"
    providers: ProviderToggles = Field(default_factory=ProviderToggles)
    browser_accounts: list[BrowserAccount] = Field(
        # One default account per multi-account provider; its id is the kind.
        default_factory=lambda: [
            BrowserAccount(id=kind, kind=kind) for kind in account_kinds()
        ]
    )
    browser_accounts_version: int = 3
    copilot: CopilotConfig = Field(default_factory=CopilotConfig)
    openrouter: OpenRouterConfig = Field(default_factory=OpenRouterConfig)
    antigravity: AntigravityConfig = Field(default_factory=AntigravityConfig)
    opencode_go: OpenCodeGoConfig = Field(default_factory=OpenCodeGoConfig)
    # Provider kinds in the order the user arranged them. Empty means the
    # default order; read it through provider_order(), which also places any
    # kind this list does not mention.
    provider_order: list[str] = Field(default_factory=list)
    expanded_tiles: list[str] = Field(default_factory=list)
    collapsed_tiles: list[str] = Field(default_factory=list)
    # Disabled by default so normal GUI usage publishes no MCP cache.
    mcp_enabled: bool = False
    # Account id -> percent used at which cooperating MCP clients should pause.
    mcp_pause_policies: dict[str, McpPauseThreshold] = Field(default_factory=dict)
    local_usage: LocalUsageConfig = Field(default_factory=LocalUsageConfig)
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
            log.exception("Failed to load config from %s; using defaults.", path)
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
            # An explicit list is authoritative. In particular, do not restore
            # account rows after a user removes them post-migration.
            data["browser_accounts"] = [
                item for item in data["browser_accounts"] if isinstance(item, dict)
            ]
        accounts_version = data.get("browser_accounts_version", 1)
        if not isinstance(accounts_version, int):
            accounts_version = 1
        if accounts_version < 2:
            accounts = data.get("browser_accounts")
            if isinstance(accounts, list) and not any(
                item.get("kind") == "opencode_go"
                for item in accounts
                if isinstance(item, dict)
            ):
                legacy_opencode = data.get("opencode_go")
                colors = (
                    legacy_opencode.get("colors")
                    if isinstance(legacy_opencode, dict)
                    else None
                )
                account: dict[str, Any] = {
                    "id": "opencode_go",
                    "kind": "opencode_go",
                    "name": None,
                    "enabled": True,
                }
                if isinstance(colors, dict):
                    account["colors"] = colors
                accounts.append(account)
            data["browser_accounts_version"] = 2
            accounts_version = 2
        if accounts_version < 3:
            accounts = data.get("browser_accounts")
            if isinstance(accounts, list) and not any(
                item.get("kind") == "minimax"
                for item in accounts
                if isinstance(item, dict)
            ):
                accounts.append(
                    {
                        "id": "minimax",
                        "kind": "minimax",
                        "name": None,
                        "enabled": True,
                    }
                )
            data["browser_accounts_version"] = 3
        window = data.get("window")
        if isinstance(window, dict):
            width = window.get("width")
            height = window.get("height")
            if isinstance(width, int):
                window["width"] = max(WINDOW_MIN_WIDTH, min(width, WINDOW_MAX_WIDTH))
            if isinstance(height, int):
                window["height"] = max(WINDOW_MIN_HEIGHT, min(height, WINDOW_MAX_HEIGHT))
            collapsed_width = window.get("collapsed_width")
            if isinstance(collapsed_width, int):
                window["collapsed_width"] = max(
                    WINDOW_COLLAPSED_MIN_WIDTH,
                    min(collapsed_width, WINDOW_MAX_WIDTH),
                )
        copilot = data.get("copilot")
        if isinstance(copilot, dict) and copilot.get("monthly_quota") == 300:
            copilot["monthly_quota"] = 1500
        # Config.load() falls back to a fresh Config on any validation error, so
        # one bad threshold would silently discard every other saved setting.
        # Drop unusable entries here instead: a missing pause policy fails
        # closed at guard time, which is the safe direction.
        policies = data.get("mcp_pause_policies")
        if isinstance(policies, dict):
            data["mcp_pause_policies"] = {
                account_id: threshold
                for account_id, threshold in policies.items()
                if isinstance(account_id, str)
                and isinstance(threshold, int)
                and not isinstance(threshold, bool)
                and MCP_PAUSE_MIN <= threshold <= MCP_PAUSE_MAX
            }
        elif policies is not None:
            data["mcp_pause_policies"] = {}

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
    spec = provider_spec(kind)
    return spec.display_name if spec is not None else kind.title()


def account_display_name(account: BrowserAccount) -> str:
    base = provider_base_name(account.kind)
    label = (account.name or "").strip()
    return f"{base} ({label})" if label else base


def provider_order(config: Config) -> tuple[str, ...]:
    """Return every provider kind in display order.

    The saved order comes first. Kinds it does not mention, such as a provider
    added in a later version, follow in the default order.
    """
    saved = getattr(config, "provider_order", None) or ()
    order = [kind for kind in dict.fromkeys(saved) if kind in DEFAULT_PROVIDER_ORDER]
    order.extend(kind for kind in DEFAULT_PROVIDER_ORDER if kind not in order)
    return tuple(order)


def browser_accounts(
    config: Config,
    *,
    kind: str | None = None,
    enabled_only: bool = False,
) -> list[BrowserAccount]:
    kinds = account_kinds()
    accounts = [
        account
        for account in getattr(config, "browser_accounts", [])
        if account.kind in kinds
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
    return account_family(account_id)


def display_name_for_account(config: Config, account_id: str) -> str:
    account = browser_account(config, account_id)
    if account is not None:
        return account_display_name(account)
    spec = provider_spec(account_id)
    return spec.display_name if spec is not None else account_id


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


def _api_key_entry(kind: str, account_id: str) -> str:
    spec = provider_spec(kind)
    if spec is None or spec.api_key_entry is None:
        raise ValueError(f"{kind!r} is not an API-key provider")
    return spec.api_key_entry.format(account_id=account_id)


def get_account_api_key(kind: str, account_id: str) -> str | None:
    """Load one API-key account's key from the system keychain.

    The keyring entry name comes from the kind's registry spec, so keys saved
    before the registry existed still load.
    """
    entry = _api_key_entry(kind, account_id)
    try:
        key = keyring.get_password(KEYRING_SERVICE, entry)
        return key or None
    except keyring.errors.KeyringError:
        return None


def set_account_api_key(kind: str, account_id: str, value: str | None) -> None:
    """Save, or clear when ``value`` is empty, one account's API key."""
    entry = _api_key_entry(kind, account_id)
    if value:
        keyring.set_password(KEYRING_SERVICE, entry, value)
    else:
        try:
            keyring.delete_password(KEYRING_SERVICE, entry)
        except keyring.errors.KeyringError:
            pass


def get_opencode_go_key(account_id: str = "opencode_go") -> str | None:
    """Load one OpenCode account's API key from the system keychain."""
    return get_account_api_key("opencode_go", account_id)


def set_opencode_go_key(
    account_id: str,
    value: str | None,
) -> None:
    """Save or clear one OpenCode account's API key in the system keychain."""
    set_account_api_key("opencode_go", account_id, value)
