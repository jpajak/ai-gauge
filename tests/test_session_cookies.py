import logging
from types import SimpleNamespace

from PyQt6.QtCore import QDateTime, Qt
from PyQt6.QtNetwork import QNetworkCookie

from aigauge.webview import session_cookies
from aigauge.webview.session_cookies import SessionCookieWatcher

LOGGER = "aigauge.webview.session_cookies"
SECRET = "s3cr3t-value-do-not-log"
BASE = 1_800_000_000  # 2027-01-15T08:00:00Z


def make_cookie(
    name="sessionKey",
    expires=BASE,
    domain=".platform.claude.com",
    path="/",
    value=SECRET,
):
    cookie = QNetworkCookie(name.encode(), value.encode())
    cookie.setDomain(domain)
    cookie.setPath(path)
    if expires is not None:
        cookie.setExpirationDate(QDateTime.fromSecsSinceEpoch(expires, Qt.TimeSpec.UTC))
    return cookie


def info_lines(caplog):
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == LOGGER and r.levelno == logging.INFO
    ]


def make_watcher(caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    return SessionCookieWatcher("claude-api")


def test_new_key_logs_seen(caplog):
    watcher = make_watcher(caplog)
    watcher.on_cookie_added(make_cookie())
    assert info_lines(caplog) == [
        "session cookie seen profile=claude-api name=sessionKey "
        "domain=.platform.claude.com expires=2027-01-15T08:00Z"
    ]


def test_session_cookie_expiry_is_the_word_session(caplog):
    watcher = make_watcher(caplog)
    watcher.on_cookie_added(make_cookie(expires=None))
    assert info_lines(caplog)[0].endswith("expires=session")


def test_same_expiry_logs_nothing_at_info(caplog):
    watcher = make_watcher(caplog)
    watcher.on_cookie_added(make_cookie())
    watcher.on_cookie_added(make_cookie())
    assert len(info_lines(caplog)) == 1


def test_expiry_moved_an_hour_logs_renewed_with_reissue_count(caplog):
    watcher = make_watcher(caplog)
    watcher.on_cookie_added(make_cookie())
    watcher.on_cookie_added(make_cookie(expires=BASE + 60))  # under an hour
    watcher.on_cookie_added(make_cookie(expires=BASE + 3600))
    assert info_lines(caplog)[1:] == [
        "session cookie renewed profile=claude-api name=sessionKey "
        "domain=.platform.claude.com expires_from=2027-01-15T08:00Z "
        "expires_to=2027-01-15T09:00Z reissues=2"
    ]
    # The count restarts after an INFO line.
    watcher.on_cookie_added(make_cookie(expires=BASE + 7200))
    assert info_lines(caplog)[-1].endswith("reissues=1")


def test_move_under_an_hour_is_not_renewed(caplog):
    watcher = make_watcher(caplog)
    watcher.on_cookie_added(make_cookie())
    watcher.on_cookie_added(make_cookie(expires=BASE + 3599))
    assert len(info_lines(caplog)) == 1


def test_small_moves_compare_with_the_last_logged_expiry(caplog):
    watcher = make_watcher(caplog)
    watcher.on_cookie_added(make_cookie())
    for minutes in (30, 59):
        watcher.on_cookie_added(make_cookie(expires=BASE + minutes * 60))
    assert len(info_lines(caplog)) == 1
    watcher.on_cookie_added(make_cookie(expires=BASE + 61 * 60))
    assert "renewed" in info_lines(caplog)[-1]


def test_flip_between_session_and_persistent_is_renewed(caplog):
    watcher = make_watcher(caplog)
    watcher.on_cookie_added(make_cookie())
    watcher.on_cookie_added(make_cookie(expires=None))
    assert "expires_from=2027-01-15T08:00Z expires_to=session" in info_lines(caplog)[-1]


def test_unwatched_name_is_ignored(caplog):
    watcher = make_watcher(caplog)
    watcher.on_cookie_added(make_cookie(name="theme"))
    watcher.on_cookie_removed(make_cookie(name="theme"))
    assert [r for r in caplog.records if r.name == LOGGER] == []


def test_name_containing_session_is_watched(caplog):
    watcher = make_watcher(caplog)
    watcher.on_cookie_added(make_cookie(name="activitySessionId"))
    watcher.on_cookie_added(make_cookie(name="consoleSESSIONKeyAbc"))
    assert len(info_lines(caplog)) == 2


def test_registry_cookie_names_and_aliases_are_watched(caplog, monkeypatch):
    cookies = SimpleNamespace(name="authtoken", aliases=("authtoken.0",))
    monkeypatch.setattr(
        session_cookies, "PROVIDER_SPECS", (SimpleNamespace(cookies=cookies),)
    )
    watcher = make_watcher(caplog)
    watcher.on_cookie_added(make_cookie(name="authtoken"))
    watcher.on_cookie_added(make_cookie(name="authtoken.0"))
    watcher.on_cookie_added(make_cookie(name="other"))
    assert len(info_lines(caplog)) == 2


def test_host_only_and_domain_copies_are_separate_keys(caplog):
    watcher = make_watcher(caplog)
    watcher.on_cookie_added(make_cookie(domain=".platform.claude.com"))
    watcher.on_cookie_added(make_cookie(domain="platform.claude.com"))
    lines = info_lines(caplog)
    assert len(lines) == 2
    assert "domain=platform.claude.com " in lines[1]


def test_remove_then_add_with_new_expiry_logs_renewed(caplog):
    watcher = make_watcher(caplog)
    watcher.on_cookie_added(make_cookie())
    watcher.on_cookie_removed(make_cookie())
    assert len(info_lines(caplog)) == 1
    watcher.on_cookie_added(make_cookie(expires=BASE + 86400))
    assert "renewed" in info_lines(caplog)[-1]


def test_cookie_value_never_logged(caplog):
    watcher = make_watcher(caplog)
    watcher.on_cookie_added(make_cookie())
    watcher.on_cookie_added(make_cookie())
    watcher.on_cookie_added(make_cookie(expires=BASE + 86400))
    watcher.on_cookie_removed(make_cookie())
    watcher.on_cookie_added(make_cookie(expires=None))
    assert caplog.records
    assert SECRET not in caplog.text


def test_handler_swallows_errors(caplog):
    watcher = make_watcher(caplog)
    watcher.on_cookie_added(object())
    watcher.on_cookie_removed(object())
    assert info_lines(caplog) == []
