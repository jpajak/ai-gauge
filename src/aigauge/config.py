from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import keyring
from pydantic import BaseModel, Field

from .platforms import APP_NAME, get_platform

KEYRING_SERVICE = "ai-gauge"
KEYRING_GITHUB_PAT = "github-pat"
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


class ProviderToggles(BaseModel):
    claude: bool = True
    claude_design: bool = False
    codex: bool = True
    copilot: bool = True


class CopilotConfig(BaseModel):
    username: str | None = None
    billing_org: str | None = None
    monthly_quota: int = Field(default=300, ge=1)  # Pro=300, Pro+=1500, Business=300


class Config(BaseModel):
    active_refresh_interval_minutes: int = Field(default=5, ge=1, le=180)
    refresh_interval_minutes: int = Field(default=60, ge=1, le=180)
    start_at_login: bool = False
    providers: ProviderToggles = Field(default_factory=ProviderToggles)
    copilot: CopilotConfig = Field(default_factory=CopilotConfig)
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
        window = data.get("window")
        if isinstance(window, dict):
            width = window.get("width")
            height = window.get("height")
            if isinstance(width, int):
                window["width"] = WINDOW_WIDTH
            if isinstance(height, int):
                window["height"] = max(WINDOW_MIN_HEIGHT, min(height, WINDOW_MAX_HEIGHT))

    def save(self) -> None:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.model_dump(), indent=2, default=str),
            encoding="utf-8",
        )


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


def _cookie_key(provider: str) -> str:
    return f"cookie-{provider}"


def get_provider_cookie(provider: str) -> str | None:
    return get_platform().load_secret(_cookie_key(provider))


def set_provider_cookie(provider: str, value: str | None) -> None:
    get_platform().save_secret(_cookie_key(provider), value)
