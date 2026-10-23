"""Monthly Claude API credit for Max and Team subscribers.

The credit lives on the Claude Console (platform.claude.com), not on
claude.ai, so this provider has its own sign-in. It reads the Console's
internal billing JSON from inside the billing page, never from Python, so the
session cookie stays in Chromium. The route is undocumented: every unknown
shape is an ERROR, never zero and never the last reading.

See docs/claude-api-credit-plan.md.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from PyQt6.QtCore import QObject

from ..models import SnapshotStatus, UsageMetric, UsageSnapshot
from ._scrape_runner import ScrapeRunner
from .base import Provider

CLAUDE_API_BILLING_URL = "https://platform.claude.com/settings/billing"
SUBSCRIPTION_PROGRAM = "subscription_api_credit"
log = logging.getLogger("aigauge.providers.claude_api")

_READ_ERROR = "Couldn't read Console billing"
_SIGNED_OUT_ERROR = "Not signed in to Claude Console."
_SEVERAL_SUBSCRIPTION_ERROR = (
    "Several Console organizations receive subscription credit. Not supported yet."
)
_NO_BILLING_ROLE_ERROR = (
    "This Console sign-in can't see billing. It needs the Owner, Admin or "
    "Billing role."
)
_SEVERAL_ORGS_ERROR = "Several Console organizations. Not supported yet."
_NO_API_ORG_ERROR = "No Console organization with API access."

# Runs in the billing page. It is synchronous: the first call starts the
# fetches and returns the scraper's retry marker, and every later call returns
# the stored result. The result is assigned once, after every fetch has
# settled, so a half-read payload never leaves the page. Each fetch aborts
# after 4 s, so two stages fit the scraper's 5 reruns and 25 s timeout.
#
# Only structure and amounts leave the page. Org ids, names, tranche ids and
# scope contents stay in it: the scope of each tranche becomes the index of its
# JSON among the distinct scopes of that response. An exception reports only
# its name, because its message can carry a URL with an org id.
EXTRACTOR_JS = r"""
(() => {
  const S = (window.__aigaugeCredit = window.__aigaugeCredit || {});
  const errorType = body => {
    const t = body && body.error && body.error.type;
    return typeof t === 'string' && /^[a-z_]{1,40}$/.test(t) ? t : null;
  };
  const num = v => (typeof v === 'number' ? v : null);
  const word = (v, re) => (typeof v === 'string' && re.test(v) ? v : null);
  const stamp = v => word(v, /^\d{4}-\d\d-\d\dT[\d:.]+(Z|[+-]\d\d:\d\d)?$/);
  const exponent = v => num(v && v.credits && v.credits.exponent);
  const pick = body => {
    if (!body || typeof body !== 'object' || Array.isArray(body)) return null;
    let tranches = null;
    if (Array.isArray(body.promo_tranches)) {
      const scopes = [];
      tranches = body.promo_tranches.map(t => {
        if (!t || typeof t !== 'object') return null;
        const key = JSON.stringify(t.scope === undefined ? null : t.scope);
        let token = scopes.indexOf(key);
        if (token < 0) { token = scopes.length; scopes.push(key); }
        return {
          program_id: word(t.program_id, /^[a-z0-9_]{1,60}$/),
          currency: word(t.currency, /^[A-Za-z]{3}$/),
          granted_amount_minor_units: num(t.granted_amount_minor_units),
          remaining_amount_minor_units: num(t.remaining_amount_minor_units),
          granted_at: stamp(t.granted_at),
          expires_at: stamp(t.expires_at),
          granted_exponent: exponent(t.granted),
          remaining_exponent: exponent(t.remaining),
          scope_token: token
        };
      });
    }
    return {
      amount: num(body.amount),
      amount_without_scoped_credits: num(body.amount_without_scoped_credits),
      currency: word(body.currency, /^[A-Za-z]{3}$/),
      promo_tranches: tranches
    };
  };
  if (!S.started) {
    S.started = true;
    const get = async url => {
      const ctl = new AbortController();
      const t = setTimeout(() => ctl.abort(), 4000);
      try {
        const r = await fetch(url, { headers: { accept: 'application/json' },
                                     signal: ctl.signal });
        let body = null;
        try { body = await r.json(); } catch (e) {}
        return { status: r.status, body };
      } finally { clearTimeout(t); }
    };
    (async () => {
      if (location.hostname !== 'platform.claude.com') {
        return { signed_out: true, reason: 'host' };
      }
      const orgs = await get('/api/organizations');
      if (orgs.status !== 200 || !Array.isArray(orgs.body)) {
        return { orgs_status: orgs.status, orgs_error: errorType(orgs.body) };
      }
      const api = orgs.body.filter(o => o && Array.isArray(o.capabilities) &&
                                        o.capabilities.includes('api'));
      if (api.length > 10) return { orgs_status: 200, too_many_orgs: api.length };
      const credits = await Promise.all(api.map(o =>
        get(`/api/organizations/${o.uuid}/prepaid/credits`)
          .then(c => ({ status: c.status, error: errorType(c.body),
                        credit: c.status === 200 ? pick(c.body) : null }))));
      return { orgs_status: 200, api_orgs: api.length, credits };
    })().then(
      r => { S.result = r; },
      e => { S.result = { error: (e && e.name) || 'Error' }; });
  }
  return S.result ||
    { __retry_after_ms: 2000, __retry_reason: 'credit fetch pending' };
})()
"""


class _BadShape(Exception):
    """The Console JSON is not the shape this provider knows."""


@dataclass(frozen=True)
class _Tranche:
    granted: int
    remaining: int
    granted_at: datetime  # aware, UTC
    expires_at: datetime  # aware, UTC
    currency: str
    scope: int


@dataclass(frozen=True)
class _OrgCredit:
    currency: str
    amount: int
    amount_without_scoped: int
    subscription: tuple[_Tranche, ...]  # every subscription tranche, validated


@dataclass(frozen=True)
class _Group:
    expires_at: datetime
    currency: str
    scope: int
    tranches: tuple[_Tranche, ...]

    @property
    def granted(self) -> int:
        return sum(t.granted for t in self.tranches)

    @property
    def remaining(self) -> int:
        return sum(t.remaining for t in self.tranches)


# -- small pure helpers -------------------------------------------------------

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_WORD = re.compile(r"^[A-Za-z_]{1,40}$")


def _as_int(value: Any) -> int | None:
    """An integer amount. A whole-number float is accepted: QtWebEngine hands
    some JS numbers back as float."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _local(moment: datetime) -> datetime:
    """Aware UTC to the naive local time the other providers use."""
    return moment.astimezone().replace(tzinfo=None)


