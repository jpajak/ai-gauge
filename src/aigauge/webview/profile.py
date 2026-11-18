from __future__ import annotations

import logging

from PyQt6.QtCore import QT_VERSION_STR
from PyQt6.QtWebEngineCore import (
    QWebEngineProfile,
    qWebEngineChromiumSecurityPatchVersion,
    qWebEngineChromiumVersion,
    qWebEngineVersion,
)

from ..config import Config, webview_profile_dir
from .browser_identity import apply_browser_identity, detect_browser_identity
from .session_cookies import SessionCookieWatcher

log = logging.getLogger("aigauge.webview.profile")

_REALISTIC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

# The Console is signed in through the user's own browser, so its profile sends
# that browser's identity, not the fixed one (does the Console then renew?).
_SIGNIN_IDENTITY_PROVIDERS = frozenset({"claude_api"})

_profiles: dict[str, QWebEngineProfile] = {}
# Held so a watcher is not garbage collected while its profile lives.
_cookie_watchers: dict[str, SessionCookieWatcher] = {}


def _apply_signin_identity(provider: str, profile: QWebEngineProfile) -> str | None:
    """Send the sign-in browser's identity; return its user agent, or None."""
    try:
        identity = detect_browser_identity(Config.load().sign_in_browser)
        if identity is None:
            log.info("browser identity provider=%s applied=False reason=not found", provider)
            return None
        apply_browser_identity(profile, identity)
    except Exception as exc:  # noqa: BLE001 - never break get_profile
        log.info("browser identity provider=%s applied=False reason=%s", provider, exc)
        return None
    log.info(
        "browser identity provider=%s browser=%s version=%s applied=True",
        provider,
        identity.browser_id,
        identity.full_version,
    )
    return identity.user_agent


def get_profile(provider: str) -> QWebEngineProfile:
    """Return a per-provider persistent QWebEngineProfile.

    Profiles share the process but keep separate cookie/cache stores on disk so
    that signing into Claude doesn't overlap with ChatGPT cookies.
    """
    if provider in _profiles:
        return _profiles[provider]

    storage_dir = webview_profile_dir(provider)
    storage_dir.mkdir(parents=True, exist_ok=True)

    profile = QWebEngineProfile(f"ai-gauge-{provider}")
    profile.setPersistentStoragePath(str(storage_dir))
    profile.setCachePath(str(storage_dir / "cache"))
    profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
    )
    user_agent = _REALISTIC_UA
    profile.setHttpUserAgent(user_agent)
    if provider in _SIGNIN_IDENTITY_PROVIDERS:
        user_agent = _apply_signin_identity(provider, profile) or user_agent
    # Providers pick the page language from Accept-Language, which would
    # otherwise follow the OS language; the extractors match English labels.
    profile.setHttpAcceptLanguage("en-US,en")

    log.info(
        "webengine profile created provider=%s storage=%s cache=%s ua=%r "
        "qt=%s webengine=%s chromium=%s chromium_patch=%s",
        provider,
        storage_dir,
        storage_dir / "cache",
        user_agent,
        QT_VERSION_STR,
        qWebEngineVersion(),
        qWebEngineChromiumVersion(),
        qWebEngineChromiumSecurityPatchVersion(),
    )

    watcher = SessionCookieWatcher(provider)
    store = profile.cookieStore()
    store.cookieAdded.connect(watcher.on_cookie_added)
    store.cookieRemoved.connect(watcher.on_cookie_removed)
    _cookie_watchers[provider] = watcher
    # Emits the cookies already on disk through cookieAdded, once, as a baseline.
    store.loadAllCookies()

    _profiles[provider] = profile
    return profile
