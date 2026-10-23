"""Run the Claude API credit extractor under Node.

The extractor is synchronous but starts async fetches, so the harness stubs
`fetch` and `location`, runs the script, lets promises and timers settle, and
runs it again in the same global context, as the scraper does on each rerun.
Skipped when Node is unavailable; GitHub's runners all ship it.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from aigauge.providers.claude_api import EXTRACTOR_JS

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="Node.js not available"
)

# argv: config.json extractor.js. The config holds the hostname, the number of
# runs, and `routes`: url -> {status, body} | {hang: true} | {throw, message}.
# Timers are shortened so the real 4 s abort fires in a few milliseconds.
HARNESS = """
const fs = require('fs');
const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const src = fs.readFileSync(process.argv[3], 'utf8');
const realSetTimeout = setTimeout;
global.setTimeout = (fn, ms) => realSetTimeout(fn, Math.min(ms, 20));
global.window = global;
global.location = { hostname: cfg.hostname };
const calls = [];
global.fetch = (url, init) => {
  calls.push(url);
  const route = cfg.routes[url];
  if (!route) return Promise.reject(new Error('unrouted ' + url));
  if (route.hang) {
    return new Promise((resolve, reject) => {
      init.signal.addEventListener('abort', () => {
        const e = new Error('aborted ' + url);
        e.name = 'AbortError';
        reject(e);
      });
    });
  }
  if (route.throw) {
    const e = new Error(route.message || '');
    e.name = route.throw;
    return Promise.reject(e);
  }
  return Promise.resolve({
    status: route.status,
    json: () => route.invalid
      ? Promise.reject(new SyntaxError('Unexpected token <'))
      : Promise.resolve(JSON.parse(JSON.stringify(route.body))),
  });
};
const settle = () => new Promise(r => realSetTimeout(r, 150));
(async () => {
  const runs = [];
  for (let i = 0; i < cfg.runs; i++) {
    runs.push((0, eval)(src));
    await settle();
  }
  process.stdout.write(JSON.stringify({ runs, calls }));
})();
"""

ORG_ID = "11111111-aaaa-bbbb-cccc-dddddddddddd"
OTHER_ORG_ID = "22222222-aaaa-bbbb-cccc-eeeeeeeeeeee"
CHAT_ORG_ID = "33333333-aaaa-bbbb-cccc-ffffffffffff"
ORG_NAME = "Secret Org Name"
TRANCHE_ID = "tranche-id-not-for-export"
TRANCHE_NAME = "API credit (Max 5x)"
SCOPE_A = {"workspaces": ["ws-secret-scope-a"], "models": "all"}
SCOPE_B = {"workspaces": ["ws-secret-scope-b"]}

RETRY = {"__retry_after_ms": 2000, "__retry_reason": "credit fetch pending"}


def promo(scope=None, remaining=10000, **overrides):
    t = {
        "name": TRANCHE_NAME,
        "program_id": "subscription_api_credit",
        "currency": "USD",
        "granted_amount_minor_units": 10000,
        "remaining_amount_minor_units": remaining,
        "granted_at": "2026-10-08T13:47:24.772000Z",
        "expires_at": "2026-11-05T00:00:00Z",
        "id": TRANCHE_ID,
        "scope": SCOPE_A if scope is None else scope,
        "granted": {"credits": {"amount_minor": 10000, "exponent": 2}},
        "remaining": {"credits": {"amount_minor": remaining, "exponent": 2}},
    }
    t.update(overrides)
    return t


def credits_body(*tranches, amount=10000, without=0):
    return {
        "amount": amount,
        "currency": "USD",
        "balance_credits": 100,
        "amount_without_scoped_credits": without,
        "tranches": [],
        "promo_tranches": list(tranches),
        "next_expires_at": "2026-11-05T00:00:00Z",
        "auto_reload_settings": None,
    }


def orgs_body():
    return [
        {"uuid": CHAT_ORG_ID, "name": "Chat " + ORG_NAME, "capabilities": ["claude_max"]},
        {"uuid": ORG_ID, "name": ORG_NAME, "capabilities": ["api"], "extra": 1},
    ]


def routes(credits=None, orgs=None, orgs_status=200):
    out = {
        "/api/organizations": {
            "status": orgs_status,
            "body": orgs_body() if orgs is None else orgs,
        },
    }
    out[f"/api/organizations/{ORG_ID}/prepaid/credits"] = credits or {
        "status": 200,
        "body": credits_body(promo()),
    }
    return out


def run(tmp_path, route_map, hostname="platform.claude.com", runs=2):
    cfg = tmp_path / "config.json"
    harness = tmp_path / "harness.js"
    script = tmp_path / "extractor.js"
    cfg.write_text(
        json.dumps({"hostname": hostname, "routes": route_map, "runs": runs}),
        encoding="utf-8",
    )
    harness.write_text(HARNESS, encoding="utf-8")
    script.write_text(EXTRACTOR_JS, encoding="utf-8")
    out = subprocess.run(
        ["node", str(harness), str(cfg), str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    return json.loads(out.stdout)


def result_of(tmp_path, route_map, **kwargs):
    """The payload a later run returns, after everything settled."""
    return run(tmp_path, route_map, **kwargs)["runs"][-1]


# -- the retry protocol ---------------------------------------------------------


def test_first_run_returns_the_retry_marker_and_a_later_run_the_payload(tmp_path):
    out = run(tmp_path, routes(), runs=3)
    assert out["runs"][0] == RETRY
    payload = out["runs"][1]
    assert payload["orgs_status"] == 200
    assert payload["api_orgs"] == 1
    assert payload["credits"][0]["status"] == 200
    # Settled once: a further run returns the same object without refetching.
    assert out["runs"][2] == payload
    assert out["calls"] == [
        "/api/organizations",
        f"/api/organizations/{ORG_ID}/prepaid/credits",
    ]


def test_only_organizations_with_api_access_are_fetched(tmp_path):
    out = run(tmp_path, routes())
    assert f"/api/organizations/{CHAT_ORG_ID}/prepaid/credits" not in out["calls"]


def test_payload_carries_the_amounts_and_structure_the_builder_needs(tmp_path):
    body = credits_body(promo(remaining=8760), amount=8760 + 1234, without=1234)
    payload = result_of(tmp_path, routes({"status": 200, "body": body}))
    entry = payload["credits"][0]
    assert entry["error"] is None
    assert entry["error_code"] is None
    assert entry["credit"] == {
        "amount": 9994,
        "amount_without_scoped_credits": 1234,
        "currency": "USD",
        "promo_tranches": [
            {
                "program_id": "subscription_api_credit",
                "currency": "USD",
                "granted_amount_minor_units": 10000,
                "remaining_amount_minor_units": 8760,
                "granted_at": "2026-10-08T13:47:24.772000Z",
                "expires_at": "2026-11-05T00:00:00Z",
                "granted_exponent": 2,
                "remaining_exponent": 2,
                "scope_token": 0,
            }
        ],
    }


# -- off host, failures ---------------------------------------------------------


def test_off_host_is_signed_out_and_makes_no_fetch(tmp_path):
    out = run(tmp_path, routes(), hostname="platform.claude.com.evil.example", runs=2)
    assert out["calls"] == []
    assert out["runs"][0] == RETRY
    assert out["runs"][1] == {"signed_out": True, "reason": "host"}


def test_login_host_redirect_is_signed_out(tmp_path):
    out = run(tmp_path, routes(), hostname="claude.ai")
    assert out["calls"] == []
    assert out["runs"][-1]["signed_out"] is True


def test_organizations_401_reports_status_and_error_type_only(tmp_path):
    body = {"error": {"type": "authentication_error", "message": f"no session for {ORG_ID}"}}
    out = run(tmp_path, routes(orgs=body, orgs_status=401))
    assert out["runs"][-1] == {
        "orgs_status": 401,
        "orgs_error": "authentication_error",
        "orgs_error_code": None,
    }
    assert out["calls"] == ["/api/organizations"]
    assert ORG_ID not in json.dumps(out["runs"])


@pytest.mark.parametrize(
    "error_type",
    ["Invalid Auth!", "UPPER", "x" * 41, "", 7, None, {"nested": True}],
)
def test_error_type_must_be_a_short_lowercase_word(tmp_path, error_type):
    body = {"error": {"type": error_type}}
    payload = result_of(tmp_path, routes(orgs=body, orgs_status=403))
    assert payload == {"orgs_status": 403, "orgs_error": None, "orgs_error_code": None}


def test_organizations_html_body_is_status_only(tmp_path):
    route_map = routes()
    route_map["/api/organizations"] = {"status": 403, "invalid": True}
    assert result_of(tmp_path, route_map) == {
        "orgs_status": 403,
        "orgs_error": None,
        "orgs_error_code": None,
    }


def test_organizations_200_with_a_non_list_body_is_not_read_as_orgs(tmp_path):
    payload = result_of(tmp_path, routes(orgs={"organizations": []}))
    assert payload == {"orgs_status": 200, "orgs_error": None, "orgs_error_code": None}


def test_credits_refusal_reports_status_and_error_type(tmp_path):
    refused = {
        "status": 403,
        "body": {"error": {"type": "permission_error", "message": "Invalid authorization"}},
    }
    payload = result_of(tmp_path, routes(refused))
    assert payload["credits"] == [
        {
            "status": 403,
            "error": "permission_error",
            "error_code": None,
            "code": None,
            "credit": None,
        }
    ]


def test_a_credits_200_with_an_unparsable_body_has_no_credit(tmp_path):
    payload = result_of(tmp_path, routes({"status": 200, "invalid": True}))
    assert payload["credits"] == [
        {"status": 200, "error": None, "error_code": None, "code": None, "credit": None}
    ]


def test_a_hung_fetch_aborts_and_still_settles(tmp_path):
    out = run(tmp_path, routes({"hang": True}), runs=3)
    assert out["runs"][0] == RETRY
    assert out["runs"][1] == {"error": "AbortError"}
    assert out["runs"][2] == {"error": "AbortError"}


def test_a_hung_organizations_fetch_aborts_too(tmp_path):
    route_map = routes()
    route_map["/api/organizations"] = {"hang": True}
    out = run(tmp_path, route_map)
    assert out["runs"][-1] == {"error": "AbortError"}
    assert out["calls"] == ["/api/organizations"]


def test_an_exception_yields_only_its_name(tmp_path):
    message = f"Failed to fetch https://platform.claude.com/api/organizations/{ORG_ID}/prepaid/credits"
    out = run(tmp_path, routes({"throw": "TypeError", "message": message}))
    assert out["runs"][-1] == {"error": "TypeError"}
    assert ORG_ID not in json.dumps(out["runs"])


def test_too_many_organizations_reports_only_the_count(tmp_path):
    many = [
        {"uuid": f"org-{i}", "name": f"name-{i}", "capabilities": ["api"]}
        for i in range(11)
    ]
    out = run(tmp_path, routes(orgs=many))
    assert out["runs"][-1] == {"orgs_status": 200, "too_many_orgs": 11}
    assert out["calls"] == ["/api/organizations"]


def test_no_api_organization_is_an_empty_credit_list(tmp_path):
    chat_only = [{"uuid": CHAT_ORG_ID, "name": ORG_NAME, "capabilities": ["claude_max"]}]
    out = run(tmp_path, routes(orgs=chat_only))
    assert out["runs"][-1] == {"orgs_status": 200, "api_orgs": 0, "credits": []}


# -- nothing sensitive leaves the page ------------------------------------------


def test_payload_holds_no_org_id_name_tranche_id_or_scope_content(tmp_path):
    orgs = orgs_body() + [
        {"uuid": OTHER_ORG_ID, "name": "Another " + ORG_NAME, "capabilities": ["api", "x"]}
    ]
    route_map = routes(orgs=orgs)
    route_map[f"/api/organizations/{OTHER_ORG_ID}/prepaid/credits"] = {
        "status": 200,
        "body": credits_body(promo(scope=SCOPE_B), amount=10000),
    }
    out = run(tmp_path, route_map)
    text = json.dumps(out["runs"])
    for secret in (
        ORG_ID, OTHER_ORG_ID, CHAT_ORG_ID, ORG_NAME, TRANCHE_ID,
        TRANCHE_NAME, "ws-secret-scope", "workspaces", "models",
    ):
        assert secret not in text, secret
    payload = out["runs"][-1]
    assert payload["api_orgs"] == 2
    assert len(payload["credits"]) == 2


def test_scope_token_is_the_index_among_distinct_scopes_in_that_response(tmp_path):
    body = credits_body(
        promo(scope=SCOPE_B),            # first seen: token 0
        promo(scope=SCOPE_A),            # token 1
        promo(scope=SCOPE_B),            # token 0 again
        promo(scope={"workspaces": ["ws-secret-scope-a"], "models": "all"}),  # equal to A: token 1
        amount=40000,
    )
    payload = result_of(tmp_path, routes({"status": 200, "body": body}))
    tokens = [t["scope_token"] for t in payload["credits"][0]["credit"]["promo_tranches"]]
    assert tokens == [0, 1, 0, 1]
    assert all(isinstance(t, int) for t in tokens)


def test_a_missing_scope_is_one_distinct_value(tmp_path):
    no_scope = promo()
    del no_scope["scope"]
    null_scope = promo(scope=None)
    null_scope["scope"] = None
    body = credits_body(no_scope, null_scope, promo(scope=SCOPE_A), amount=30000)
    payload = result_of(tmp_path, routes({"status": 200, "body": body}))
    tokens = [t["scope_token"] for t in payload["credits"][0]["credit"]["promo_tranches"]]
    assert tokens == [0, 0, 1]


def test_scope_tokens_restart_for_each_organization_response(tmp_path):
    orgs = [
        {"uuid": ORG_ID, "capabilities": ["api"]},
        {"uuid": OTHER_ORG_ID, "capabilities": ["api"]},
    ]
    route_map = routes(orgs=orgs)
    route_map[f"/api/organizations/{OTHER_ORG_ID}/prepaid/credits"] = {
        "status": 200,
        "body": credits_body(promo(scope=SCOPE_B)),
    }
    payload = result_of(tmp_path, route_map)
    firsts = [
        c["credit"]["promo_tranches"][0]["scope_token"] for c in payload["credits"]
    ]
    assert firsts == [0, 0]


def test_free_text_fields_are_dropped_unless_they_look_like_data(tmp_path):
    t = promo(program_id="Subscription API Credit!", currency="not-a-code",
              expires_at="tomorrow-ish", granted_at="<script>")
    payload = result_of(tmp_path, routes({"status": 200, "body": credits_body(t)}))
    kept = payload["credits"][0]["credit"]["promo_tranches"][0]
    assert kept["program_id"] is None
    assert kept["currency"] is None
    assert kept["expires_at"] is None
    assert kept["granted_at"] is None


def test_odd_tranche_entries_pass_through_as_null_for_the_builder_to_reject(tmp_path):
    body = credits_body(None, "text", promo())
    payload = result_of(tmp_path, routes({"status": 200, "body": body}))
    tranches = payload["credits"][0]["credit"]["promo_tranches"]
    assert tranches[0] is None and tranches[1] is None
    assert tranches[2]["program_id"] == "subscription_api_credit"


def test_a_credit_without_promo_tranches_keeps_the_gap_visible(tmp_path):
    body = credits_body()
    del body["promo_tranches"]
    payload = result_of(tmp_path, routes({"status": 200, "body": body}))
    assert payload["credits"][0]["credit"]["promo_tranches"] is None


# -- end to end with the builder ------------------------------------------------


def test_extractor_output_builds_a_snapshot(tmp_path):
    from datetime import datetime

    from aigauge.models import SnapshotStatus
    from aigauge.providers.claude_api import _build_snapshot

    body = credits_body(promo(remaining=8760), amount=8760 + 1234, without=1234)
    payload = result_of(tmp_path, routes({"status": 200, "body": body}))
    snap = _build_snapshot(payload, "claude_api", datetime(2026, 10, 23, 12, 0))
    assert snap.status == SnapshotStatus.OK
    assert [m.label for m in snap.metrics] == ["Credit", "Left $87.60 · Other $12.34"]
    assert snap.metrics[0].percent_used == pytest.approx(12.4)


# -- shapes measured on a real Console account (P2 result in the plan) -----------

SIGNED_OUT_BODY = {
    "type": "error",
    "error": {
        "type": "permission_error",
        "message": f"Invalid session for {ORG_ID}",
        "details": {
            "error_visibility": "user_facing",
            "error_code": "account_session_invalid",
        },
    },
    "request_id": "req_011CAAAAAAAAAAAAAAAAAAAA",
}
REFUSAL_BODY = {
    "type": "error",
    "error": {"type": "permission_error", "message": f"No billing access for {ORG_ID}"},
    "request_id": "req_011CBBBBBBBBBBBBBBBBBBBB",
    "code": "permission_denied",
}


def test_organizations_signed_out_reports_the_error_code(tmp_path):
    out = run(tmp_path, routes(orgs=SIGNED_OUT_BODY, orgs_status=403))
    assert out["runs"][-1] == {
        "orgs_status": 403,
        "orgs_error": "permission_error",
        "orgs_error_code": "account_session_invalid",
    }
    assert ORG_ID not in json.dumps(out["runs"])


def test_permission_refusal_has_the_same_type_no_error_code_and_a_top_level_code(tmp_path):
    payload = result_of(tmp_path, routes({"status": 403, "body": REFUSAL_BODY}))
    assert payload["credits"] == [
        {
            "status": 403,
            "error": "permission_error",
            "error_code": None,
            "code": "permission_denied",
            "credit": None,
        }
    ]


def test_a_non_json_body_reports_no_type_and_no_code(tmp_path):
    payload = result_of(tmp_path, routes({"status": 403, "invalid": True}))
    assert payload["credits"] == [
        {"status": 403, "error": None, "error_code": None, "code": None, "credit": None}
    ]


@pytest.mark.parametrize(
    "code", ["Permission", "has space", "x" * 41, "", 7, None, ["a"], {"a": 1}]
)
def test_top_level_code_must_be_a_short_lowercase_word(tmp_path, code):
    body = {"error": {"type": "permission_error"}, "code": code}
    payload = result_of(tmp_path, routes({"status": 403, "body": body}))
    assert payload["credits"][0]["code"] is None


def test_credits_signed_out_reports_the_error_code(tmp_path):
    payload = result_of(tmp_path, routes({"status": 403, "body": SIGNED_OUT_BODY}))
    assert payload["credits"] == [
        {
            "status": 403,
            "error": "permission_error",
            "error_code": "account_session_invalid",
            "code": None,
            "credit": None,
        }
    ]
    assert ORG_ID not in json.dumps(payload)


@pytest.mark.parametrize(
    "code",
    ["Session Invalid!", "UPPER", "x" * 41, "", 7, None, {"nested": True}],
)
def test_error_code_must_be_a_short_lowercase_word(tmp_path, code):
    body = {"error": {"type": "permission_error", "details": {"error_code": code}}}
    payload = result_of(tmp_path, routes(orgs=body, orgs_status=403))
    assert payload == {
        "orgs_status": 403,
        "orgs_error": "permission_error",
        "orgs_error_code": None,
    }


def test_error_without_usable_details_has_no_code(tmp_path):
    for error in ({"type": "permission_error"}, {"type": "permission_error", "details": "x"}):
        payload = result_of(tmp_path, routes(orgs={"error": error}, orgs_status=403))
        assert payload["orgs_error_code"] is None


MEASURED_NAME = "Measured Promo Grant"
MEASURED_TRANCHE_ID = "tranche-0000-measured"
MEASURED_PRODUCTS = ["agent_sdk", "api", "batch", "console", "product_xx", "product_yy"]


def measured_credits_body():
    def minor(amount):
        return {"money": None, "credits": {"amount_minor": amount, "exponent": 2}}

    return {
        "amount": 4184,
        "amount_without_scoped_credits": 0,
        "currency": "USD",
        "balance": minor(4184),
        "balance_credits": 41,
        "auto_reload_settings": None,
        "auto_reload_disabled_reason": None,
        "auto_reload_stripe_error_code": None,
        "pending_invoice_amount_cents": None,
        "last_paid_purchase_cents": None,
        "expiry_policy_months": None,
        "credit_expiry_policy": None,
        "auto_reload_terms_banner": None,
        "tranches": [],
        "next_expires_at": "2026-11-05T00:00:00Z",
        "promo_tranches": [
            {
                "program_id": "subscription_api_credit",
                "currency": "USD",
                "granted_amount_minor_units": 10000,
                "remaining_amount_minor_units": 4183,
                "granted_at": "2026-10-08T13:47:24.772000Z",
                "expires_at": "2026-11-05T00:00:00Z",
                "granted": minor(10000),
                "remaining": minor(4183),
                "name": MEASURED_NAME,
                "id": MEASURED_TRANCHE_ID,
                "scope": {
                    "kind": "scoped",
                    "applies_to": [
                        {"product": p, "model_family": None, "model_version": None}
                        for p in MEASURED_PRODUCTS
                    ],
                },
            }
        ],
    }


def test_measured_console_response_builds_the_credit_bar(tmp_path, caplog):
    import logging
    from datetime import datetime

    from aigauge.models import SnapshotStatus
    from aigauge.providers.claude_api import _build_snapshot

    console_org = {
        "uuid": ORG_ID,
        "name": ORG_NAME,
        "capabilities": ["api", "customer_terms:standard"],
    }
    chat_org = {
        "uuid": CHAT_ORG_ID,
        "name": "Chat " + ORG_NAME,
        "capabilities": ["claude_max", "chat"],
    }
    route_map = routes(
        {"status": 200, "body": measured_credits_body()},
        orgs=[chat_org, console_org],
    )
    out = run(tmp_path, route_map)
    assert out["calls"] == [
        "/api/organizations",
        f"/api/organizations/{ORG_ID}/prepaid/credits",
    ]
    exported = json.dumps(out["runs"][-1])
    for secret in (
        ORG_ID, CHAT_ORG_ID, ORG_NAME, MEASURED_NAME, MEASURED_TRANCHE_ID,
        "agent_sdk", "product_xx", "applies_to", "model_family", "kind",
    ):
        assert secret not in exported

    caplog.set_level(logging.INFO, logger="aigauge.providers.claude_api")
    snap = _build_snapshot(out["runs"][-1], "claude_api", datetime(2026, 10, 23, 12, 0))
    assert snap.status == SnapshotStatus.OK
    credit_bar = snap.metrics[0]
    assert credit_bar.label == "Credit"
    assert round(credit_bar.percent_used, 2) == 58.17
    assert credit_bar.guard_eligible is False
    assert [m.label for m in snap.metrics] == ["Credit", "Left $41.83"]
    assert snap.raw["other"] == "agree"
    assert "other=agree" in caplog.records[-1].getMessage()
