# Provider registry: plan

Assessed 2026-10-19 against `main` at `f87036c` (0.8.5), prompted by issue
#12 (Langdock), issue #13 and PR #14 (MiniMax).

## Problem

Adding a provider means editing the same literal lists in many files. PR #14
adds one browser-session provider and touches nine core files in about twenty
places, and still misses one (`menubar.py` falls back to a computed
abbreviation). The lists are easy to get out of step and every new provider
repeats the copy.

Sites on `main` that enumerate providers or account kinds by literal:

| Concern | Anchors |
|---|---|
| Cookie domain, cookie names, aliases | `src/aigauge/config.py:39-60` |
| Default order, toggles, default accounts | `src/aigauge/config.py:113-131`, `config.py:226-236` |
| Base and display names | `src/aigauge/config.py:387-393`, `config.py:457-465` |
| Browser-account kind filters | `src/aigauge/config.py:422`, `config.py:442-449`, `widget.py:1905`, `settings_dialog.py:543`, `webview/cookies.py:323`, `app.py:1065` |
| Login URL and title | `src/aigauge/app.py:74-77` |
| Provider construction | `src/aigauge/app.py:445-497` |
| Paste-cookie eligibility | `src/aigauge/app.py:926-932` |
| Compact collapse eligibility | `src/aigauge/app.py:1347-1353`, `widget.py:1198`, `widget.py:1269` |
| Tile family | `src/aigauge/widget.py:115-122` |
| Summary chip naming | `src/aigauge/widget.py:2455` |
| Settings nav, page keys, checkboxes, pages map, checkbox map | `src/aigauge/settings_dialog.py:664-676`, `:1004-1021`, `:1455-1465`, `:1501-1510` |
| Per-family account group boxes and tabs | `src/aigauge/settings_dialog.py:1070-1090`, `:1309-1323`, `:1345-1384` |
| Account row rebuild and apply | `src/aigauge/settings_dialog.py:1736-1750`, `:2079-2085` |
| Account row per-kind branches | `src/aigauge/settings_dialog.py:563`, `:615` |
| Menu-bar abbreviation | `src/aigauge/menubar.py:33-39`, `:53-59` |
| MCP account set | `src/aigauge/mcp_server.py:50-61` |
| Sign-in verification page and predicate | `src/aigauge/webview/targets.py` |
| Allowed sign-in hosts, provider names in prompts | `src/aigauge/webview/login_window.py:85-95`, `:570`; `webview/external_login.py:132-137`, `:249` |
| Paste-cookie hints | `src/aigauge/cookie_dialog.py:36` |

About thirty sites. Three provider shapes exist: browser session (Claude,
Codex), API key per account (OpenCode Go), and single-instance credentials or
CLI (Copilot, OpenRouter, Antigravity).

## Recommended approach

A static, in-code registry. No plugin loading, no entry points.

`src/aigauge/providers/registry.py`:

```python
@dataclass(frozen=True)
class ProviderSpec:
    id: str                      # "claude", "minimax", "langdock"
    display_name: str            # "Claude"
    short_label: str             # "Cl" for the menu bar
    auth: Literal["browser_session", "api_key", "single"]
    multi_account: bool
    default_enabled: bool
    default_order: int
    login: LoginSpec | None      # url, window title, allowed hosts
    cookies: CookieSpec | None   # domain, names, aliases, paste hint
    verify: VerifySpec | None    # url, predicate JS
    make: Callable[..., Provider]  # lazy import inside, see issue #7
    settings_hint: str
    usage_url: str | None

PROVIDER_SPECS: tuple[ProviderSpec, ...] = (...)
```

Derived from it, replacing the literals above: `COOKIE_DOMAINS`,
`COOKIE_NAME_ALIASES`, `LOGIN_URLS`, `VERIFY_TARGETS`, `PROVIDER_LABELS`,
`_NAV_ENTRIES`, `_PROVIDER_PAGE_KEYS`, `provider_base_name`,
`display_name_for_account`, `_provider_family`, every
`kind in ("claude", "codex", ...)` test (`browser_session_kinds()`,
`api_key_kinds()`, `account_kinds()`), and the allowed login host list.

Keep `ProviderToggles` as explicit pydantic fields so the config schema and
its migrations stay as they are. A registry test asserts every spec has a
toggle field and vice versa.

Settings gets one `_AccountFamilyPage(spec)` that builds the enable row, hint,
"Open usage in browser" button and account rows for every `multi_account`
spec. Claude, Codex, OpenCode and MiniMax use it; Copilot, OpenRouter and
Antigravity keep their bespoke forms behind the same page map.

`app._build_providers` becomes one loop over `browser_accounts(config)`
calling `spec.make(...)`, plus one loop over single-instance specs.

### Decisions for the build (2026-10-19)