def _money(minor: int, currency: str) -> str:
    """`$87.60`, or `EUR 87.60` for another currency. Exponent is always 2."""
    prefix = "$" if currency == "USD" else f"{currency} "
    return f"{prefix}{minor // 100}.{minor % 100:02d}"


def _date_text(local: datetime) -> str:
    return f"{_MONTHS[local.month - 1]} {local.day}"


def _date_time_text(local: datetime) -> str:
    # Built by hand: strftime's no-padding day flag differs between Windows
    # and POSIX, and %b follows the locale.
    return f"{_date_text(local)} {local.hour:02d}:{local.minute:02d}"


def _safe_word(value: Any) -> str | None:
    return value if isinstance(value, str) and _WORD.match(value) else None


def _parse_org(entry: Any) -> _OrgCredit:
    """Validate one org's `credit` object. Raises _BadShape."""
    if not isinstance(entry, dict):
        raise _BadShape("credit is not an object")
    currency = entry.get("currency")
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Za-z]{3}", currency):
        raise _BadShape("currency")
    amount = _as_int(entry.get("amount"))
    without = _as_int(entry.get("amount_without_scoped_credits"))
    if amount is None or without is None or amount < 0 or without < 0:
        raise _BadShape("amount")
    raw_tranches = entry.get("promo_tranches")
    if not isinstance(raw_tranches, list):
        raise _BadShape("promo_tranches")

    subscription: list[_Tranche] = []
    for raw in raw_tranches:
        if not isinstance(raw, dict):
            raise _BadShape("tranche is not an object")
        if raw.get("program_id") != SUBSCRIPTION_PROGRAM:
            continue  # other programs count only through `amount`
        granted = _as_int(raw.get("granted_amount_minor_units"))
        remaining = _as_int(raw.get("remaining_amount_minor_units"))
        if granted is None or remaining is None or granted <= 0:
            raise _BadShape("tranche amounts")
        if remaining < 0 or remaining > granted:
            raise _BadShape("tranche remaining out of range")
        granted_at = _parse_utc(raw.get("granted_at"))
        expires_at = _parse_utc(raw.get("expires_at"))
        if granted_at is None or expires_at is None or granted_at >= expires_at:
            raise _BadShape("tranche dates")
        if raw.get("currency") != currency:
            raise _BadShape("tranche currency")
        for key in ("granted_exponent", "remaining_exponent"):
            if raw.get(key) is not None and _as_int(raw.get(key)) != 2:
                raise _BadShape("exponent")
        scope = _as_int(raw.get("scope_token"))
        if scope is None or scope < 0:
            raise _BadShape("scope token")
        subscription.append(
            _Tranche(granted, remaining, granted_at, expires_at, currency, scope)
        )
    return _OrgCredit(currency, amount, without, tuple(subscription))


