from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import keyring
from pydantic import BaseModel, Field

APP_NAME = "usage-view"
KEYRING_SERVICE = "usage-view"
KEYRING_GITHUB_PAT = "github-pat"

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
    """%APPDATA%/usage-view on Windows, ~/.config/usage-view elsewhere."""
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / ".config" / APP_NAME


def webview_profile_dir(provider: str) -> Path:
    return app_data_dir() / "profiles" / provider


def config_path() -> Path:
    return app_data_dir() / "config.json"


class WindowState(BaseModel):
    x: int | None = None
    y: int | None = None
    width: int = 340
    height: int = 220
    always_on_top: bool = True
    opacity: float = Field(default=1.0, ge=0.3, le=1.0)


class ProviderToggles(BaseModel):
    claude: bool = True
    codex: bool = True
    copilot: bool = True


class CopilotConfig(BaseModel):
    username: str | None = None
    billing_org: str | None = None
    monthly_quota: int = Field(default=300, ge=1)  # Pro=300, Pro+=1500, Business=300


class Config(BaseModel):
    active_refresh_interval_minutes: int = Field(default=5, ge=1, le=180)
    refresh_interval_minutes: int = Field(default=60, ge=1, le=180)
    start_with_windows: bool = False
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
    from .secret_storage import load_secret
    return load_secret(KEYRING_GITHUB_PAT)


def set_github_pat(pat: str | None) -> None:
    from .secret_storage import save_secret
    if pat:
        try:
            keyring.set_password(KEYRING_SERVICE, KEYRING_GITHUB_PAT, pat)
        except keyring.errors.KeyringError:
            pass
        save_secret(KEYRING_GITHUB_PAT, pat)
    else:
        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_GITHUB_PAT)
        except keyring.errors.PasswordDeleteError:
            pass
        save_secret(KEYRING_GITHUB_PAT, None)


def _cookie_key(provider: str) -> str:
    return f"cookie-{provider}"


def get_provider_cookie(provider: str) -> str | None:
    # DPAPI-encrypted file — Credential Manager has a 2.5KB blob limit that
    # ChatGPT's session JWT exceeds.
    from .secret_storage import load_secret
    return load_secret(_cookie_key(provider))


def set_provider_cookie(provider: str, value: str | None) -> None:
    from .secret_storage import save_secret
    save_secret(_cookie_key(provider), value)