- **The registry is a leaf module.** `providers/registry.py` imports nothing
  from `config`, Qt or QtWebEngine at module scope; `config.py` imports it.
  Each `make` does its provider import inside the function (issue #7).
- **What moves into the spec:** identity, display name, menu-bar label, auth
  shape, multi-account flag, default enabled, default order, login URL and
  window title, allowed sign-in hosts, cookie domain, cookie names and
  aliases, usage URL, settings hint, paste-cookie support, compact-collapse
  support, factory.
- **What stays keyed by id where it is, with a completeness test:** the
  verification JS in `webview/targets.py` (long predicates read best next to
  each other), the paste-cookie dialog hints in `cookie_dialog.py`, and the
  bespoke Copilot, OpenRouter and Antigravity settings forms. The registry
  test asserts every `browser_session` spec has a `VERIFY_TARGETS` entry and
  a cookie-dialog hint.
- **Names derived but kept:** `COOKIE_DOMAINS`, `COOKIE_NAMES`,
  `COOKIE_NAME_ALIASES`, `DEFAULT_PROVIDER_ORDER`, `LOGIN_URLS`,
  `PROVIDER_LABELS`, `_NAV_ENTRIES`, `_PROVIDER_PAGE_KEYS` and the helper
  functions keep their names and types, built from the registry, so tests and
  call sites that import them keep working.
- **API-key kinds are generic.** `get_account_api_key(kind, account_id)` and
  `set_account_api_key(kind, account_id, value)` in `config.py`; the keyring
  entry name comes from the spec (`opencode-go-key-{account_id}` for
  OpenCode, unchanged, so saved keys still load). `get_opencode_go_key` and
  `set_opencode_go_key` stay as thin wrappers. The settings account row and
  `apply_to` branch on `spec.auth == "api_key"`, not on `"opencode_go"`.
- **No behaviour change.** No provider is added, removed, renamed or
  reordered; config files load and save identically; widget, tray, menu bar
  and Settings look and act the same.

### Sign-in verification by page, not cookie name

PR #14 verifies a MiniMax sign-in by evaluating the verify predicate in the
temporary browser over CDP because MiniMax has no stable auth-cookie name
(`external_login.py`, `_minimax_usage_visible` in the PR). Langdock will be
the same. Generalise it: `VerifySpec` carries the predicate, and
`_session_is_ready` uses the cookie-name test when `cookies.names` is set and
the page predicate otherwise. The CDP evaluation is already written in the PR
and can be lifted as-is once it keys on the spec rather than on
`self._provider == "minimax"`.

Built in this pass, as the last commit, with no provider using it yet. Its
unit tests come from PR #14's `tests/test_external_login.py` cases, keyed on
a test-only spec. Langdock is the expected first user.

## First consumers

### MiniMax (issue #13, PR #14)

An `api_key` spec. Its design is in `minimax-api-provider-plan.md`. Decided
2026-10-19: the registry is built now, with no behaviour change, and MiniMax
is wired in afterwards as its first new consumer. The generic per-kind key
helpers and the `api_key_kinds()` slice are part of the registry work.

### Langdock (issue #12)

A `browser_session` spec. Langdock shows exactly the numbers AI Gauge draws:
Settings > Account > Usage at `app.langdock.com/settings/account/usage` has a
session bar and a weekly bar with the time to each reset, and the chat header
has the same figures. There is no API for a user's own usage. Hitting a limit
routes messages to a fallback model rather than blocking, so the gauge is a
"which model am I getting" indicator as much as a quota one.

What the spec needs that only a Langdock user can supply:

- The rendered English text of the usage page (labels, number formats, the
  reset phrasing) for the extractor and its fixtures.
- Which sign-in path their workspace uses (Google, Microsoft, SAML) so the
  allowed-host list is right.
- A willingness to run test builds on their OS. BYOK and dedicated
  deployments only show the page when the account has a finite included
  limit, so the signed-in-but-no-usage-page case needs a real account to see.

The requester ticked "not willing to send a PR". Asking for the three items
above is the next step on the issue; without them there is nothing to build.

## Status

Started 2026-10-19 at John's call, ahead of the trigger first proposed (a
second browser-session provider with a tester), because Langdock and further
requests are expected and there was usage to spend.

## Alternatives considered

- **Keep adding to the literal lists.** Cheapest per provider for the author
  of the PR, costliest for review, and the pattern that let PR #14 miss the
  menu-bar label. Fine for MiniMax alone; not for a third browser-session
  provider.
