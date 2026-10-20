# MiniMax Coding Plan provider: plan

Assessed 2026-10-19 against `main` at `f87036c` (0.8.5), issue #13 and draft
PR #14 by innoscoutpro (head `32c00e9`).

## Problem

MiniMax's Coding Plan (sold as Token Plan and M Plan on newer docs pages) has
the same shape as Claude and Codex: a 5-hour window and a weekly window, each
with a used percentage and a reset time. Users who run Claude Code or Codex
against MiniMax want it on the gauge. PR #14 adds it by scraping the signed-in
console page at `platform.minimax.io/console/usage` through the Claude/Codex
browser-session path.

That route costs more than it needs to:

- MiniMax exposes the same numbers over HTTP with the Coding Plan API key
  (`sk-cp-...`), the key users already hold for inference. The official
  `mmx` CLI reads quota that way (`MiniMax-AI/cli`, `src/client/endpoints.ts`,
  `quotaEndpoint`), and so do CodexBar, openclaw, kilocode and others.
- The scrape needs core changes that nothing else needs: a visible-to-Chromium
  scraper view (`webview/scraper.py`, `_prepare_provider_view`), a 70-line CDP
  page check during external sign-in (`webview/external_login.py`,
  `_minimax_usage_visible`), an exception to the encrypted cookie fallback
  (`webview/cookies.py`), a new allowed login host, and paste-cookie
  exclusions in `app.py` and `settings_dialog.py`.
- Its parser fails closed unless the weekly row literally reads
  **Unlimited**, so any tier with a finite weekly limit shows an error.
- It is pinned to today's English console labels for a product MiniMax has
  renamed twice on its docs site, and it was live-tested on Linux only.

OpenCode Go went through the same choice in 0.8.4: the browser-session scrape
was replaced by its authenticated API. MiniMax should start on the API.

## Current wiring to reuse

The API-key account family already exists for OpenCode Go. A MiniMax provider
follows it almost line for line.

| Piece | Anchor |
|---|---|
| API-key provider template | `src/aigauge/providers/opencode_go.py:1-190` |
| Per-account key in the system keychain | `src/aigauge/config.py:580-600` (`get_opencode_go_key`, `set_opencode_go_key`) |
| Account row: no Sign in button for key kinds | `src/aigauge/settings_dialog.py:563` |
| Account row: masked key field and Clear saved key | `src/aigauge/settings_dialog.py:615-640` |
| Account family group box and hint | `src/aigauge/settings_dialog.py:1309-1323` |
| Provider page assembly | `src/aigauge/settings_dialog.py:1376-1384` |
| Provider construction per account | `src/aigauge/app.py:445-473` |
| Account kinds treated as browser accounts | `src/aigauge/config.py:422`, `config.py:442-449`, `widget.py:1905`, `settings_dialog.py:543` |
| Tile family and compact collapse | `src/aigauge/widget.py:115-122`, `widget.py:1198`, `widget.py:1269`, `app.py:1347-1353` |
| Menu-bar abbreviation | `src/aigauge/menubar.py:33-39` |
| MCP account set (automatic once the kind is in `browser_accounts`) | `src/aigauge/mcp_server.py:50-61` |

## Recommended approach

### The request

```
GET https://api.minimax.io/v1/token_plan/remains
Authorization: Bearer <sk-cp-... key>
Accept: application/json
```

