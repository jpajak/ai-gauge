# Plan: automatically start newly available Claude/Codex usage periods

**Status:** proposed (not started)  
**Author:** design note for a future change  
**Last researched:** 2026-07-10  
**Working name:** period starter

## Goal

Optionally send one deliberately tiny Claude or Codex request after a used usage
period resets, so a new on-demand countdown begins without the user having to
remember to send the first message manually.

The feature must:

- be off by default and explicitly enabled by the user;
- use the user's Claude/Codex **subscription allocation**, not a separately
  billed public API key;
- send at most one request for a given account/reset event;
- verify that the old period has actually reset before sending;
- do nothing if the user has already started the new period;
- never fall back silently to API billing or paid overflow credits;
- work asynchronously without freezing AI Gauge; and
- fail closed when authentication, account identity, or upstream state is
  uncertain.

The first implementation should target the default `claude` and `codex`
accounts only. Robust multi-account support is a separate phase because the
browser profiles AI Gauge scrapes and the authentication profiles used by the
vendor CLIs are not currently linked.

## Important terminology

This is not really an "API request" feature. The public Anthropic and OpenAI
APIs use separately billed API credentials and do not reliably affect the
subscription usage periods displayed by AI Gauge.

The supported route is to invoke each vendor's official non-interactive CLI:

- `claude -p ...`, authenticated with a Claude subscription; and
- `codex exec ...`, authenticated with ChatGPT.

The CLI will make the model request, but AI Gauge should treat it as a managed
child process rather than reproduce a private HTTP endpoint.

## Current behavior and where this fits

AI Gauge already has most of the observation side:

- `UsageMetric` carries `percent_used`, `resets_at`, `reset_label`, and
  `window` in `src/aigauge/models.py`.
- Claude and Codex convert an unused period without a meaningful countdown to
  `reset_label == "idle"` through `providers/idle.py`.
- `App._earliest_reset_refresh_time()` finds future resets for used metrics and
  schedules a scrape one minute after the earliest reset.
- The one-shot app timer currently calls `refresh_now(manual=False)` directly.
- `_on_snapshot()` records and renders the result, then schedules the next
  refresh.

The period starter should build on this state instead of adding an unrelated
wall-clock scheduler. The existing timer should wake the app, refresh the due
account first, and only then decide whether a starter request is appropriate.

## Product facts to re-check before implementation

Provider behavior changes frequently. Re-open the linked primary documentation
and perform the spike described below before relying on any of these details.

### Authentication and billing

- OpenAI documents two Codex login modes: ChatGPT sign-in for subscription
  access and API-key sign-in for usage-based access. `codex exec` reuses saved
  CLI authentication by default.
- Anthropic documents that Claude Code can use a Pro/Max subscription and that
  usage is shared with Claude's other product surfaces. Anthropic also warns
  that an `ANTHROPIC_API_KEY` environment variable takes precedence and causes
  API-billed usage.
- Both vendors bill their public API products separately from consumer
  subscriptions. A public Responses/Messages API call is therefore the wrong
  mechanism for this feature.

### Period semantics

- A five-hour/session period may be reported as idle until it receives its
  first use. AI Gauge already represents that state with `reset_label ==
  "idle"`.
- Codex may also omit the Session metric entirely while OpenAI exposes only
  the shared Weekly limit. Absence means unavailable, not idle: it must not
  register or trigger a starter event. If the Session metric later returns,
  normal event discovery resumes from fresh successful snapshots.
- Anthropic's current Pro documentation says the weekly reset is a fixed
  account-assigned day/time and does not move based on when the user starts
  using Claude. If the post-reset page already shows the next weekly reset,
  there is nothing to start and AI Gauge must not send a request merely because
  the old weekly boundary passed.
- The implementation should not assume that every provider, plan, or metric is
  on-demand. A request is justified only by live post-reset evidence that the
  relevant period is idle.

This live-state rule lets the implementation survive changes in weekly policy:
if the provider automatically advances the reset, mark the event satisfied; if
the provider explicitly reports an idle period whose countdown starts on next
use, it is eligible for activation.

## Recommended user experience

Add an opt-in setting for each supported default account:

> **Start new idle usage periods automatically**  
> After a used Claude/Codex period resets, AI Gauge can send one minimal
> subscription request to begin the next on-demand countdown. This consumes a
> small amount of usage. It requires the provider's command-line tool to be
> installed and signed in to the same subscription account.

The setting should include a non-consuming **Check CLI** action that reports:

- executable found/not found;
- CLI version;
- signed in/not signed in;
- subscription authentication confirmed/API authentication detected/unknown;
- whether the account mapping is safe enough to enable; and
- the executable path that will be used.

Do not offer a "send test request" button without a second, explicit warning:
testing would consume real usage and could start a period immediately.

For the first version:

- expose the option only for the default account IDs `claude` and `codex`;
- show it disabled for additional browser accounts with an explanation that
  per-account CLI authentication is not yet configured; and
- show the most recent starter outcome in Settings or a tooltip, for example
  `Last starter: Codex Session · succeeded Jul 10 12:16 AM`.

## Configuration shape

Prefer a small dedicated top-level configuration object rather than mixing
execution policy into `ProviderToggles`:

```python
class PeriodStarterConfig(BaseModel):
    enabled_accounts: list[str] = Field(default_factory=list)
    grace_seconds: int = Field(default=120, ge=60, le=900)
    command_timeout_seconds: int = Field(default=60, ge=15, le=180)

class Config(BaseModel):
    ...
    period_starter: PeriodStarterConfig = Field(
        default_factory=PeriodStarterConfig
    )
```

Only `enabled_accounts` needs a UI initially. Grace and timeout can remain
internal/config-file values until there is evidence users need to tune them.
Pydantic defaults make old configuration files migrate safely, and the default
empty list keeps the feature off.

An optional executable path override may eventually be useful because packaged
GUI applications do not always inherit the user's interactive shell `PATH`.
Do not add it until normal discovery has been tested on Windows, macOS, and
Linux. If added, store an absolute path and pass it directly to `QProcess`; do
not accept an arbitrary shell command string.

## Persistent event state

The app may be closed or the computer asleep at the reset boundary, so pending
events and deduplication cannot live only in memory.

Create a focused store, for example:

```text
src/aigauge/period_starter.py
<app-data>/period-starter-state.json
```

Suggested event fields:

```python
@dataclass
class ResetEvent:
    event_id: str
    account_id: str
    provider_kind: str
    metric_labels: list[str]
    observed_reset_epoch: int
    due_epoch: int
    status: str  # pending, verifying, running, satisfied, failed, expired
    verification_attempts: int = 0
    request_attempts: int = 0
    last_error: str | None = None
    completed_epoch: int | None = None
```

Build `event_id` from stable values, not the scrape time:

```text
sha256(account_id + sorted(metric labels) + observed reset epoch)
```

Using an epoch avoids daylight-saving ambiguity in the persisted scheduler even
though the existing provider parsers currently return naive local datetimes.
Convert the observed `resets_at` once, when registering the event.

Store writes should be atomic (temporary file followed by `replace`) and a
malformed state file should be logged and quarantined/ignored rather than
preventing the app from starting. Retain completed events long enough to stop
duplicates across restarts (30 days is ample), then prune them.

### Registering events

After every successful Claude/Codex snapshot:

1. Consider only accounts enabled for the period starter.
2. Consider only metrics with `percent_used > 0` and a future `resets_at`.
3. Register an event for `resets_at + grace` if the stable event ID is not
   already present.
4. Coalesce metrics for the same account whose reset times are close enough
   that one model request can satisfy both (for example, within five minutes).
5. Never register an event from an error, auth-required, stale, or idle metric.

This matches the current `_earliest_reset_refresh_time()` rule that an unused
metric resetting would not normally change visible state.

### Late events

On startup or resume:

- if a pending event is due but no more than 30 minutes late, verify it normally;
- if it is older than the allowed lateness, mark it expired and do not send;
- if the app was not running when no event had yet been learned, do not infer a
  request from wall-clock guesses; wait for a fresh trustworthy snapshot.

The maximum lateness prevents a laptop that was off for days from sending
surprise messages immediately after boot.

## State machine and scheduling flow