- **Auto-registration through `Provider.__init_subclass__`.** Rejected: wiring
  becomes implicit, and the app deliberately avoids importing QtWebEngine at
  module scope (issue #7, `providers/_scrape_runner.py:10-16`), which a
  class-level registry would undermine.
- **Entry-point plugins.** Rejected: nothing to gain in a PyInstaller-packaged
  desktop app, and third-party extractors would carry cookie access.
- **Dynamic `ProviderToggles` built from the registry.** Rejected: pydantic
  model generation at import time complicates migrations and type checking
  for no user-visible gain.

## Risks

- Large diffs in `settings_dialog.py` (2,139 lines) and `widget.py` (3,120
  lines). Mitigated by characterisation tests first: assert the derived maps
  equal today's literals, then switch call sites one file per commit.
- Tile order, collapsed-tile memory and the tray indicator all key on
  provider ids. Covered by existing tests in `tests/test_widget.py`,
  `tests/test_app.py` and `tests/test_menubar.py`; add a case per derived
  helper.
- PyInstaller hidden imports if `make` lazily imports a provider module.
  Check `ai-gauge.spec` and the packaged smoke in CI.
- Behavioural drift in the sign-in verification change. Keep the cookie-name
  path for Claude and Codex unchanged and add the predicate path beside it.

## Test strategy

1. Registry consistency: ids unique, every id has a toggle and an order slot,
   every `browser_session` spec has login, cookies or verify, every `api_key`
   spec has none of those.
2. Characterisation: derived `COOKIE_DOMAINS`, `LOGIN_URLS`, `VERIFY_TARGETS`,
   `PROVIDER_LABELS`, nav entries and kind sets equal the current literals,
   captured before the first call site moves.
3. Existing suite, offscreen, in the project venv, after each commit.
4. One manual pass per OS of Settings navigation, drag ordering, sign-in for
   Claude, and the tray indicator, since those are the paths with the most
   moved code.

## Size

Five or six commits: registry and characterisation tests; config
derivations; app, widget, menu bar, MCP; settings family page; webview maps
and predicate verification; docs and changelog.

## As built (2026-10-19)

Seven commits, no provider added. Where the build differs from the sketch
above:

- **No `VerifySpec`.** Verification JS stays in `webview/targets.py` as
  decided; the spec carries `session_proof: "cookie" | "page"` instead, and
  `ExternalLoginWorker._session_is_ready` branches on it. Every shipped spec
  is `"cookie"`.
- **Spec fields added beyond the sketch:** `settings_label` (Settings nav
  and checkbox, "GitHub Copilot" where `display_name` is "Copilot"),
  `LoginSpec.prompt_name` ("ChatGPT" for Codex), `CookieSpec.extra_auth_names`
  (Codex's `__Secure-oai-is`), `api_key_entry` and `api_key_placeholder`.
- **`PROVIDER_SPECS` is sorted by `default_order`,** so every derived table
  is in default order.
- **Provider classes are no longer imported by `app.py`.** Tests that
  substitute a provider class patch it on its own module
  (`aigauge.providers.claude.ClaudeProvider`), which the factory imports at
  call time.

## Adding a provider

Everything else (Settings nav row and page, enable checkbox, MCP account
ids and pause-policy rows, menu-bar label, provider order slot, tile family,
compact collapse, "Sign in" or "Add key" wording, default account in fresh
configs) is derived from the spec. `tests/test_provider_registry.py` fails
if a step below is missed where it can check.

### API key, several accounts (OpenCode, MiniMax)

1. A `ProviderSpec` in `providers/registry.py` with `auth="api_key"`,
   `multi_account=True`, a free `default_order`, `settings_hint`,
   `api_key_entry` (a keyring name not used by any other kind, containing
   `{account_id}`), `api_key_placeholder`, and a factory that returns
   `Cls(config, parent=parent, account_id=account.id)`.
2. A `ProviderToggles` field in `config.py` named after the spec id, with
   the spec's `default_enabled` as its default.
3. A provider module in `providers/` whose class takes
   `(config, parent=None, account_id=...)` and reads its key with
   `get_account_api_key("<id>", account_id)`.
4. A default-account migration in `Config._migrate`: bump
   `browser_accounts_version` and append `{"id": "<id>", "kind": "<id>"}`
   to an explicit account list that has no account of the kind, as the
   version-2 OpenCode step does.

### Browser session, several accounts (Claude, Codex, Langdock)

Steps 1 to 4 as above, with these differences in the spec and module:

1. The spec has `auth="browser_session"`, a `LoginSpec` (URL, window
   title, prompt name, the provider's own sign-in hosts), a `CookieSpec`
   (domain, and cookie names if stable), `usage_url`, `paste_cookie`, and
   `session_proof="page"` when there is no stable auth-cookie name. Its
   factory returns `Cls(parent=parent, account_id=account.id)`.
3. The provider module scrapes the usage page.

And two keyed tables:

5. A `VERIFY_TARGETS` entry in `webview/targets.py`: the signed-in page
   URL and its predicate.
6. A paste-cookie hint in `cookie_dialog.INSTRUCTIONS`.

If the sign-in flow uses an identity provider not yet allowed, add its host
to `_IDENTITY_PROVIDER_HOSTS` in `webview/login_window.py`.

### Single instance (Copilot, OpenRouter, Antigravity)

1. A spec with `auth="single"`, `multi_account=False` and a factory.
2. A `ProviderToggles` field.
3. A provider module.
4. A bespoke Settings page, built in `SettingsDialog.__init__` and added to
   `provider_pages` under the spec id. Settings fails to open without one.
5. Its own config section and credential helpers, and its colours in
   `gauge.thresholds_for_provider`.

No migration: a single-instance provider has no account rows.
