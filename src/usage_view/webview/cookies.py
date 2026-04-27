from __future__ import annotations

from http.cookies import SimpleCookie

from PyQt6.QtCore import QByteArray, QDateTime, QUrl
from PyQt6.QtNetwork import QNetworkCookie

from ..config import (
    COOKIE_DOMAINS,
    COOKIE_NAME_ALIASES,
    COOKIE_NAMES,
    get_provider_cookie,
)
from .profile import get_profile

# 60-day expiry — Claude/ChatGPT session tokens last weeks; we re-set on each
# launch anyway, so this is just to keep the cookie persistent across the
# WebEngine restart cycle.
_COOKIE_TTL_DAYS = 60


def _parse_name_value_pairs(cookie_text: str) -> list[tuple[str, str]]:
    parsed: list[tuple[str, str]] = []
    try:
        jar = SimpleCookie()
        jar.load(cookie_text.replace("\r\n", "; ").replace("\n", "; "))
        for name, morsel in jar.items():
            parsed.append((name, morsel.value))
    except Exception:  # noqa: BLE001 - fall through to the manual parser
        parsed = []

    if parsed:
        return parsed

    for part in cookie_text.replace("\r\n", ";").replace("\n", ";").split(";"):
        if "=" not in part:
            continue
        name, item_value = part.split("=", 1)
        name = name.strip()
        if name:
            parsed.append((name, item_value.strip()))
    return parsed


def _has_auth_cookie(provider: str, pairs: list[tuple[str, str]]) -> bool:
    names = {name for name, _ in pairs}
    aliases = set(COOKIE_NAME_ALIASES.get(provider, ()))
    if provider == "codex":
        return bool(names & aliases) or "__Secure-oai-is" in names
    return bool(names & aliases)


def _parse_cookie_pairs(provider: str, pasted: str) -> list[tuple[str, str]]:
    """Parse raw values, `name=value` lines, or a full Cookie header.

    Browser DevTools has changed ChatGPT's auth cookie name over time, and very
    large values may be split as `.0` / `.1` cookies. If the user pastes only a
    raw value, inject it under every known non-split alias for the provider.
    When a full request Cookie header is pasted, keep all cookies for that
    provider; modern ChatGPT sessions can need more than the next-auth token.
    """
    value = pasted.strip()
    if not value:
        return []

    aliases = COOKIE_NAME_ALIASES.get(provider, (COOKIE_NAMES.get(provider, ""),))
    alias_set = {a for a in aliases if a}
    raw_names = [a for a in aliases if a and not a.endswith((".0", ".1"))]
    raw_lines = [line.strip() for line in value.splitlines() if line.strip()]
    if provider == "codex" and len(raw_lines) > 1 and all("=" not in line for line in raw_lines):
        return [
            (f"__Secure-next-auth.session-token.{i}", line)
            for i, line in enumerate(raw_lines[:2])
        ]

    cookie_text = value
    if cookie_text.lower().startswith("cookie:"):
        cookie_text = cookie_text.split(":", 1)[1].strip()

    parsed: list[tuple[str, str]] = []
    if "=" in cookie_text:
        keep_all = ";" in cookie_text
        all_pairs = _parse_name_value_pairs(cookie_text)
        if keep_all and _has_auth_cookie(provider, all_pairs):
            parsed = all_pairs
        else:
            parsed = [
                (name, item_value)
                for name, item_value in all_pairs
                if name in alias_set
            ]
        return parsed

    if parsed:
        return parsed

    return [(name, value) for name in raw_names]


def _set_cookie(provider: str, name: str, value: str) -> None:
    domain = COOKIE_DOMAINS[provider]
    profile = get_profile(provider)
    store = profile.cookieStore()

    cookie = QNetworkCookie(
        QByteArray(name.encode("utf-8")),
        QByteArray(value.strip().encode("utf-8")),
    )
    if not name.startswith("__Host-"):
        cookie.setDomain(domain)
    cookie.setPath("/")
    cookie.setSecure(True)
    cookie.setHttpOnly(True)
    cookie.setExpirationDate(QDateTime.currentDateTime().addDays(_COOKIE_TTL_DAYS))

    # Origin URL must match the cookie domain (drop the leading dot).
    origin = QUrl(f"https://{domain.lstrip('.')}/")
    store.setCookie(cookie, origin)


def inject_session_cookie(provider: str, value: str) -> bool:
    """Push a cookie into the WebEngine profile so subsequent loads are signed-in.

    Returns True if a cookie was injected, False if no name/domain mapping exists
    for this provider.
    """
    domain = COOKIE_DOMAINS.get(provider)
    if not COOKIE_NAMES.get(provider) or not domain:
        return False

    pairs = _parse_cookie_pairs(provider, value)
    for name, cookie_value in pairs:
        _set_cookie(provider, name, cookie_value)
    return bool(pairs)


def hydrate_all_from_keyring() -> list[str]:
    """On startup, push any saved cookies into their respective WebEngine profiles.

    Returns the list of providers that had a cookie loaded.
    """
    loaded: list[str] = []
    for provider in COOKIE_NAMES:
        value = get_provider_cookie(provider)
        if value and inject_session_cookie(provider, value):
            loaded.append(provider)
    return loaded