def _active(org: _OrgCredit, now_utc: datetime) -> list[_Tranche]:
    return [t for t in org.subscription if t.expires_at > now_utc]


def _group(tranches: list[_Tranche]) -> list[_Group]:
    """Group by expiry, currency and scope token. Soonest expiry first."""
    buckets: dict[tuple[datetime, str, int], list[_Tranche]] = {}
    for tranche in tranches:
        key = (tranche.expires_at, tranche.currency, tranche.scope)
        buckets.setdefault(key, []).append(tranche)
    return [
        _Group(key[0], key[1], key[2], tuple(items))
        for key, items in sorted(buckets.items(), key=lambda kv: kv[0][0])
    ]


def _other_amount(org: _OrgCredit, active: list[_Tranche]) -> tuple[bool, int]:
    """(agree, amount) for the "Other" figure.

    Two independent derivations must agree: `amount` minus what remains in the
    active subscription tranches, and `amount_without_scoped_credits`. That
    needs no assumption about what either field means.
    """
    derived = org.amount - sum(t.remaining for t in active)
    return derived == org.amount_without_scoped, derived


# -- snapshot -----------------------------------------------------------------


def _build_snapshot(
    payload: dict[str, Any],
    account_id: str = "claude_api",
    now: datetime | None = None,
) -> UsageSnapshot:
    """`now` is naive local time, like every other provider's timestamps."""
    now = now or datetime.now()
    diag: dict[str, Any] = {}
    snapshot = _snapshot(
        payload if isinstance(payload, dict) else {}, account_id, now, diag
    )
    snapshot.raw = dict(diag)
    log.info("%s credit %s", account_id, _diag_line(diag))
    return snapshot


def _diag_line(diag: dict[str, Any]) -> str:
    """One structural line: no ids, names, amounts or response text."""
    statuses = diag.get("credits_statuses") or []
    parts = [
        f"orgs_status={diag.get('orgs_status', '-')}",
        f"api_orgs={diag.get('api_orgs', '-')}",
        "credits_statuses=" + (",".join(str(s) for s in statuses) or "-"),
        f"subscription_tranches={diag.get('subscription_tranches', '-')}",
        f"groups={diag.get('groups', '-')}",
        f"other={diag.get('other', '-')}",
        f"result={diag.get('result', '-')}",
    ]
    for key in ("orgs_error", "error"):
        if diag.get(key):
            parts.append(f"{key}={diag[key]}")
    return " ".join(parts)


def _fail(
    account_id: str,
    diag: dict[str, Any],
    result: str,
    error: str,
    status: SnapshotStatus = SnapshotStatus.ERROR,
) -> UsageSnapshot:
    diag["result"] = result
    return UsageSnapshot(provider=account_id, status=status, error=error)