The critical rule is **refresh and verify first, then send**.

```text
used snapshot with future reset
            |
            v
     persist pending event
            |
       reset + grace
            |
            v
 refresh only the due account
            |
   +--------+-------------------+------------------+
   |                            |                  |
 error/auth/old state     active/new reset       idle
   |                            |                  |
 retry verification       mark satisfied    preflight CLI auth
 with a short cap                              |
                                      +--------+--------+
                                      |                 |
                                    valid             unsafe
                                      |                 |
                               run one request       mark failed
                                      |
                               refresh after delay
                                      |
                               mark completed/log
```

### Post-reset decision policy

Given an event learned from old reset time `R`, use the fresh snapshot as
follows:

1. **Snapshot error or auth required:** do not run a CLI. Retry verification
   after roughly five minutes, with a small cap (for example three verification
   attempts over 30 minutes), then fail/expire.
2. **Metric has non-zero usage and a reset later than `R`:** the user or another
   client already started the new period. Mark satisfied without sending.
3. **Metric is zero but has a credible reset later than `R`:** the provider
   automatically created the next fixed period. Mark satisfied without sending.
4. **Metric is explicitly idle (`reset_label == "idle"`):** it is eligible for
   one starter request.
5. **Metric still shows the old reset or appears internally inconsistent:**
   assume upstream reporting lag and retry verification; do not send.
6. **Metric is missing or only partially rendered:** do not send. This includes
   a successful weekly-only Codex snapshot while its Session card is
   unavailable; do not infer the old five-hour window from history.

When an event contains both Session and Weekly, one request is enough. When only
one is due, the request will naturally consume whatever shared allowance the
provider applies; do not send separate prompts per metric.

A weekly-only Codex snapshot is complete for the currently exposed layout, but
it cannot verify or activate a previously learned Session event. Let that event
follow the bounded verification/expiry policy rather than treating the missing
metric as idle.

The existing Codex builder treats an active session plus an idle weekly card as
a transient inconsistent render. Keep that conservative behavior. The starter
should not bypass a provider `ERROR` snapshot by interpreting `raw` page data
itself.

### Timer integration

Replace the timer's direct `refresh_now(manual=False)` callback with a method
such as `_on_refresh_timer()`:

```python
def _on_refresh_timer(self) -> None:
    due_accounts = self._period_starter.due_accounts(datetime.now())
    if due_accounts:
        self._pending_period_verification.update(due_accounts)
        self.refresh_accounts(due_accounts, manual=False)
    else:
        self.refresh_now(manual=False)
```

`_schedule_next_refresh()` should take the minimum of:

- the normal adaptive refresh time;
- the current reset-aware refresh time;
- stale-error recovery; and
- the earliest pending period-starter due time.

Avoid maintaining two independent `QTimer` instances unless integration with
the existing refresh queue proves unreasonably complex. One scheduler gives a
single source of truth and avoids a starter racing a normal scrape.

The current refresh queue accepts all providers or a single provider. A small
`refresh_accounts(iterable[str], manual=False)` helper would avoid pretending a
period verification is a user-requested/manual refresh.

After `_on_snapshot()` receives the fresh due-account result, ask the period
starter policy for one of `wait`, `satisfied`, or `run`. Do not start `QProcess`
until the provider has left `_inflight`, and keep normal refresh scheduling
paused while a starter request is running.

## CLI runner design

Use `QProcess`, not `subprocess.run`, so the UI and tray remain responsive.
Create a small runner abstraction that can be faked in tests:

```python
class PeriodStarterRunner(QObject):
    finished = pyqtSignal(str, bool, str)  # event_id, success, safe summary

    def preflight(self, provider_kind: str, account_id: str) -> PreflightResult:
        ...

    def start(self, event: ResetEvent) -> None:
        ...
```

Execution requirements:

- use `QProcess.setProgram()` plus `setArguments()`; never concatenate a shell
  command;
- use an empty app-owned working directory so no repository content,
  `AGENTS.md`, `CLAUDE.md`, or source files become prompt context;
