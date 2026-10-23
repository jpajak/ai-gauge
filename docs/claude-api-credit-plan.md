# Plan: show the monthly Claude API credit (Max and Team plans)

**Status:** in progress since 2026-10-23. Commits 1 and 2 are in, commit 3 is
being built. P1 and P2 are recorded; commit 5 turns the provider's
provisional signed-out and permission rules into the measured ones. First
written 2026-10-08; revised 2026-10-23 after an outside review, whose points
are folded into [Review outcome](#review-outcome-2026-10-23).
**Found while:** asking how to monitor the new subscriber API credit
**Size:** 5 commits, plus one cherry-pick if check P1 finds no stable cookie
**Branch:** `feature/claude-api-credit`, cut from the tip of
`feature/provider-registry-minimax` (PR #15). It needs the registry, not the
MiniMax acceptance test. It ships as its own PR, after #15.
**Decided:** its own tile, off by default, with its own Console sign-in. The
claude.ai session is not reused.

## Problem

Since 2026-10-07, Claude Max and Team plans include a monthly credit for the
Claude API: $100 on Max 5x, $200 on Max 20x, up to $500 pooled on Team. The
subscriber links one Claude Console organization and the credit lands there
each billing cycle. It is spent before purchased credit, and what is left
expires at the end of the cycle with no rollover
([docs](https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers)).

The gauge shows subscription limits but not this credit. Two questions are
worth answering at a glance: how much is left, and is it on pace to be used
before it expires.

claude.ai does not show the remaining amount. Settings > Billing there lists
only the monthly grant and the next grant date. The remaining amount is on the
Console, under Settings > Billing > Promotional credits.

Facts from the docs page (read 2026-10-23) that shape the design:

- Each plan links to one Console organization, and each organization takes
  credit from one plan. Two organizations with subscription credit under one
  Console login means two eligible plans.
- An upgrade from Max 5x to 20x grants prorated credit at once, so two
  subscription grants can be active in one cycle without a rollover.
- The credit covers the API, Managed Agents, the Agent SDK and the
  playground. It does **not** cover Claude Code.
- When it runs out, requests continue on purchased credit if there is any,
  and stop otherwise.
- Claiming needs the Owner, Admin or Billing role in the Console org. A member
  without one may not be able to read billing.

## What was measured

All of this was read on 2026-10-08 from a signed-in browser on a Max 5x
account, with read-only GETs. Ids and names are removed.

**The Console route has the exact balance.** With a Console session:

`GET https://platform.claude.com/api/organizations/<org uuid>/prepaid/credits`

```json
{
  "amount": 10000,
  "currency": "USD",
  "balance_credits": 100,
  "amount_without_scoped_credits": 0,
  "tranches": [],
  "promo_tranches": [
    {
      "name": "API credit (Max 5x)",
      "program_id": "subscription_api_credit",
      "currency": "USD",
      "granted_amount_minor_units": 10000,
      "remaining_amount_minor_units": 10000,
      "granted_at": "2026-10-08T13:47:24.772000Z",
      "expires_at": "2026-11-05T00:00:00Z",
      "id": "<uuid>",
      "scope": "<object, not inspected>"
    }
  ],
  "next_expires_at": "2026-11-05T00:00:00Z"
}
```

Other top-level keys, all null here: `auto_reload_settings`,
`pending_invoice_amount_cents`, `last_paid_purchase_cents`,
`expiry_policy_months`, `credit_expiry_policy`. Each tranche also carries
`granted` and `remaining` objects of the form
`{"credits": {"amount_minor": 10000, "exponent": 2}}`.

**The page can find the org id itself.** `GET /api/organizations` on
platform.claude.com returned every organization the account belongs to. The
Console one has `"api"` in `capabilities`; the chat one has `"claude_max"`.
Each entry has a `uuid`.

**The claude.ai session cannot read it.** The same route on claude.ai, for the
Console org, returned 403 "Invalid authorization for organization". A browser
signed in to claude.ai was not signed in to platform.claude.com. claude.ai and
claude.com are different sites, so a claude.ai cookie is never sent to the
Console.

**claude.ai has the grant facts but no balance.**
`GET https://claude.ai/api/quirky-lollipop/link-state?organization_uuid=<chat org>`
returned `status: "linked"`, `monthly_credit_usd: "100.00"`,
`next_grant_at: "2026-11-04T04:07:23Z"`, `grant_count: 1`, `link.linked_at`,
`link.console_org`, and `last_grant` with
`period_start_at: "2026-10-02T22:07:23Z"`.

Three things follow from comparing the two responses:

- The first grant is made when the org is linked (`granted_at` is two seconds
  after `linked_at`), not at the start of the billing period.
- `expires_at` is midnight UTC after the next grant, so an old and a new
  tranche may overlap from 2026-11-04 04:07Z to 2026-11-05 00:00Z on the test
  account. Not observed yet.
- `amount` includes the promo tranche. `amount_without_scoped_credits` was 0
  with no purchased credit, so it is probably the purchased balance. That is an
  inference from one account.

**There is no official balance endpoint.** The Usage and Cost Admin API
reports historical spend, not a grant's remaining amount or expiry. A request
for a balance endpoint was closed as not planned
([claude-code#47574](https://github.com/anthropics/claude-code/issues/47574)).

## Review outcome (2026-10-23)

What the review changed, and what this revision adds that it did not raise.

| Point | Outcome |
|---|---|
| Use the provider registry instead of the hardcoded tables | Taken. [Current wiring](#current-wiring) is rewritten for it. |
| Separate branch on the registry, not inside PR #15 | Taken. |
| Prove the Console sign-in before relying on it | Taken, split: P1 and P2 are checks in a browser, the in-gauge sign-in is the gate after commit 3. |
| Positive evidence of a session, not absence of login text | Taken. The verify predicate fetches `/api/organizations` and needs a 200 with a list. |
| A 403 is not necessarily a sign-out | Taken. `AUTH_REQUIRED` only on the signed-out response P2 measures, or a redirect off the Console host. |
| Diagnostics: status and structure only | Taken. The provider logs one structural line and does not use `log_page_diagnosis`. In-page errors report only `e.name`, because a message can carry a URL with an org id. |
| No silent org choice, no `slice(0, 5)` | Taken. Ambiguity shows a clear error instead of a guess. |
| Org picker that stores an org id | Not built. Deferred with a trigger, see [Deferred](#deferred). Needing it takes two eligible plans under one Console login, or several orgs with only purchased credit. |
| Several grants: no single tranche by latest expiry, no balance hidden in a tooltip | Taken. Grants with one expiry combine into the bar, an older expiry group gets its own text line on the tile. |
| No bar when a trustworthy percentage is unknown | Taken. |
| "Other credit" only when the scopes are known to match | Taken, as a cross-check: shown only when two independent derivations agree. |
| Exclude credit from the MCP guard explicitly, not by tag | Taken, and the account's pause row is hidden: the credit cannot pay for Claude Code, which is what the guard governs. |
| Admin API wording | Taken. The rejection now rests on the API reporting spend, not balance. |
| No paid test request | Taken. The manual check compares a reading after ordinary API use. |
| Use `d3c98d1` if there is no stable auth cookie | Conditional on P1. |
| Rollover not a prerequisite | Agreed. Fixtures cover overlap; a live read in the 2026-11-04 window is a follow-up. |

Added here, not in the review:

- **Cookie domain matching.** `_domain_matches` (`external_login.py:116`)
  accepts the spec domain and its subdomains only. If the Console session
  cookie is set on `.claude.com`, a spec domain of `platform.claude.com` drops
  it silently. P1 records each cookie's domain.
- **Retry budget.** The scraper allows 5 reruns of at most 5000 ms and a 25 s
  scrape timeout (`scraper.py:255-262`, `scraper.py:90`). Each in-page fetch
  gets its own timeout so the whole extract fits, and the result is assigned
  once, after every fetch has settled. Running out of reruns already ends in a
  scrape error, never a snapshot.
- **Host check first.** The extractor reports signed out if the page is not on
  `platform.claude.com`, before it fetches anything.
- **Default account.** The registry's pattern adds a default account with the
  toggle off (as MiniMax does) through a config migration. The first draft's
  "no default account" predates the registry.

## Before the build

Two checks in John's own Chrome, signed in to the Console. Neither needs code.

**P1. Console cookies.** DevTools > Application > Cookies >
`https://platform.claude.com`. For each cookie note the name, Domain,
HttpOnly and Expires. Not the values.

- A stable HttpOnly session cookie (the old console.anthropic.com used
  `sessionKey`) becomes `CookieSpec.name` and `aliases`, and its Domain becomes
  `CookieSpec.domain`. The normal external sign-in then works as for Claude.
- No stable name: cherry-pick `d3c98d1` from `feature/page-session-proof` as
  the first commit, set the page proof on this spec, and update
  `docs/provider-registry-plan.md:130`, which says that commit lands with
  Langdock. Also decide the saved-cookie fallback question at
  `provider-registry-plan.md:167-174` for this provider.
- The Expires value is the first clue to how long a Console session lasts.

**P1 result (2026-10-23).** Stable session cookies exist, so `d3c98d1` is
not needed:

| Cookie | Domain | HttpOnly | Expires |
|---|---|---|---|
| `sessionKey` | `.platform.claude.com` | yes | 2026-10-30 or 31 |
| `sessionKeyV3` | `platform.claude.com` | yes | 2026-10-30 or 31 |
| `sessionKeyLC`, `sessionKeyV3LC` | as above | no | as above |
| `routingHint` (two) | both forms | yes | 2026-11 |
| `activitySessionId` | `platform.claude.com` | yes | 2026-10 |
| `__cf_bm`, `lastActiveOrg`, analytics | `.claude.com` | mixed | various |

`CookieSpec(domain=".platform.claude.com", name="sessionKey",
aliases=("sessionKey", "sessionKeyV3"))`. `_domain_matches` strips the
leading dot, so both forms match, and the `.claude.com` cookies (Cloudflare's
`__cf_bm`, analytics) stay out; the scraper's own page load gets a fresh
`__cf_bm`. The session cookies expire about a week after the reading. Whether
they roll forward on use is not known yet.

**P2. Response shapes.** Paste this into the DevTools console of a
platform.claude.com tab (Chrome asks to type "allow pasting" first). It makes
only GETs and copies a sanitized result to the clipboard. Ids, names and
messages come out as `"string"`.

```js
(async () => {
  // Keeps numbers, booleans, nulls, lowercase enum words, currency codes and
  // ISO timestamps. Every other string (ids, names, messages) becomes "string".
  const clean = v => Array.isArray(v) ? v.map(clean)
    : v && typeof v === 'object'
      ? Object.fromEntries(Object.entries(v).map(([k, x]) => [k, clean(x)]))
    : typeof v === 'string' &&
      !/^([a-z_]{2,40}|[A-Z]{3}|\d{4}-\d\d-\d\dT[\d:.]+Z?)$/.test(v)
      ? 'string' : v;
  const get = async (url, init = {}) => {
    const r = await fetch(url, { headers: { accept: 'application/json' }, ...init });
    const type = (r.headers.get('content-type') || '').split(';')[0];
    const body = type.includes('json') ? await r.json().catch(() => null) : null;
    return { status: r.status, type, body };
  };
  const anon = await get('/api/organizations', { credentials: 'omit' });
  const orgs = await get('/api/organizations');
  const list = Array.isArray(orgs.body) ? orgs.body : [];
  const credits = [];
  for (const o of list) {
    const c = await get(`/api/organizations/${o.uuid}/prepaid/credits`);
    credits.push({ capabilities: o.capabilities, status: c.status,
                   type: c.type, body: clean(c.body) });
  }
  const out = {
    signed_out: { status: anon.status, type: anon.type, body: clean(anon.body) },
    orgs: { status: orgs.status, type: orgs.type,
            entries: list.map(o => ({ capabilities: o.capabilities,
                                      keys: Object.keys(o).sort() })) },
    credits,
  };
  window.__aigaugeProbe = JSON.stringify(out, null, 2);
  console.log(window.__aigaugeProbe);
})();
```

Then run `copy(__aigaugeProbe)` on its own line. DevTools' `copy()` exists
only at the console's top level, not after an `await`, which is why the first
run ended in "copy is not defined".

**P2 result (2026-10-23),** same Max 5x account, sanitized:

- **Signed out** (`/api/organizations` without cookies): 403 JSON,
  `error.type: "permission_error"`,
  `error.details.error_code: "account_session_invalid"`.
- **Permission refusal** (`prepaid/credits` for the chat org): 403 JSON,
  `error.type: "permission_error"`, no `details`, top-level
  `code: "permission_denied"`.
- Both share status and `error.type`; only `error.details.error_code` tells
  them apart. The extractor reports that code too.
- **Org list:** 200, two orgs. The Console org's `capabilities` are `api` and
  `customer_terms:standard`; the chat org's are `claude_max` and `chat`.
- **Credits:** the shape of 10-08 plus `balance` (`{money, credits}`) and three
  more null auto-reload keys. `amount` 4184, `amount_without_scoped_credits`
  0, one subscription tranche granted 10000 with **remaining 4183**. The total
  and the tranche differ by one minor unit, presumably because each is rounded
  from a finer figure, so the "Other" cross-check needs a tolerance.
- **Scope:** `{"kind": "scoped", "applies_to": [{product, model_family,
  model_version}, ...]}` listing `agent_sdk`, `api`, `batch`, `console` and
  two products whose names the probe masked. No ids, so the scope token rule
  holds: grants of one program share it.

What each part settles:

| Output | Decides |
|---|---|
| `signed_out` | The signed-out status and error type. A JSON 401 makes the rule simple; an HTML 403 means Cloudflare answered, and the rule has to lean on the error type of a JSON response. |
| `credits[].status` for the chat org | What a "not a billing org" or permission refusal looks like, so it is not read as a sign-out. |
| `promo_tranches[].scope` | Whether scope is identical across subscription grants (combine them) or carries a per-grant id (combine on `program_id`, expiry and currency, and compare scope by its keys only). |
| `amount`, `amount_without_scoped_credits` | A second sample for the "Other credit" cross-check. |

Record the result under [What was measured](#what-was-measured) and turn it
into the builder's fixtures.

## Current wiring

On `feature/provider-registry-minimax` at `c94f430`. The registry derives the
Settings page, enable checkbox, MCP account ids, menu-bar label, order slot,
tile family, Sign in wording and default account from one spec
(`docs/provider-registry-plan.md:253-300`).

Registry and config:

- `ProviderSpec`, `LoginSpec`, `CookieSpec`: `providers/registry.py:30-87`.
  Specs are declared at `registry.py:157`; Claude's is the browser-session
  example at `registry.py:158-183`.
- `account_family` at `registry.py:350` matches `kind` or `kind-<suffix>`.
  `claude_api` and `claude_api-2` do not start with `claude-`, so they are
  never read as Claude accounts, and `_claude_auth_redirect`
  (`webview/scraper.py:50`) ignores them.
- `ProviderToggles` at `config.py:119`, one explicit field per spec, checked
  by `tests/test_provider_registry.py`.
- Default-account migrations in `Config._migrate` at `config.py:269`;
  `browser_accounts_version` is 3 (`config.py:236`, last step at
  `config.py:346`).

Webview:

- Sign-in hosts come from `LoginSpec.hosts` (`registry.py:374`). Shared
  identity-provider hosts are `_IDENTITY_PROVIDER_HOSTS` at
  `login_window.py:89`; `google.com` is already there.
- Verify targets: `VERIFY_TARGETS` at `webview/targets.py:13`. The predicate
  is polled up to 12 times at 1 s (`login_window.py:822-850`), so it can start
  a fetch on one call and read the answer on a later one.
- External sign-in readiness uses domain-filtered cookies
  (`external_login.py:287-299`), filtered by `_domain_matches` at
  `external_login.py:116`.
- The scraper loads the page fresh on every attempt (`scraper.py:165`), so
  in-page state cannot survive into the next refresh. Extractor reruns:
  `scraper.py:244-273`.

Widget and display:

- A signed-out snapshot replaces the tile body with "not signed in" and a Sign
  in button; there is no per-row signed-out state.
- A metric with `percent_used=None` renders as a text line whose label carries
  the figures, as OpenRouter's balance line does
  (`providers/openrouter.py:196-227`). `note` is a tooltip only
  (`widget.py:967`).
- History skips metrics without a percentage or reset time (`history.py:95`).
  The weekly ratio reads only "session" and "weekly".

MCP guard:

- `usage_cache.py:67` sets `guard_eligible = metric.tag is None`.
- `mcp_server.py:113` takes only eligible percentages; an account with none
  is blocked with `no_eligible_metric` (`mcp_server.py:251`).
- Settings lists every browser account in the pause policies
  (`settings_dialog.py:1355`), so the new account would get a "Pause at 90%"
  row by default.
- `UsageMetric` is at `models.py:14`. `SnapshotStatus` has only `OK`,
  `AUTH_REQUIRED` and `ERROR`.

## Recommended approach

A browser-session spec `claude_api`, shown as a tile named "Claude API", off
by default.

```
Claude                              ~7.9/wk
Session  ▏░░░░░░░░░░░░░   2%   4h 30m
Weekly   ███████░░░░░░░  49%   3.9d

Claude API
Credit   ██░░░░░░░░░░░░  12%   13d
Left $87.60 · Other $12.34
```

### 1. Spec

In `providers/registry.py`:

- `id="claude_api"`, `display_name="Claude API"`, `settings_label="Claude API"`,
  `short_label="CA"`, `auth="browser_session"`, `multi_account=True`,
  `default_enabled=False`, `default_order` right after Claude (renumber the
  rest if needed; the order test will say).
- `LoginSpec(url="https://platform.claude.com/login",
  window_title="Sign in to Claude Console", prompt_name="Claude Console",
  hosts=("claude.com", "anthropic.com"))`.
- `CookieSpec(domain=".platform.claude.com", name="sessionKey",
  aliases=("sessionKey", "sessionKeyV3"))`, from P1.
- `usage_url="https://platform.claude.com/settings/billing"`,
  `paste_cookie=False`.
- `settings_hint`: the shared browser sign-in hint, plus one sentence that the
  Console is a separate sign-in from claude.ai and needs the Owner, Admin or
  Billing role to read billing.
- A new spec field `usage_guard: bool = True`, set to False here. Settings
  skips the pause-policy row for accounts whose spec sets it False.

Plus a `ProviderToggles.claude_api = False` field and a migration to version 4
that adds `{"id": "claude_api", "kind": "claude_api"}` to an explicit account
list that has none, as the version-2 OpenCode step does.

### 2. Sign-in verification

`VERIFY_TARGETS["claude_api"]` loads
`https://platform.claude.com/settings/billing`. The predicate returns false
off `platform.claude.com`. Otherwise its first call starts
`fetch('/api/organizations')` and stores the outcome on `window`; later calls
return true only for a 200 whose body is a list. Login text is not consulted:
the signed-out Console keeps the URL and title, and a loading page has no
text.

### 3. Extractor

New `src/aigauge/providers/claude_api.py`, shaped like the Claude provider: a
`ScrapeRunner` on the billing URL, an extractor, and a pure `_build_snapshot`.
The extractor fetches JSON in the page.

```js
(() => {
  const S = (window.__aigaugeCredit = window.__aigaugeCredit || {});
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
      const api = orgs.body.filter(o => (o.capabilities || []).includes('api'));
      if (api.length > 10) return { orgs_status: 200, too_many_orgs: api.length };
      const credits = await Promise.all(api.map(o =>
        get(`/api/organizations/${o.uuid}/prepaid/credits`)
          .then(c => ({ status: c.status, error: errorType(c.body),
                        credit: c.status === 200 ? pick(c.body) : null }))));
      return { orgs_status: 200, api_orgs: api.length, credits };
    })().then(
      r => { S.result = r; },
      e => { S.result = { error: e && e.name || 'Error' }; });
  }
  return S.result ||
    { __retry_after_ms: 2000, __retry_reason: 'credit fetch pending' };
})()
```

- `S.result` is assigned once, after every fetch has settled. Two fetch stages
  of at most 4 s each fit the rerun budget (5 reruns of 2 s) and the 25 s
  scrape timeout.
- `errorType` returns only `body.error.type` when it is a short lowercase
  word, else null.
- `pick` keeps `amount`, `amount_without_scoped_credits`, `currency`, and per
  promo tranche `program_id`, `currency`, `granted_amount_minor_units`,
  `remaining_amount_minor_units`, `granted_at`, `expires_at`, the two
  `exponent` values, and a scope token. The token is the index of the tranche's
  scope among the distinct scopes in that response (rule set after P2). Ids,
  names and scope contents stay in the page.

### 4. Snapshot rules

**Status.**

- `AUTH_REQUIRED` only for `signed_out` (off-host), a 401, or a 403 whose
  `error.details.error_code` is `account_session_invalid`, on
  `/api/organizations` or on a credits fetch.
- A credits 403 without that code is the permission refusal of org rule 4.
- Any other non-200 from `/api/organizations` (including other 403s), any
  other credits status, a timeout, or `error`: `ERROR`, "Couldn't read Console
  billing".
- An unknown shape anywhere: `ERROR`. Never zero, never the last reading.

**Which org.**

1. Exactly one org with an active subscription grant: use it.
2. Two or more: `ERROR`, "Several Console organizations receive subscription
   credit. Not supported yet."
3. None, exactly one org with `api`, and it returned 200: use it (purchased
   credit only, text line, no bar).
4. None, and that one org was refused: `ERROR`, "This Console sign-in can't
   see billing. It needs the Owner, Admin or Billing role."
5. None, several orgs: `ERROR`, "Several Console organizations. Not supported
   yet."
6. No org with `api`: `ERROR`, "No Console organization with API access."

A failed fetch fails the whole refresh (status rules), so a transient failure
of the linked org can never hand the tile to another org.

**Validation, per tranche.** Minor units are integers,
`0 <= remaining <= granted`, `granted > 0`, `granted_at < expires_at`,
currency equals the top-level currency, exponents are 2 where present. A
subscription tranche (`program_id == "subscription_api_credit"`) that fails is
an unknown shape. Other programs are ignored except through `amount`.

**Grouping.** Active subscription tranches (not expired) group by
`expires_at`, currency and scope token. The **current group** is the one with
the latest expiry.

**Bar.** `UsageMetric(label="Credit", guard_eligible=False)` over the current
group: `percent_used = (sum granted - sum remaining) / sum granted * 100`,
`resets_at = expires_at`, `window = expires_at - earliest granted_at` in the
group. This combines a regular grant with a prorated upgrade grant that ends
the same day. The countdown, pace tick and colours come from the existing row
code. Red near 100% is right: at $0 requests stop unless there is purchased
credit. If two groups share the latest expiry with different scope tokens,
there is no bar, only the text line.

**Text line.** A `percent_used=None` metric whose label carries the figures,
OpenRouter style:

- `Left $87.60` for the current group.
- `Other $12.34` only when
  `amount - sum remaining over all active subscription tranches` is within one
  minor unit per active tranche of `amount_without_scoped_credits`, and
  `amount_without_scoped_credits` (the figure shown) is above zero. P2 saw
  the total and the tranche one unit apart. Disagreement omits it and logs
  `other=mismatch`; a null `amount_without_scoped_credits` omits it and logs
  `other=none`. That reading needs no assumption about what either field
  means.
- An older active group with something left gets its own text line, `Older
  $4.10, ends <local time>`, so the overlap never hides behind a tooltip. A
  spent older group gets none.

The note (tooltip) states that unused credit expires at the shown time and
does not pay for Claude Code. Amounts are minor units with exponent 2; a
non-USD currency shows its code instead of "$".

**Diagnostics.** One line per refresh:
`claude_api credit orgs_status=200 api_orgs=1 credits_statuses=200
subscription_tranches=1 groups=1 other=agree result=ok`. No ids, names,
amounts or response text.

### 5. Guard eligibility

- `UsageMetric.guard_eligible: bool = True` in `models.py`. It means the
  metric is a limit an agent should pause on.
- `usage_cache.py:67` becomes `metric.tag is None and metric.guard_eligible`.
  The cache schema version stays: the field already exists and keeps its
  meaning.
- The Credit bar sets it False. The text lines have no percentage and are
  ineligible already.
- Settings hides the pause row for `usage_guard=False` specs (section 1). A
  hand-edited policy for a `claude_api` account still fails closed with
  `no_eligible_metric` and is never recommended.
- `docs/mcp.md`: one line saying balance rows are reported but never count
  toward a pause.

### 6. Docs

- README: provider list at `README.md:17`, the setup paragraph at
  `README.md:80`, a row in the table at `README.md:82-90`, and the
  multiple-accounts lines at `README.md:94-98`.
- `CHANGELOG.md` under Unreleased, with the `changelog-style` skill.

## Deferred

- **Org picker.** A Settings choice of Console org per account, stored as an
  org id, kept out of snapshots, logs, the usage cache and MCP output.
  **Trigger to start:** the first report of rule 2 or rule 5 above.
- **Row inside the Claude tile.** Keep `claude_api` as its own provider and
  add a setting that draws its rows at the bottom of a chosen Claude tile
  while its snapshot is OK. **Trigger to start:** the tile has shipped and the
  separate tile still feels like clutter.
- **Credit as a guard.** A pause policy on API spend, for agents that run on
  the API. **Trigger to start:** a request from someone running agents on the
  credit.
- **Slower refresh for this tile.** **Trigger to start:** the extra page load
  shows in refresh time.

## Alternatives considered

- **Reuse the claude.ai session.** Rejected: claude.ai and claude.com are
  different sites, and copying a cookie across them leans on undocumented
  behaviour. Profiles are keyed by account id, so the new kind gets its own
  without extra work.
- **Admin API cost report plus the claude.ai link-state route.** Rejected: the
  API reports spend, not the remaining balance or expiry. Remaining would be a
  grant minus spend from daily UTC buckets, wrong for the first cycle and for
  prorated upgrade grants unless special-cased, and it still needs an internal
  route for the cycle dates.
- **claude.ai link-state alone.** No sign-in to add, but it has no balance.
- **Reading the Console billing page text.** The page did not render in a
  background tab during testing, and text depends on wording and number
  formatting. The JSON has granted and remaining as separate numbers.
- **Calling the route from Python with the stored cookie.** It replays a
  session cookie outside the browser, which the Claude and Codex providers
  avoid by driving Chromium.

## Not verified

After P1 and P2 these remain:

- Whether the gauge's external sign-in completes for the Console, and whether
  the Console accepts the imported session in QtWebEngine. The gate after
  commit 3.
- How long a Console session lasts, and whether it survives an app restart.
- The response at rollover. A P2 run between 2026-11-04 04:07Z and
  2026-11-05 00:00Z captures it on the test account.
- The response with purchased credit, on a Team plan (pooled credit), for a
  member without a billing role, and in a non-USD currency. Until seen, each
  falls to the text-only or `ERROR` rules above.

## Risks

- **Internal route.** It can change without notice. Every unknown shape is
  `ERROR`, and no rule falls back to zero or an old reading.
- **Rollover display.** The grouping rule is untested on a live rollover.
  Fixtures cover it; the first real rollover is the confirmation.
- **Wrong org.** Removed by the selection rules: an ambiguous account shows
  an error instead of a guess.
- **Refresh cost.** One more Chromium page per refresh for a slowly changing
  number. Deferred above.

## Test strategy

Run with `.venv\Scripts\python.exe -m pytest` and `QT_QPA_PLATFORM=offscreen`.
Fixtures come from the P2 output.

- **Builder**, `tests/test_claude_api.py`, pure functions over payloads: unused
  (0%), partly used, exhausted with purchased credit left, a prorated upgrade
  grant combined with the regular one, an older group overlapping the current
  one, two groups with the same expiry and different scope tokens (no bar),
  purchased credit only, "Other" agreeing and disagreeing, each org selection
  rule, signed out, permission refusal, non-200, timeout, malformed numbers,
  `remaining > granted`, mixed currencies, non-USD.
- **Extractor under Node**, harness from `tests/test_claude_extractor_js.py`,
  stubbed `fetch` and `location`: first run returns the retry marker, a later
  run returns the payload, off-host gives `signed_out` with no fetch made, a
  hung fetch aborts and still settles, an exception yields only its name, and
  the payload holds no org id, name or scope content.
- **Verify predicate under Node** (`tests/test_login_window.py` pattern): 200
  with a list is true on a later call, 401, 403, non-list and off-host stay
  false.
- **Registry and config**: the registry tests pass with the new spec, the
  toggle defaults to off, the version-4 migration adds the account once and
  leaves an old config loading, `account_family("claude_api-2") ==
  "claude_api"` and `account_family("claude-2") == "claude"`.
- **Domains**: `_domain_matches` accepts the P1 domain and rejects
  `claude.ai`; a `sessionKey` on `.claude.ai` does not make a `claude_api`
  session ready.
- **MCP**: a Credit bar is written with `guard_eligible: false`; a policy on a
  `claude_api` account blocks with `no_eligible_metric` and is not
  recommended; existing Claude and Codex guard tests unchanged; Settings shows
  no pause row for the account.
- **Widget**: an OK snapshot draws the bar and its text lines, a signed-out
  snapshot shows Sign in, a Claude tile beside it is unchanged.
- **Manual**: sign in to the Console in the gauge; compare the bar and "Left"
  with Console > Settings > Billing > Promotional credits; restart the app and
  refresh; after ordinary API use, confirm the reading moves; clear the Console
  sign-in and confirm only that tile changes.

## Implementation checklist

1. `providers/claude_api.py` with extractor and builder, plus the builder and
   extractor tests. New files only. The Credit bar does not set
   `guard_eligible` yet; the provider is unreachable until commit 3.
2. Guard plumbing: `UsageMetric.guard_eligible`, `ProviderSpec.usage_guard`,
   the cache change, the hidden pause row, `docs/mcp.md`, MCP and Settings
   tests against a test-only spec.
3. The spec (with `usage_guard=False`; P1 found stable cookies, so no
   cherry-pick of `d3c98d1`),
   toggle, migration, verify target, and `guard_eligible=False` on the Credit
   bar. Registry, config, domain, verify and MCP tests. **Gate:** John signs
   in to the Console through the gauge and the tile reads a balance.
4. Settings hint check, README, changelog.
5. The P2 rules: the extractor reports `error.details.error_code`, the
   builder maps `account_session_invalid` to `AUTH_REQUIRED`, the "Other"
   tolerance and its null case, no line for a spent older group, and a test on
   the measured response shape. Then fixes from the manual run, most likely
   around sign-in.

Commits 1 and 2 touch different files and are built in parallel. Commit 3
needs both, and P1.

**Trigger to start:** P1 and P2 recorded in this plan, and John's go.