def _snapshot(
    payload: dict[str, Any],
    account_id: str,
    now: datetime,
    diag: dict[str, Any],
) -> UsageSnapshot:
    now_utc = now.astimezone(timezone.utc)

    # Provisional until check P2 in docs/claude-api-credit-plan.md.
    if payload.get("signed_out"):
        return _fail(
            account_id, diag, "signed_out", _SIGNED_OUT_ERROR,
            SnapshotStatus.AUTH_REQUIRED,
        )

    error_name = _safe_word(payload.get("error"))
    if "error" in payload:
        diag["error"] = error_name or "unknown"
        return _fail(account_id, diag, "exception", _READ_ERROR)

    orgs_status = _as_int(payload.get("orgs_status"))
    if orgs_status is not None:
        diag["orgs_status"] = orgs_status
    orgs_error = _safe_word(payload.get("orgs_error"))
    if orgs_error:
        diag["orgs_error"] = orgs_error

    # Provisional until check P2 in docs/claude-api-credit-plan.md.
    if orgs_status == 401:
        return _fail(
            account_id, diag, "orgs_401", _SIGNED_OUT_ERROR,
            SnapshotStatus.AUTH_REQUIRED,
        )
    if orgs_status != 200:
        return _fail(account_id, diag, "orgs_status", _READ_ERROR)

    too_many = _as_int(payload.get("too_many_orgs"))
    if too_many is not None:
        diag["api_orgs"] = too_many
        return _fail(account_id, diag, "too_many_orgs", _SEVERAL_ORGS_ERROR)

    api_orgs = _as_int(payload.get("api_orgs"))
    credits = payload.get("credits")
    if (
        api_orgs is None
        or api_orgs < 0
        or not isinstance(credits, list)
        or len(credits) != api_orgs
        or not all(isinstance(c, dict) for c in credits)
    ):
        return _fail(account_id, diag, "bad_shape", _READ_ERROR)
    diag["api_orgs"] = api_orgs

    statuses = [_as_int(c.get("status")) for c in credits]
    diag["credits_statuses"] = [s if s is not None else 0 for s in statuses]
    # Provisional until check P2 in docs/claude-api-credit-plan.md: a 403 is
    # the permission refusal, and any other non-200 fails the whole refresh so
    # a transient failure can never hand the tile to another org.
    if any(s not in (200, 403) for s in statuses):
        return _fail(account_id, diag, "credits_status", _READ_ERROR)

    parsed: list[_OrgCredit | None] = []  # None marks a refused org
    try:
        for status, entry in zip(statuses, credits):
            parsed.append(_parse_org(entry.get("credit")) if status == 200 else None)
    except _BadShape:
        return _fail(account_id, diag, "bad_shape", _READ_ERROR)

    subscription_orgs = [
        (org, active)
        for org in parsed
        if org is not None
        for active in [_active(org, now_utc)]
        if active
    ]
    diag["subscription_tranches"] = sum(len(a) for _, a in subscription_orgs)

    if len(subscription_orgs) > 1:
        return _fail(
            account_id, diag, "several_subscription_orgs",
            _SEVERAL_SUBSCRIPTION_ERROR,
        )
    if len(subscription_orgs) == 1:
        org, active = subscription_orgs[0]
        return _subscription_snapshot(account_id, now, org, active, diag)

    # No org has an active subscription grant.
    if api_orgs == 0:
        return _fail(account_id, diag, "no_api_org", _NO_API_ORG_ERROR)
    if api_orgs > 1:
        return _fail(account_id, diag, "several_orgs", _SEVERAL_ORGS_ERROR)
    org = parsed[0]
    if org is None:
        return _fail(account_id, diag, "no_billing_role", _NO_BILLING_ROLE_ERROR)
    return _purchased_only_snapshot(account_id, now, org, diag)