- set a hard timeout (initially 60 seconds), terminate, then kill if needed;
- capture bounded stdout/stderr in memory for result classification;
- never log complete CLI output or authentication details;
- never persist the model response;
- allow only one model-request attempt per event; and
- refresh the provider about 30-60 seconds after success so the new reset can
  appear in AI Gauge.

### Environment

Build a child `QProcessEnvironment` from the system environment and explicitly
remove:

```text
ANTHROPIC_API_KEY
OPENAI_API_KEY
CODEX_API_KEY
```

Also reject the run during preflight if any of these variables would otherwise
select API billing. Sanitizing variables is defense in depth, not proof of
subscription login: the CLI itself may have been persistently logged in with an
API key, so authentication status must also be checked.

Do not read, copy, parse, or log CLI token files. Let the official CLI own token
refresh and secure storage.

### Authentication preflight

Claude:

```text
claude auth status --json
```

Parse only documented/stable fields that distinguish subscription login from
API/Console login. If the installed CLI's schema is unknown, report `unknown`
and fail closed until the parser is updated.

Codex:

```text
codex login status
```

Require a successful status that identifies ChatGPT authentication. An API-key
login is unsafe for this feature. If output is not machine-readable or changes
across versions/locales, version-gate known formats and fail closed for unknown
ones.

Preflight should run when the user enables the feature and again shortly before
the real request. Cached status is not sufficient indefinitely because users
can log out or switch accounts outside AI Gauge.

Authentication mode alone cannot prove that the CLI account matches AI Gauge's
browser profile. For the default-account first version, require the user to
confirm the mapping in Settings. Do not enable additional AI Gauge accounts
until there is a real per-account CLI credential/profile design.

## Minimal command candidates

Exact flags must be validated against the minimum supported CLI versions during
the spike. Keep the argv builders isolated and unit-tested so compatibility
changes do not leak into scheduling code.

### Claude

Candidate invocation:

```text
claude -p
  --output-format json
  --no-session-persistence
  --tools ""
  --strict-mcp-config
  --max-turns 1
  --effort low
  --system-prompt "Reply with exactly OK."
  "Reply now."
```

Possible addition after testing:

```text
--model haiku
```

Haiku should reduce consumption if it is available and affects the same target
period, but availability and model-specific limits can differ by plan. Do not
hard-code it until a real subscription test confirms that the desired Session
and Weekly cards advance. Omitting `--model` is more compatible but may consume
slightly more allocation.

Newer Claude versions document a `--bare` mode. It is attractive because it
skips hooks, skills, plugins, MCP, memory, and project instructions, but it was
not present in every installed version observed during planning. Use it only
behind a version/capability check. The explicit flags and empty working
directory remain necessary safeguards.

### Codex

Candidate invocation:

```text
codex exec
  --json
  --ephemeral
  --ignore-user-config
  --ignore-rules
  --sandbox read-only
  --skip-git-repo-check
  -C <empty-app-owned-directory>
  "Reply with exactly OK. Do not inspect files or call tools."
```

`--ignore-user-config` avoids user MCP servers, hooks, and other configuration
affecting this special-purpose request while retaining the normal Codex auth
home. `--ephemeral` avoids writing a session rollout. The empty directory plus
read-only sandbox makes accidental repository access unlikely.

Do not hard-code a Codex model name: subscription model availability changes.
An optional low reasoning-effort config override can be added only after its
key and behavior are verified across supported CLI versions.

For JSON output, treat a completed turn and process exit code zero as success.
Do not require the text to equal `OK` exactly; harmless model formatting drift
should not cause a second charged request.

## Failure and retry policy

Separate **verification retries** (scraping, no model usage) from **request
retries** (real model usage):

- Scrape verification may retry a few times while the provider commits a reset.
- Authentication/preflight failure sends no request and remains failed until a
  later reset event or explicit user action; do not repeatedly launch status
  commands every minute.
- Once the model child process is started, count the request attempt even if the
  process times out or its output cannot be parsed. The server may have accepted
  the prompt, so automatic retry could duplicate usage.
- After an ambiguous request failure, refresh the usage page once. If it now
  shows a started period, mark success; otherwise mark the event failed and let
  the user decide.
- Never send more than one model request for the same event ID.

Safe user-facing failure examples:

