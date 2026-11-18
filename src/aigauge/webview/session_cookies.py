"""Log when a provider's session cookies are issued or renewed.

A diagnostic for sessions that expire on a fixed schedule even though the
scraper keeps loading the site: the profile's Cookies SQLite file is locked
while the app runs, so the app watches the cookie store itself. Cookie values
are never read, stored or logged.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..providers.registry import PROVIDER_SPECS

log = logging.getLogger("aigauge.webview.session_cookies")

# An expiry that moves by less than this is a cookie sliding on every request
# and stays at DEBUG, so a busy cookie costs about one INFO line per hour.
_RENEW_THRESHOLD_SECONDS = 3600

_SESSION = "session"

_Key = tuple[str, str, str]


def _spec_cookie_names() -> frozenset[str]:
    names: set[str] = set()
    for spec in PROVIDER_SPECS:
        if spec.cookies is None:
            continue
        if spec.cookies.name:
            names.add(spec.cookies.name)
        names.update(spec.cookies.aliases)
    return frozenset(names)


class _Seen:
    """What was last logged at INFO for one cookie key."""

    __slots__ = ("text", "secs", "adds")

    def __init__(self, text: str, secs: int | None) -> None:
        self.text = text
        self.secs = secs  # None for a session cookie
        self.adds = 0  # adds since the last INFO line


class SessionCookieWatcher:
    """Receives a profile's ``cookieAdded`` and ``cookieRemoved`` signals."""

    def __init__(self, profile_id: str) -> None:
        self._profile_id = profile_id
        self._names = _spec_cookie_names()
        self._seen: dict[_Key, _Seen] = {}

    def _watched(self, name: str) -> bool:
        return name in self._names or "session" in name.lower()

    @staticmethod
    def _describe(cookie) -> tuple[str, str, str, str, int | None]:
        """Return name, domain, path, expiry text and expiry epoch seconds."""
        name = bytes(cookie.name()).decode("utf-8", errors="replace")
        domain = str(cookie.domain())
        path = str(cookie.path())
        if cookie.isSessionCookie():
            return name, domain, path, _SESSION, None
        secs = int(cookie.expirationDate().toUTC().toSecsSinceEpoch())
        text = datetime.fromtimestamp(secs, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%MZ"
        )
        return name, domain, path, text, secs

    def on_cookie_added(self, cookie) -> None:
        try:
            name, domain, path, text, secs = self._describe(cookie)
            if not self._watched(name):
                return
            key = (name, domain, path)
            seen = self._seen.get(key)
            if seen is None:
                self._seen[key] = _Seen(text, secs)
                log.info(
                    "session cookie seen profile=%s name=%s domain=%s expires=%s",
                    self._profile_id,
                    name,
                    domain,
                    text,
                )
                return
            seen.adds += 1
            flipped = (seen.secs is None) != (secs is None)
            moved = (
                secs is not None
                and seen.secs is not None
                and abs(secs - seen.secs) >= _RENEW_THRESHOLD_SECONDS
            )
            if flipped or moved:
                log.info(
                    "session cookie renewed profile=%s name=%s domain=%s "
                    "expires_from=%s expires_to=%s reissues=%d",
                    self._profile_id,
                    name,
                    domain,
                    seen.text,
                    text,
                    seen.adds,
                )
                seen.text = text
                seen.secs = secs
                seen.adds = 0
            else:
                log.debug(
                    "session cookie reissued profile=%s name=%s domain=%s "
                    "expires=%s",
                    self._profile_id,
                    name,
                    domain,
                    text,
                )
        except Exception:  # a Qt slot must not raise
            log.debug("session cookie add handler failed", exc_info=True)

    def on_cookie_removed(self, cookie) -> None:
        # Chromium emits a removal for every overwrite, so this is noise. The
        # key and its last logged expiry stay, so a later add still compares.
        try:
            name, domain, _path, _text, _secs = self._describe(cookie)
            if self._watched(name):
                log.debug(
                    "session cookie removed profile=%s name=%s domain=%s",
                    self._profile_id,
                    name,
                    domain,
                )
        except Exception:
            log.debug("session cookie remove handler failed", exc_info=True)