def _subscription_snapshot(
    account_id: str,
    now: datetime,
    org: _OrgCredit,
    active: list[_Tranche],
    diag: dict[str, Any],
) -> UsageSnapshot:
    groups = _group(active)
    diag["groups"] = len(groups)
    agree, other = _other_amount(org, active)
    diag["other"] = "agree" if agree else "mismatch"
    show_other = agree and other > 0

    latest = groups[-1].expires_at
    current = [g for g in groups if g.expires_at == latest]
    older = [g for g in groups if g.expires_at != latest]
    currency = org.currency
    expires_local = _local(latest)
    expiry_note = (
        f"Unused credit expires {_date_text(expires_local)} "
        "and doesn't pay for Claude Code."
    )

    metrics: list[UsageMetric] = []
    if len(current) == 1:
        group = current[0]
        earliest = min(t.granted_at for t in group.tranches)
        metrics.append(
            UsageMetric(
                label="Credit",
                percent_used=(group.granted - group.remaining) / group.granted * 100,
                resets_at=expires_local,
                note=(
                    f"{_money(group.remaining, currency)} of "
                    f"{_money(group.granted, currency)} left. {expiry_note}"
                ),
                window=latest - earliest,
                guard_eligible=False,
            )
        )
        left = group.remaining
        left_note = expiry_note
    else:
        # Same expiry, different scopes: no trustworthy percentage, so the
        # amount stays visible as text and nothing is guessed about the scopes.
        left = sum(g.remaining for g in current)
        left_note = (
            f"Several credit scopes end together, so there is no percentage. "
            f"{expiry_note}"
        )

    left_label = f"Left {_money(left, currency)}"
    if show_other:
        left_label += f" · Other {_money(other, currency)}"
        left_note += " Other is credit outside the subscription grants."
    metrics.append(UsageMetric(label=left_label, percent_used=None, note=left_note))

    for group in older:
        ends = _date_time_text(_local(group.expires_at))
        metrics.append(
            UsageMetric(
                label=f"Older {_money(group.remaining, currency)}, ends {ends}",
                percent_used=None,
                note="Credit from an earlier grant. Unused credit expires then.",
            )
        )

    diag["result"] = "ok"
    return UsageSnapshot(
        provider=account_id,
        status=SnapshotStatus.OK,
        metrics=metrics,
        fetched_at=now,
    )


def _purchased_only_snapshot(
    account_id: str,
    now: datetime,
    org: _OrgCredit,
    diag: dict[str, Any],
) -> UsageSnapshot:
    diag["groups"] = 0
    agree, balance = _other_amount(org, [])
    diag["other"] = "agree" if agree else "mismatch"
    if not agree:
        # The two fields disagree about the purchased balance, so there is no
        # figure to trust.
        return _fail(account_id, diag, "bad_shape", _READ_ERROR)
    diag["result"] = "purchased_only"
    return UsageSnapshot(
        provider=account_id,
        status=SnapshotStatus.OK,
        metrics=[
            UsageMetric(
                label=f"Balance {_money(balance, org.currency)}",
                percent_used=None,
                note="Purchased credit. No subscription credit is active here.",
            )
        ],
        fetched_at=now,
    )


class ClaudeApiProvider(Provider):
    name = "claude_api"
    display_name = "Claude API"

    def __init__(
        self,
        parent: QObject | None = None,
        account_id: str = "claude_api",
    ):
        self._parent = parent
        self._account_id = account_id
        self._runner: ScrapeRunner | None = None  # held to prevent GC

    def refresh(self, on_done: Callable[[UsageSnapshot], None]) -> None:
        def _build(payload: dict[str, Any]) -> UsageSnapshot:
            return _build_snapshot(payload, account_id=self._account_id)

        self._runner = ScrapeRunner(
            account_id=self._account_id,
            url=CLAUDE_API_BILLING_URL,
            extractor_js=EXTRACTOR_JS,
            build=_build,
            log=log,
            # The extractor only needs the page on the Console host, not
            # hydrated: it fetches JSON itself and asks for a rerun until done.
            wait_ms=3000,
            transport_max_attempts=2,
            build_max_attempts=2,
            parent=self._parent,
        )
        self._runner.run(on_done)