- `Claude CLI not found`
- `Claude CLI is signed in for API billing; subscription login required`
- `Codex CLI authentication could not be verified`
- `Usage page did not confirm the reset`
- `Starter request timed out; not retried to avoid duplicate usage`

## Billing and safety guardrails

Even a tiny request is real usage. The following are release blockers:

1. Feature defaults to disabled.
2. A post-reset scrape explicitly confirms idle state.
3. Grace period accounts for server clock/reporting lag.
4. Subscription authentication is confirmed immediately before execution.
5. API-key environment variables are rejected/removed.
6. No automatic switch to API billing, flexible credits, or usage credits.
7. One request maximum per persisted event ID.
8. No retry after an ambiguous request submission.
9. Fixed prompt; no user/project/provider data included.
10. No private web tokens or undocumented endpoints.

The user-facing copy should still acknowledge a residual risk: provider-side
reporting can lag, billing behavior can change, and a minimal request may consume
paid overflow if the provider itself applies credits automatically. The verify
first flow and grace period reduce this risk but cannot prove billing behavior
inside the provider.

## Multi-account limitation and future design

AI Gauge currently maintains one persistent Qt WebEngine profile per browser
account ID. The Claude and Codex CLIs normally maintain one active login in
their own global credential homes. Those identities are independent.

Therefore, browser account `codex-work` cannot safely invoke the globally logged
in Codex CLI and assume it is the work account. The same is true for Claude.

Potential future approaches:

- configure a distinct `CODEX_HOME` per Codex account and authenticate each
  explicitly;
- investigate whether Claude officially supports separate credential/config
  homes suitable for this use case;
- store a per-account absolute CLI path plus credential-home mapping; and
- present the CLI-reported account identity next to the AI Gauge account name
  for explicit confirmation.

Do not copy browser cookies into CLI credential files, and do not share one
CLI login across several enabled AI Gauge accounts. Until official per-account
profiles are established, keep v1 limited to one default account per provider.

## Alternatives considered

### Public model APIs

Rejected. Anthropic Console/API and OpenAI Platform API billing are separate
from the subscriptions whose limits AI Gauge displays. They could incur charges
without starting the desired period.

### Undocumented Claude/ChatGPT web endpoints

Rejected. This would require reverse-engineering private request shapes and
using sensitive browser session credentials outside their intended flow. It is
brittle, difficult to secure, and likely to break without notice.

### Automating the web chat UI

Not recommended. It would be slower, more layout-dependent than the existing
read-only scraper, and likely create visible junk conversations. It also makes
submission success difficult to distinguish from UI/reporting lag.

### External OS scheduled tasks

Not preferred. They would duplicate AI Gauge's reset knowledge and account
configuration and would have poorer deduplication and visibility. Keeping the
feature in the app lets it verify live usage state before acting.

## Implementation phases

### Phase 0: disposable manual spike

Before changing app behavior:

- confirm both CLIs are installed and subscription-authenticated on a test
  account;
- wait for a naturally due, previously used session;
- capture the usage page immediately after reset;
- run the candidate minimal command from an empty directory;
- refresh the usage page and confirm which Session/Weekly cards and reset times
  changed;
- measure process duration and approximate usage delta;
- repeat with API-key environment variables present to ensure preflight refuses
  rather than charges;
- check Claude fixed-weekly behavior rather than assuming it needs activation;
  and
- document minimum working CLI versions and actual auth-status output shapes in
  tests/fixtures with personal data removed.

Do not automate until this spike proves that non-interactive subscription usage
affects the exact metrics AI Gauge scrapes.

### Phase 1: pure policy and persistence

- Add `PeriodStarterConfig` with disabled defaults.
- Implement the event store, event ID, pruning, and atomic persistence.
- Implement pure functions for registration, coalescing, due selection, and
  post-reset snapshot decisions.
- Add exhaustive unit tests without launching any real process.

### Phase 2: asynchronous CLI runner

- Implement executable discovery and version capture.
- Implement auth preflight parsers, environment sanitization, argv builders,
  empty working directory, timeout, and bounded output.
