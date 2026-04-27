from __future__ import annotations

from PyQt6.QtWebEngineCore import QWebEngineProfile

from ..config import webview_profile_dir

_REALISTIC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

_profiles: dict[str, QWebEngineProfile] = {}


def get_profile(provider: str) -> QWebEngineProfile:
    """Return a per-provider persistent QWebEngineProfile.

    Profiles share the process but keep separate cookie/cache stores on disk so
    that signing into Claude doesn't overlap with ChatGPT cookies.
    """
    if provider in _profiles:
        return _profiles[provider]

    storage_dir = webview_profile_dir(provider)
    storage_dir.mkdir(parents=True, exist_ok=True)

    profile = QWebEngineProfile(f"usage-view-{provider}")
    profile.setPersistentStoragePath(str(storage_dir))
    profile.setCachePath(str(storage_dir / "cache"))
    profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
    )
    profile.setHttpUserAgent(_REALISTIC_UA)

    _profiles[provider] = profile
    return profile
