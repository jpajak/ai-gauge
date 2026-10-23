"""Pure builder tests for the Claude API credit provider.

Fixtures follow the sample response in docs/claude-api-credit-plan.md. The
signed-out and permission responses are the ones measured in its "P2 result".
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from aigauge.models import SnapshotStatus
from aigauge.providers import claude_api
from aigauge.providers.claude_api import (
    ClaudeApiProvider,
    _build_snapshot,
    _date_time_text,
    _local,
    _money,
)

# Naive local time, like every other provider's timestamps.
NOW = datetime(2026, 10, 23, 12, 0)

GRANTED = "2026-10-08T13:47:24.772000Z"
EXPIRES = "2026-11-05T12:00:00Z"
OLDER_GRANTED = "2026-09-08T13:47:24Z"
OLDER_EXPIRES = "2026-10-30T12:00:00Z"
SUB = "subscription_api_credit"

READ_ERROR = "Couldn't read Console billing"
SIGNED_OUT = "Not signed in to Claude Console."
SEVERAL_SUB = "Several Console organizations receive subscription credit. Not supported yet."
NO_ROLE = "This Console sign-in can't see billing. It needs the Owner, Admin or Billing role."
SEVERAL_ORGS = "Several Console organizations. Not supported yet."
NO_API = "No Console organization with API access."


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def tranche(
    granted=10000,
    remaining=10000,
    expires=EXPIRES,
    granted_at=GRANTED,
    scope=0,
    program=SUB,
    currency="USD",
    granted_exponent=2,
    remaining_exponent=2,
):
    return {
        "program_id": program,
        "currency": currency,
        "granted_amount_minor_units": granted,
        "remaining_amount_minor_units": remaining,
        "granted_at": granted_at,
        "expires_at": expires,
        "granted_exponent": granted_exponent,
        "remaining_exponent": remaining_exponent,
        "scope_token": scope,
    }


def credit(tranches, purchased=0, currency="USD", amount=None, without=None):
    """`amount` includes the promo tranches, as the Console reports it."""
    if amount is None:
        amount = purchased + sum(
            t["remaining_amount_minor_units"] or 0
            for t in tranches
            if isinstance(t, dict) and isinstance(t["remaining_amount_minor_units"], int)
        )
    return {
        "amount": amount,
        "amount_without_scoped_credits": purchased if without is None else without,
        "currency": currency,
        "promo_tranches": tranches,
    }


def org(tranches=None, status=200, error_code=None, **kwargs):
    if status != 200:
        error = "permission_error" if status == 403 else None
        return {"status": status, "error": error, "error_code": error_code, "credit": None}
    return {"status": 200, "error": None, "credit": credit(tranches or [], **kwargs)}


def payload(*orgs, orgs_status=200):
    return {"orgs_status": orgs_status, "api_orgs": len(orgs), "credits": list(orgs)}


def build(data, now=NOW):
    return _build_snapshot(data, "claude_api", now)


def bar(snapshot):
    return next((m for m in snapshot.metrics if m.percent_used is not None), None)


def lines(snapshot):
    return [m.label for m in snapshot.metrics if m.percent_used is None]


def assert_error(snapshot, message, status=SnapshotStatus.ERROR):
    assert snapshot.status == status
    assert snapshot.error == message
    assert snapshot.metrics == []


# -- credit shapes --------------------------------------------------------------


def test_unused_credit_is_zero_percent():
    snap = build(payload(org([tranche()])))
    assert snap.status == SnapshotStatus.OK
    assert snap.provider == "claude_api"
    credit_bar = bar(snap)
    assert credit_bar.label == "Credit"
    assert credit_bar.percent_used == 0
    assert credit_bar.resets_at == _local(utc(EXPIRES))
    assert credit_bar.resets_at.tzinfo is None
    assert credit_bar.window == utc(EXPIRES) - utc(GRANTED)
    assert lines(snap) == ["Left $100.00"]
    assert snap.fetched_at == NOW


def test_bar_note_states_amounts_expiry_and_claude_code():
    snap = build(payload(org([tranche(remaining=8760)])))
    note = bar(snap).note
    assert note == (
        "$87.60 of $100.00 left. Unused credit expires Nov 5 and "
        "doesn't pay for Claude Code."
    )


def test_partly_used_credit():
    snap = build(payload(org([tranche(remaining=8760)])))
    assert bar(snap).percent_used == pytest.approx(12.4)
    assert lines(snap) == ["Left $87.60"]


def test_exhausted_credit_with_purchased_credit_left():
    snap = build(payload(org([tranche(remaining=0)], purchased=1234)))
    assert bar(snap).percent_used == 100
    assert lines(snap) == ["Left $0.00 · Other $12.34"]


def test_prorated_upgrade_grant_combines_with_the_regular_one():
    upgrade_at = "2026-10-15T09:00:00Z"
    snap = build(
        payload(
            org(
                [
                    tranche(granted=10000, remaining=6000),
                    tranche(granted=5000, remaining=5000, granted_at=upgrade_at),
                ]
            )
        )
    )
    credit_bar = bar(snap)
    # 15000 granted, 11000 left.
    assert credit_bar.percent_used == pytest.approx(4000 / 15000 * 100)
    assert credit_bar.window == utc(EXPIRES) - utc(GRANTED)  # earliest grant
    assert lines(snap) == ["Left $110.00"]
    assert "$110.00 of $150.00 left" in credit_bar.note


def test_older_group_overlapping_the_current_one_gets_its_own_line():
    snap = build(
        payload(
            org(
                [
                    tranche(granted=10000, remaining=10000),
                    tranche(
                        granted=10000,
                        remaining=410,
                        granted_at=OLDER_GRANTED,
                        expires=OLDER_EXPIRES,
                    ),
                ]
            )
        )
    )
    ends = _date_time_text(_local(utc(OLDER_EXPIRES)))
    assert lines(snap) == ["Left $100.00", f"Older $4.10, ends {ends}"]
    # The bar is the current (latest expiry) group only.
    assert bar(snap).percent_used == 0
    assert bar(snap).resets_at == _local(utc(EXPIRES))


def test_older_lines_list_soonest_expiry_first():
    snap = build(
        payload(
            org(
                [
                    tranche(),
                    tranche(remaining=300, expires="2026-10-31T12:00:00Z", granted_at=OLDER_GRANTED),
                    tranche(remaining=200, expires=OLDER_EXPIRES, granted_at=OLDER_GRANTED),
                ]
            )
        )
    )
    older = [label for label in lines(snap) if label.startswith("Older")]
    assert [label.split(",")[0] for label in older] == ["Older $2.00", "Older $3.00"]


def test_same_expiry_with_different_scope_tokens_has_no_bar():
    snap = build(
        payload(
            org(
                [
                    tranche(granted=10000, remaining=7000, scope=0),
                    tranche(granted=5000, remaining=5000, scope=1),
                ]
            )
        )
    )
    assert snap.status == SnapshotStatus.OK
    assert bar(snap) is None
    assert lines(snap) == ["Left $120.00"]
    assert "no percentage" in snap.metrics[0].note


def test_purchased_credit_only_is_a_text_line_without_a_bar():
    snap = build(payload(org([], purchased=1234)))
    assert snap.status == SnapshotStatus.OK
    assert bar(snap) is None
    assert lines(snap) == ["Balance $12.34"]


def test_purchased_credit_only_with_zero_balance_still_shows():
    snap = build(payload(org([], purchased=0)))
    assert lines(snap) == ["Balance $0.00"]


def test_purchased_only_with_disagreeing_amounts_is_an_error():
    snap = build(payload(org([], amount=1234, without=0)))
    assert_error(snap, READ_ERROR)
    assert snap.raw["other"] == "mismatch"


def test_expired_subscription_tranche_is_not_active():
    expired = tranche(granted_at="2026-08-01T00:00:00Z", expires="2026-10-01T00:00:00Z", remaining=0)
    snap = build(payload(org([expired], purchased=500)))
    assert lines(snap) == ["Balance $5.00"]
    assert bar(snap) is None


def test_tranche_expiring_exactly_now_is_not_active():
    exact = tranche(remaining=0, expires=NOW.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    exact["granted_at"] = "2026-10-01T00:00:00Z"
    snap = build(payload(org([exact], purchased=500)))
    assert lines(snap) == ["Balance $5.00"]


def test_other_programs_are_ignored_except_through_amount():
    other_program = tranche(program="welcome_bonus", granted=-5, remaining=100, currency="XXX")
    snap = build(
        payload(
            org(
                [tranche(remaining=8760), other_program],
                amount=8760 + 100,
                without=100,
            )
        )
    )
    assert snap.status == SnapshotStatus.OK
    assert lines(snap) == ["Left $87.60 · Other $1.00"]


# -- "Other" cross-check --------------------------------------------------------


def test_other_agreeing_is_shown():
    snap = build(payload(org([tranche(remaining=8760)], purchased=1234)))
    assert lines(snap) == ["Left $87.60 · Other $12.34"]
    assert snap.raw["other"] == "agree"
    assert "Other is credit outside the subscription grants." in snap.metrics[-1].note


def test_other_agreeing_at_zero_is_omitted():
    snap = build(payload(org([tranche(remaining=8760)])))
    assert lines(snap) == ["Left $87.60"]
    assert snap.raw["other"] == "agree"


def test_other_disagreeing_is_omitted_and_flagged():
    # amount minus remaining says 12.34, amount_without_scoped_credits says 20.00.
    snap = build(payload(org([tranche(remaining=8760)], amount=8760 + 1234, without=2000)))
    assert snap.status == SnapshotStatus.OK
    assert lines(snap) == ["Left $87.60"]
    assert snap.raw["other"] == "mismatch"


def test_other_agrees_within_one_unit_per_active_tranche():
    # One tranche: derived is 1233 against 1234, one unit apart.
    snap = build(payload(org([tranche(remaining=8760)], amount=8760 + 1233, without=1234)))
    assert lines(snap) == ["Left $87.60 · Other $12.34"]  # the reported figure is shown
    assert snap.raw["other"] == "agree"
    # Two tranches allow two units.
    two = [
        tranche(granted=5000, remaining=4000),
        tranche(granted=5000, remaining=4760),
    ]
    snap = build(payload(org(two, amount=8760 + 1236, without=1234)))
    assert lines(snap) == ["Left $87.60 · Other $12.34"]
    assert snap.raw["other"] == "agree"
    snap = build(payload(org(two, amount=8760 + 1231, without=1234)))
    assert snap.raw["other"] == "mismatch"
    assert lines(snap) == ["Left $87.60"]


def test_other_disagreeing_by_more_than_the_tolerance_is_omitted():
    snap = build(payload(org([tranche(remaining=8760)], amount=8760 + 1236, without=1234)))
    assert snap.raw["other"] == "mismatch"
    assert lines(snap) == ["Left $87.60"]


def test_other_tolerance_counts_only_active_tranches():
    expired = tranche(granted_at="2026-08-01T00:00:00Z", expires="2026-10-01T00:00:00Z", remaining=0)
    snap = build(payload(org([tranche(remaining=8760), expired], amount=8760 + 1236, without=1234)))
    assert snap.raw["other"] == "mismatch"


def test_the_measured_one_unit_gap_with_nothing_else_agrees_and_shows_no_other():
    snap = build(payload(org([tranche(remaining=4183)], amount=4184, without=0)))
    assert snap.status == SnapshotStatus.OK
    assert lines(snap) == ["Left $41.83"]
    assert snap.raw["other"] == "agree"


@pytest.mark.parametrize("missing", ["null", "absent"])
def test_other_null_on_the_subscription_path_is_omitted_and_not_an_error(missing, caplog):
    caplog.set_level(logging.INFO, logger="aigauge.providers.claude_api")
    entry = org([tranche(remaining=8760)])
    if missing == "null":
        entry["credit"]["amount_without_scoped_credits"] = None
    else:
        del entry["credit"]["amount_without_scoped_credits"]
    snap = build(payload(entry))
    assert snap.status == SnapshotStatus.OK
    assert lines(snap) == ["Left $87.60"]
    assert snap.raw["other"] == "none"
    assert "other=none" in caplog.records[-1].getMessage()


def test_purchased_only_with_a_null_other_figure_is_an_error():
    entry = org([], purchased=1234)
    entry["credit"]["amount_without_scoped_credits"] = None
    snap = build(payload(entry))
    assert_error(snap, READ_ERROR)
    assert snap.raw["other"] == "none"


def test_purchased_only_keeps_its_exact_agreement_rule():
    # One minor unit apart is a mismatch here: there is no tranche to round.
    assert_error(build(payload(org([], amount=1235, without=1234))), READ_ERROR)


def test_a_spent_older_group_has_no_line():
    snap = build(
        payload(
            org(
                [
                    tranche(),
                    tranche(
                        granted=10000,
                        remaining=0,
                        granted_at=OLDER_GRANTED,
                        expires=OLDER_EXPIRES,
                    ),
                ]
            )
        )
    )
    assert snap.status == SnapshotStatus.OK
    assert lines(snap) == ["Left $100.00"]
    assert bar(snap).percent_used == 0


# -- non-USD and formatting -----------------------------------------------------


def test_non_usd_shows_the_currency_code():
    snap = build(
        payload(
            org(
                [tranche(currency="EUR", remaining=8760)],
                currency="EUR",
                purchased=1234,
            )
        )
    )
    assert lines(snap) == ["Left EUR 87.60 · Other EUR 12.34"]
    assert bar(snap).note.startswith("EUR 87.60 of EUR 100.00 left.")


def test_non_usd_purchased_only():
    snap = build(payload(org([], currency="GBP", purchased=500)))
    assert lines(snap) == ["Balance GBP 5.00"]


def test_money_uses_integer_arithmetic():
    assert _money(0, "USD") == "$0.00"
    assert _money(5, "USD") == "$0.05"
    assert _money(10000, "USD") == "$100.00"
    assert _money(123456789, "USD") == "$1234567.89"
    assert _money(1, "EUR") == "EUR 0.01"


def test_date_time_text_is_locale_free_and_zero_padded():
    assert _date_time_text(datetime(2026, 11, 4, 20, 0)) == "Nov 4 20:00"
    assert _date_time_text(datetime(2026, 12, 14, 9, 5)) == "Dec 14 09:05"


def test_whole_number_floats_are_accepted():
    t = tranche(granted=10000.0, remaining=8760.0)
    snap = build(payload(org([t], amount=8760.0, without=0.0)))
    assert snap.status == SnapshotStatus.OK
    assert lines(snap) == ["Left $87.60"]


# -- org selection rules --------------------------------------------------------


def test_rule1_exactly_one_org_with_a_subscription_grant_is_used():
    snap = build(
        payload(
            org([], purchased=500),
            org([tranche(remaining=8760)]),
        )
    )
    assert snap.status == SnapshotStatus.OK
    assert bar(snap).percent_used == pytest.approx(12.4)
    assert lines(snap) == ["Left $87.60"]


def test_rule2_two_orgs_with_subscription_grants_is_an_error():
    snap = build(payload(org([tranche()]), org([tranche()])))
    assert_error(snap, SEVERAL_SUB)


def test_rule3_one_api_org_with_no_subscription_grant_shows_purchased_credit():
    snap = build(payload(org([], purchased=1234)))
    assert snap.status == SnapshotStatus.OK
    assert lines(snap) == ["Balance $12.34"]


def test_rule4_one_api_org_refused_is_a_permission_error():
    snap = build(payload(org(status=403)))
    assert_error(snap, NO_ROLE)


def test_rule5_several_api_orgs_without_a_subscription_grant_is_an_error():
    assert_error(build(payload(org([], purchased=1), org([], purchased=2))), SEVERAL_ORGS)
    assert_error(build(payload(org(status=403), org([], purchased=2))), SEVERAL_ORGS)


def test_rule6_no_api_org_is_an_error():
    assert_error(build(payload()), NO_API)


def test_more_than_ten_api_orgs_is_an_error():
    snap = build({"orgs_status": 200, "too_many_orgs": 11})
    assert_error(snap, SEVERAL_ORGS)


# -- signed out, refusal, failures ----------------------------------------------


def test_signed_out_from_the_extractor_needs_sign_in():
    snap = build({"signed_out": True, "reason": "host"})
    assert snap.status == SnapshotStatus.AUTH_REQUIRED
    assert snap.metrics == []
    assert snap.error


def test_organizations_401_needs_sign_in():
    snap = build({"orgs_status": 401, "orgs_error": "authentication_error"})
    assert snap.status == SnapshotStatus.AUTH_REQUIRED
    assert snap.raw["orgs_status"] == 401


@pytest.mark.parametrize("status", [403, 404, 429, 500, 502, 200])
def test_organizations_other_non_200_is_an_error_not_a_sign_out(status):
    # 200 here means the body was not a list; the extractor sends no `credits`.
    assert_error(build({"orgs_status": status, "orgs_error": None}), READ_ERROR)


def test_credits_status_other_than_200_or_403_fails_the_whole_refresh():
    for status in (404, 429, 500):
        snap = build(payload(org([tranche()]), org(status=status)))
        assert_error(snap, READ_ERROR)


def test_credits_401_needs_sign_in_for_the_whole_refresh():
    snap = build(payload(org([tranche()]), org(status=401)))
    assert_error(snap, SIGNED_OUT, SnapshotStatus.AUTH_REQUIRED)
    assert snap.raw["result"] == "credits_signed_out"


def test_credits_403_with_account_session_invalid_needs_sign_in():
    snap = build(payload(org(status=403, error_code="account_session_invalid")))
    assert_error(snap, SIGNED_OUT, SnapshotStatus.AUTH_REQUIRED)
    # One signed-out answer outranks a good org and a failed fetch.
    snap = build(
        payload(
            org([tranche()]),
            org(status=500),
            org(status=403, error_code="account_session_invalid"),
        )
    )
    assert snap.status == SnapshotStatus.AUTH_REQUIRED


def test_credits_403_with_another_code_is_the_permission_refusal():
    snap = build(payload(org(status=403, error_code="permission_denied")))
    assert_error(snap, NO_ROLE)


# -- measured signed-out and refusal responses ----------------------------------
# Shapes as the extractor reports them for the bodies measured in "P2 result":
# the signed-out and refusal answers share status 403 and error type, and only
# error.details.error_code tells them apart.

MEASURED_SIGNED_OUT = {
    "orgs_status": 403,
    "orgs_error": "permission_error",
    "orgs_error_code": "account_session_invalid",
}
MEASURED_REFUSAL = {"orgs_status": 403, "orgs_error": "permission_error", "orgs_error_code": None}


def test_measured_signed_out_body_on_the_org_list_needs_sign_in():
    snap = build(MEASURED_SIGNED_OUT)
    assert_error(snap, SIGNED_OUT, SnapshotStatus.AUTH_REQUIRED)
    assert snap.raw["orgs_error_code"] == "account_session_invalid"


def test_measured_refusal_body_on_the_org_list_is_an_error():
    assert_error(build(MEASURED_REFUSAL), READ_ERROR)


def test_measured_refusal_body_on_a_sole_api_orgs_credits_is_the_rule_4_error():
    entry = {
        "status": 403,
        "error": "permission_error",
        "error_code": None,
        "credit": None,
    }
    assert_error(build(payload(entry)), NO_ROLE)


def test_a_403_html_page_on_the_org_list_is_an_error_not_a_sign_out():
    # The extractor reports no error type or code when the body is not JSON.
    snap = build({"orgs_status": 403, "orgs_error": None, "orgs_error_code": None})
    assert_error(snap, READ_ERROR)
    assert snap.raw["result"] == "orgs_status"


def test_org_list_401_needs_sign_in_whatever_the_code():
    snap = build({"orgs_status": 401, "orgs_error": None, "orgs_error_code": None})
    assert snap.status == SnapshotStatus.AUTH_REQUIRED


def test_org_list_403_with_a_different_code_is_an_error():
    snap = build({**MEASURED_SIGNED_OUT, "orgs_error_code": "something_else"})
    assert_error(snap, READ_ERROR)


def test_a_failed_credits_fetch_for_the_linked_org_never_hands_over_to_another():
    # Org A (linked) fails, org B has only purchased credit: no snapshot of B.
    snap = build(payload(org(status=500), org([], purchased=500)))
    assert_error(snap, READ_ERROR)


def test_timeout_or_exception_from_the_page_is_an_error():
    for name in ("AbortError", "TypeError", "Error"):
        assert_error(build({"error": name}), READ_ERROR)


def test_unusable_payloads_are_errors():
    for data in ({}, {"orgs_status": None}, {"orgs_status": 200}, {"retry": True}):
        assert_error(build(data), READ_ERROR)
    assert_error(_build_snapshot(None, "claude_api", NOW), READ_ERROR)


# -- unknown shapes -------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        {"granted": "10000"},
        {"granted": 10000.5},
        {"granted": True},
        {"granted": None},
        {"granted": 0, "remaining": 0},
        {"granted": -100, "remaining": 0},
        {"remaining": "7000"},
        {"remaining": None},
        {"remaining": -1},
        {"remaining": 10001},  # remaining > granted
        {"expires": "not a date"},
        {"expires": None},
        {"granted_at": EXPIRES},  # granted_at == expires_at
        {"granted_at": "2026-12-01T00:00:00Z"},  # granted after expiry
        {"currency": "EUR"},  # tranche currency differs from the org's USD
        {"currency": None},
        {"granted_exponent": 3},
        {"remaining_exponent": 0},
        {"scope": None},
        {"scope": -1},
        {"scope": "0"},
    ],
)
def test_malformed_subscription_tranche_is_an_error(bad):
    kwargs = {
        key: value
        for key, value in bad.items()
        if key not in ("scope",)
    }
    t = tranche(**kwargs)
    if "scope" in bad:
        t["scope_token"] = bad["scope"]
    snap = build(payload(org([t], amount=10000, without=0)))
    assert_error(snap, READ_ERROR)
    assert snap.raw["result"] == "bad_shape"


def test_remaining_greater_than_granted_never_draws_a_bar():
    snap = build(payload(org([tranche(granted=10000, remaining=12000)], amount=12000, without=0)))
    assert_error(snap, READ_ERROR)


def test_mixed_currencies_in_one_org_is_an_error():
    snap = build(
        payload(
            org(
                [tranche(currency="USD"), tranche(currency="EUR", remaining=500)],
                amount=10500,
                without=0,
            )
        )
    )
    assert_error(snap, READ_ERROR)


def test_malformed_tranche_in_a_non_selected_org_still_fails():
    bad = tranche(remaining=-5)
    snap = build(payload(org([tranche()]), org([bad], amount=0, without=0)))
    assert_error(snap, READ_ERROR)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.update(amount="100"),
        lambda c: c.update(amount=None),
        lambda c: c.update(amount=-1),
        lambda c: c.update(amount_without_scoped_credits="0"),
        lambda c: c.update(amount_without_scoped_credits=-1),
        lambda c: c.update(amount_without_scoped_credits=True),
        lambda c: c.update(currency="usd$"),
        lambda c: c.update(currency=None),
        lambda c: c.update(promo_tranches=None),
        lambda c: c.update(promo_tranches=[None]),
        lambda c: c.update(promo_tranches="none"),
    ],
)
def test_malformed_top_level_credit_is_an_error(mutate):
    entry = org([tranche()])
    mutate(entry["credit"])
    assert_error(build(payload(entry)), READ_ERROR)


def test_a_200_without_a_credit_object_is_an_error():
    entry = {"status": 200, "error": None, "credit": None}
    assert_error(build(payload(entry)), READ_ERROR)


def test_credits_list_must_match_the_org_count():
    data = payload(org([tranche()]))
    data["api_orgs"] = 2
    assert_error(build(data), READ_ERROR)
    data = payload(org([tranche()]))
    data["credits"] = {"not": "a list"}
    assert_error(build(data), READ_ERROR)


# -- raw and the diagnostics line -----------------------------------------------


def test_diagnostics_line_is_structural_only(caplog):
    caplog.set_level(logging.INFO, logger="aigauge.providers.claude_api")
    build(payload(org([tranche(remaining=8760)], purchased=1234)))
    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert messages == [
        "claude_api credit orgs_status=200 api_orgs=1 credits_statuses=200 "
        "subscription_tranches=1 groups=1 other=agree result=ok"
    ]
    assert "87" not in messages[0] and "12.34" not in messages[0]


def test_diagnostics_line_for_failures(caplog):
    caplog.set_level(logging.INFO, logger="aigauge.providers.claude_api")
    build({"signed_out": True, "reason": "host"})
    build(
        {
            "orgs_status": 403,
            "orgs_error": "permission_error",
            "orgs_error_code": "account_session_invalid",
        }
    )
    build(payload(org(status=403)))
    build({"error": "AbortError"})
    messages = [r.getMessage() for r in caplog.records]
    assert messages[0].endswith("result=signed_out")
    assert "orgs_status=403" in messages[1] and "orgs_error=permission_error" in messages[1]
    assert messages[1].endswith("orgs_error_code=account_session_invalid")
    assert "credits_statuses=403" in messages[2] and messages[2].endswith("result=no_billing_role")
    assert messages[3].endswith("result=exception error=AbortError")


def test_diagnostics_line_names_the_account(caplog):
    caplog.set_level(logging.INFO, logger="aigauge.providers.claude_api")
    _build_snapshot(payload(org([tranche()])), "claude_api-2", NOW)
    assert caplog.records[0].getMessage().startswith("claude_api-2 credit ")


def test_raw_holds_only_structural_counts():
    snap = build(payload(org([tranche(remaining=8760)], purchased=1234), org(status=403)))
    assert snap.raw == {
        "orgs_status": 200,
        "api_orgs": 2,
        "credits_statuses": [200, 403],
        "subscription_tranches": 1,
        "groups": 1,
        "other": "agree",
        "result": "ok",
    }


def test_hostile_error_strings_do_not_reach_the_log_or_raw(caplog):
    caplog.set_level(logging.INFO, logger="aigauge.providers.claude_api")
    hostile = "https://platform.claude.com/api/organizations/1234-secret/prepaid/credits"
    snap = build({"error": hostile})
    snap2 = build({"orgs_status": 403, "orgs_error": hostile, "orgs_error_code": hostile})
    for text in [str(snap.raw), str(snap2.raw)] + [r.getMessage() for r in caplog.records]:
        assert "secret" not in text
    assert snap.raw["error"] == "unknown"


def test_default_now_is_used_when_omitted():
    snap = _build_snapshot(payload(org([], purchased=100)))
    assert abs(snap.fetched_at - datetime.now()) < timedelta(seconds=5)


# -- provider -------------------------------------------------------------------


def test_provider_drives_a_scrape_runner_on_the_billing_page(monkeypatch):
    captured = {}

    class FakeRunner:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self, on_done):
            captured["on_done"] = on_done

    monkeypatch.setattr(claude_api, "ScrapeRunner", FakeRunner)
    provider = ClaudeApiProvider(account_id="claude_api-2")
    done = []
    provider.refresh(done.append)

    assert provider.name == "claude_api"
    assert provider.display_name == "Claude API"
    assert captured["account_id"] == "claude_api-2"
    assert captured["url"] == "https://platform.claude.com/settings/billing"
    assert captured["extractor_js"] is claude_api.EXTRACTOR_JS
    snap = captured["build"]({"signed_out": True})
    assert snap.provider == "claude_api-2"
    assert snap.status == SnapshotStatus.AUTH_REQUIRED
    assert ClaudeApiProvider()._account_id == "claude_api"