- Wrap `QProcess` behind a fakeable interface.
- Do not expose UI enablement until the runner fails closed for every unknown
  authentication state.

### Phase 3: app scheduler integration

- Teach the single app timer about pending due events.
- Add targeted automatic refresh without marking it manual.
- Evaluate the fresh snapshot and invoke the runner only for explicit idle
  state.
- Refresh once after success or ambiguous completion.
- Add lifecycle logs and last-outcome display.

### Phase 4: settings and release

- Add opt-in controls and non-consuming Check CLI action.
- Add warnings and default-account restriction.
- Run the manual platform matrix.
- Add README/CHANGELOG documentation.
- Release behind conservative defaults; consider an additional experimental
  label for the first version.

### Phase 5: optional multi-account support

- Design and validate separate official CLI credential homes.
- Add explicit identity mapping and per-account preflight.
- Only then allow non-default accounts to enable the feature.

## Test plan

No automated test may invoke a real Claude/Codex model request.

### Pure unit tests

- active metric with future reset registers exactly one event;
- zero/idle, missing-reset, error, and auth-required snapshots do not register;
- repeated snapshots deduplicate the same reset;
- nearby Session/Weekly resets coalesce to one event;
- different accounts never coalesce;
- events survive store reload/restart;
- atomic store recovery handles malformed JSON;
- due event within lateness window verifies;
- stale event expires without a request;
- fresh non-zero post-reset metric marks satisfied;
- fresh zero metric with advanced fixed reset marks satisfied;
- explicit idle metric returns `run`;
- old/inconsistent/missing metric returns `wait` or `fail`, never `run`;
- completed/failed request event can never return `run` again;
- event pruning retains the deduplication window.

### Command/preflight tests

- argv is a list and contains no shell interpolation;
- working directory is empty/app-owned;
- Claude argv disables tools, persistence, and MCP and limits turns;
- Codex argv is ephemeral, ignores config/rules, and is read-only;
- output is bounded and redacted in logs;
- all three API-key variables are removed from the child environment;
- Claude subscription status accepted; API/unknown status rejected;
- Codex ChatGPT status accepted; API/unknown status rejected;
- missing/old/unsupported executable fails without starting a model process;
- timeout counts as an attempt and never triggers an automatic second request;
- ambiguous completion schedules one scrape verification rather than a retry.

### App/Qt tests

- due event pulls the timer forward;
- timer refreshes only the due account before activation;
- normal/manual refreshes still behave as before;
- starter waits until the scrape leaves `_inflight`;
- a manual refresh during starter execution is queued safely;
- app shutdown terminates or safely detaches the child process;
- UI remains responsive during a fake long-running `QProcess`;
- settings round-trip and migration leave feature disabled by default;
- additional accounts cannot enable v1 accidentally.

### Manual verification matrix

- Windows, macOS, Linux packaged builds where supported;
- CLI absent, present but logged out, subscription login, API login;
- API-key environment variable present;
- Session-only due, Weekly-only due, nearly simultaneous reset;
- user sends a message between reset and starter verification;
- app restart before reset, after reset, and while an event is pending;
- sleep/suspend across reset;
- network offline and provider usage-page layout error;
- CLI timeout and app exit during execution;
- provider paid credits/overflow disabled and enabled, with behavior documented;
- default and extra browser accounts.

## Logging and diagnostics

Add concise structured log events without prompts, model output, emails, or
tokens:

```text
period event registered account=codex labels=session due=... event=abcd1234
period verification account=codex event=abcd1234 decision=idle
period preflight account=codex auth=chatgpt cli_version=...
period request started account=codex event=abcd1234
period request finished account=codex event=abcd1234 exit=0 elapsed_ms=...
period request skipped account=codex event=abcd1234 reason=already_active
```

Hash/truncate event IDs in logs. Log executable paths only at debug level.
Never log auth status payloads wholesale because they may contain user identity
or organization information.

## Acceptance criteria

The first release is complete when:

- the feature is disabled on upgrade and fresh install;
- enabling requires a confirmed subscription-authenticated CLI;
- a used, on-demand period that becomes explicitly idle receives exactly one
  minimal request after reset and grace;
- a fixed period, already-active period, uncertain snapshot, or unsafe auth mode
  receives no request;