- Hosts, in order: `api.minimax.io` (international), `api.minimax.cn`
  (China, the official CLI's `REGIONS.cn` in `src/config/schema.ts`), then
  `api.minimaxi.com` (older China host, still recognised by the CLI's error
  module). Advance to the next host on a wrong host (`base_resp.status_code
  == 2049`, HTTP 404), a rejected key (1004, HTTP 401 or 403) or an
  unreachable host (connection error, timeout): a key from one region may be
  rejected outright by the other, and the international host may be
  unreachable from mainland China. Stop at the first success, at 2062 (no
  plan), or at any other error. If every host fails, report the most specific
  failure: rejected key, then wrong host, then unreachable. Remember the
  working host per account for the session, so a healthy refresh is one
  request. No region setting unless that fails in practice.
- MiniMax can answer HTTP 200 with the error in the body. Treat
  `base_resp.status_code != 0` as the result: 1004 means no or bad key
  (`AUTH_REQUIRED`), 2049 wrong host (retry), 2062 no active plan
  (`ERROR` with a plain message). Also handle 401 and 403 the OpenCode way.
- Older tools call `/v1/api/openplatform/coding_plan/remains` on the console
  host. That path wants the web session cookie, not the key. Do not use it.

### The response

`model_remains[]`, one row per model, typed in the official CLI
(`src/types/api.ts`, `QuotaModelRemain`):

| Field | Meaning |
|---|---|
| `model_name` | which quota bucket the row is; see row selection below |
| `current_interval_remaining_percent` | 5-hour remaining, 0 to 100; `percent_used = 100 - value` |
| `current_interval_usage_count` | 5-hour **remaining** count, despite the name |
| `current_interval_total_count` | 5-hour total count; with the field above, the fallback when the percent field is absent: `percent_used = 100 - usage_count / total * 100` |
| `end_time` | 5-hour window end, epoch milliseconds; seconds accepted |
| `current_interval_status` | 1 limited, 2 exhausted (show 100%), 3 unlimited |
| `current_weekly_remaining_percent`, `current_weekly_usage_count`, `current_weekly_total_count` | weekly equivalents, same semantics |
| `weekly_end_time` | weekly window end, epoch milliseconds; seconds accepted |
| `current_weekly_status` | same codes; 3 is the **Unlimited** weekly row PR #14 saw |
| `weekly_boost_permille` | the console shows weekly remaining as `base × boost / 1000`, up to 150% on boosted plans. The bar uses the base percent, which is the share of the boosted allowance spent and reaches 100% when the console reaches 0%. When boost is above 1000 the row note gives the console's figure so the two reconcile: "Weekly allowance boosted to 150% (console: 90% left)." |

The remaining reading of `*_usage_count` is the official CLI's: its quota
table passes `current_interval_usage_count` as the `remaining` argument of
`remainingPct` (`src/output/quota-table.ts`), and openclaw does the same.
Prefer the percent fields anyway.

Row selection, following the CLI and openclaw:

1. Drop rows where both statuses are 3 and both totals are 0. The API uses
   that shape for a model the plan does not include (CLI issue 173).
2. Prefer the row named `general` (the current chat bucket) or whose name
   starts with `minimax-m`, case-insensitively.
3. Else the first row with status 1 or 2 on either window.
4. Else the first remaining row. None left is a layout error.

### Mapping to `UsageSnapshot`

Two `UsageMetric` rows, like OpenCode Go's rolling and weekly rows:

- `5h`: `percent_used`, `resets_at` from `end_time` converted to local naive
  time (see `_parse_reset_at` in `opencode_go.py` for the convention),
  `window=timedelta(hours=5)`.
- `Weekly`: same from the weekly fields; when status is 3, `percent_used=None`
  and `reset_label="Unlimited"`, which the widget already renders
  (`widget.py:791`, `widget.py:947`).

### Core changes

Decided 2026-10-19: MiniMax lands as the first new consumer of the provider
registry (`provider-registry-plan.md`), after that refactor merges. On the
registry it needs:

1. An `api_key` `ProviderSpec`: id `minimax`, display name `MiniMax`, menu-bar
   label `Mm`, off by default, ordered after Codex, settings hint naming where
   the key comes from (`platform.minimax.io` Coding Plan or Token Plan page,
   international or China account), usage URL
   `https://platform.minimax.io/console/usage`.
2. A `minimax: bool = False` toggle field.
3. A `browser_accounts_version` 3 migration that appends the default MiniMax
   account once, as PR #14 does.

The generic per-kind key helpers (`get_account_api_key`,
`set_account_api_key`) come from the registry refactor. No change to
`webview/`, no login URL, no cookie domain, no verify target.

### What to carry over from PR #14

- The migration shape and its test (append once, do not resurrect removed
  accounts).
- Dropping the debugging port from the sign-in log line in
  `external_login.py:230-235`; a hygiene fix independent of MiniMax.
- The README row, rewritten for the key flow.
- Credit: innoscoutpro, in the changelog entry.

## Alternatives considered

- **Merge PR #14 as drafted.** Rejected: core changes to the scraper and the
  sign-in flow that no other provider needs, a parser that only accepts an
  unlimited weekly row, English console labels as the contract, Linux-only
  live check. All of that goes away on the API.
- **Both paths, API with scrape fallback** (what CodexBar does). Rejected for
  now: double the surface for a provider that is off by default. Revisit if
  the key endpoint proves unreliable.
- **Run `mmx quota` like the Antigravity CLI provider.** Rejected: adds a
  Node install requirement for one number that an HTTP call returns.

## Risks

- The endpoint is used by MiniMax's own CLI but not documented on the
  platform docs site. Mitigated by preferring the percent fields, failing
  closed on an unknown shape, and fixture tests built from the CLI's own
  fixtures.
- Host selection by error code may not cover every account type. Mitigated by
  trying every host before reporting a failure, and by the live check below
  recording which host the contributor's key landed on.
- Nobody on the project has a Coding Plan key. innoscoutpro has a subscribed
  account and offered to test; the live pass criterion below is theirs to
  run unless John buys a plan.

## Test strategy

- `tests/test_minimax.py`: `_build_snapshot` on fixtures for limited,
  exhausted, unlimited weekly, boosted weekly, missing percent fields
  (count fallback), `base_resp` 1004, 2049, 2062, HTTP 401 and 403, malformed
  body. Pattern: `tests/test_opencode_go.py`.
- Settings: the MiniMax account row shows a key field and no Sign in or Paste
  cookie button; Clear saved key clears the keyring entry; adding a second
  account yields `minimax-<n>` ids. Pattern: the OpenCode cases in
  `tests/test_settings_dialog.py`.
- Config: migration v2 to v3 appends once; a removed MiniMax account is not
  restored.
- Live, by the contributor, on one international account:
  - The 5-hour row matches the console's Plan usage card within one percent,
    on two refreshes across a 5-hour reset.
  - The weekly row is checked by relation, not equality. A boosted row
    (`weekly_boost_permille` above 1000) shows remaining on the console as
    `base × boost / 1000`, which can pass 100%, so the tile's weekly used
    percent should equal `100 − console remaining × 1000 / boost`. Example:
    60% base remaining with a 150% boost, console shows 90% left, tile shows
    40% used and its note names the console's 90%.
  - Report which host answered (the `provider api host` line in the
    diagnostic log) and whether the weekly row was boosted.
- Run with `.venv\Scripts\python.exe -m pytest tests -q` and
  `QT_QPA_PLATFORM=offscreen`.

## Size

Three to five commits: key-helper generalisation with tests; provider and
fixtures; settings page, config and widget wiring; README and changelog.

## Sources

- Official CLI quota endpoint and response type:
  <https://github.com/MiniMax-AI/cli> (`src/client/endpoints.ts`,
  `src/types/api.ts`)
- Usage windows: <https://platform.minimax.io/docs/m-plan/usage-rules>
- Independent implementations of the same call: CodexBar
  (`docs/minimax.md`), openclaw (`src/infra/provider-usage.fetch.minimax.ts`),
  kilocode (`packages/core/src/kilocode/provider-usage/minimax/usage.ts`)