- no event can submit twice across refreshes, crashes, or restarts;
- the UI remains responsive and shows a safe outcome;
- no project data is sent and no model output/auth data is retained;
- automated tests never contact a model; and
- manual tests confirm that the desired AI Gauge metric receives a new reset
  time on both supported providers where that metric is genuinely on-demand.

## Rough effort

- **Single default account per provider:** small-to-medium, roughly 1-2 focused
  engineering days after the manual spike. The largest pieces are conservative
  state/deduplication, async process handling, and tests—not the prompt itself.
- **Polished cross-platform release:** allow additional time for packaged-app
  executable discovery and real reset-boundary testing, which cannot be sped up
  entirely with unit tests.
- **Robust multi-account support:** likely another 2-4 days plus provider-specific
  authentication research because it needs separate official CLI profiles and
  an identity-mapping UX.

## Open questions for the implementation spike

- Does each current provider/plan actually show `idle` after Session reset, and
  how long does that state take to appear?
- Which Codex metrics move after a tiny local `codex exec` turn?
- Does Claude Haiku start the same Session and all-model Weekly counters shown by
  the scraper on every relevant plan?
- Is Claude Weekly ever genuinely on-demand now, or always fixed as current
  documentation says?
- What stable fields does `claude auth status --json` expose across supported
  versions?
- Can `codex login status` be parsed safely across versions and locales, or is a
  machine-readable status option available by implementation time?
- What minimum CLI versions include every isolation flag we want?
- How reliably can packaged GUI builds discover CLIs installed through npm,
  native installers, IDE extensions, Homebrew, or user-local paths?
- Can provider-side paid overflow be disabled/detected sufficiently to make the
  preflight guarantee understandable?
- Is a two-minute grace sufficient, or do real usage pages need longer?

## Implementation checklist

- [ ] Re-check primary documentation and complete the disposable manual spike.
- [ ] Record sanitized CLI versions/auth-status fixture shapes.
- [ ] Add disabled-by-default `PeriodStarterConfig` and settings migration tests.
- [ ] Add persistent event store with atomic writes, pruning, and stable IDs.
- [ ] Add registration/coalescing/due/decision policy with pure tests.
- [ ] Add executable discovery and supported-version checks.
- [ ] Add subscription-only auth preflight and API-key environment rejection.
- [ ] Add fixed minimal argv builders and empty working directory.
- [ ] Add async `QProcess` runner, timeout, bounded output, and no-retry rule.
- [ ] Integrate pending event due times into the existing single timer.
- [ ] Add targeted verify-first refresh and post-request refresh.
- [ ] Add Settings opt-in, Check CLI, warnings, and last outcome.
- [ ] Restrict v1 to default Claude/Codex accounts.
- [ ] Add lifecycle diagnostics with redaction.
- [ ] Complete unit/Qt/manual test matrices.
- [ ] Update README, CHANGELOG, SECURITY/privacy notes if appropriate.

## Primary references

- OpenAI, [Codex authentication](https://developers.openai.com/codex/auth) —
  ChatGPT subscription auth versus API-key usage-based auth, cached CLI login,
  and `codex login status`.
- OpenAI, [Codex non-interactive mode](https://developers.openai.com/codex/noninteractive)
  — `codex exec`, saved authentication, `--ephemeral`, JSON output, and
  automation safety.
- OpenAI, [ChatGPT and API billing are separate](https://help.openai.com/en/articles/8156019-is-api-usage-included-in-chatgpt-subscriptions-even-if-i-have-a-paid-chatgpt-account)
  — why a normal Platform API request is not the subscription starter.
- Anthropic, [Use Claude Code with a Pro or Max plan](https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan)
  — subscription login, shared usage, API-key precedence, and separate API
  credits.
- Anthropic, [Claude Code CLI reference](https://code.claude.com/docs/en/cli-usage)
  — print mode, turn/tool/persistence controls, model selection, and auth status.
- Anthropic, [Claude Pro usage limits](https://support.claude.com/en/articles/8325606-what-is-the-pro-plan)
  — five-hour sessions and the current fixed account-assigned weekly reset.
