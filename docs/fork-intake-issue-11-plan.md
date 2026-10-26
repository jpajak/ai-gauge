# Fork intake from issue #11: plan

Assessed 2026-10-08 against `main` at `1075ca8` (0.8.5).

[Issue #11](https://github.com/jpajak/ai-gauge/issues/11) offers the changes in
DraftingDreamer's fork for reuse. That fork also carries two commits from a
second fork, innoscoutpro's. This plan records what to take for the next
release, what is deferred and on what trigger, what is not coming upstream, and
how to credit it.

## Sources

| Ref | Repo | Head | Ahead of base |
|---|---|---|---|
| `dd/main` | DraftingDreamer/ai-gauge | `17d8282` | 26 commits, about 7,500 lines |
| `inno/main` | innoscoutpro/ai-gauge | `5417cbd` | 4 commits, about 310 lines |

Both branch from `b34dcf8`. Both refs are fetched into this clone without a
configured remote. To refresh them:

```
git fetch --no-tags https://github.com/DraftingDreamer/ai-gauge.git "+refs/heads/*:refs/remotes/dd/*"
git fetch --no-tags https://github.com/innoscoutpro/ai-gauge.git "+refs/heads/*:refs/remotes/inno/*"
```

Anchors below are `path:line` at `main` unless prefixed `dd:` or `inno:`.

## Status at the end of 2026-10-08

Everything below is on branch `feature/antigravity-english-profile`, version
0.8.5, full suite passing (753). Nothing is merged to `main`, pushed or tagged.
This table is current; the Summary table after it records the first assessment.

| Item | Status |
|---|---|
| N1 Claude sign-in, snap Brave | Done: three cherry-picks by innoscoutpro plus the test fix |
| N2 Codex hardening | Done: JS-side layout flags, `% left` readiness, `aria-pressed`, weekly stop label |
| N3 CI hardening | Done for permissions, exit codes and a manual trigger on `test.yml` |
| N4 Test portability | Done, with a remainder (below) |
| N5 Codex in other languages | Done: English `Accept-Language`, and the progress-bar fallback |
| D1 Antigravity | Done, checked against a real free-plan `agy` |
| D6 Settings side list | Done |
| D2 resets, D4 build isolation and GUI smoke, D5 READMEs | Parked; triggers not fired |

**Owed before tagging.**

- Run the app from the branch: the Antigravity tile, the Settings dialog at
  other UI scales, and the Codex and Claude tiles. None of these was looked at
  in the running app.
- Push the branch and run the `test` workflow by hand. macOS, Ubuntu and
  Python 3.11 have not run any of this. The workflow changes themselves cannot
  be exercised before a push, and the release job's narrowed token is first
  proven by the next tag.
- Post the reply on #11.

**Left open, each with its trigger.**

- *Release workflow manual trigger.* Not taken: it rewrites how `release.yml`
  finds the version and cannot be tried before a tag. **Trigger:** wanting a
  packaging run without tagging.
- *Time zones east of UTC+9.* With `TZ` set (it works on Windows), three tests
  in `tests/test_local_usage_usage_tab.py` and
  `tests/test_local_usage_trend_tab.py` still fail from nine hours east onward:
  `test_unpriced_models_use_clear_names_and_keep_total_readable` (+9, +10),
  `test_range_selector_defaults_to_this_week_and_covers_all_ranges` (+11),
  `test_marking_a_limit_change_keeps_and_compares_older_data` (+12 to +14).
  Not investigated. **Trigger:** a contributor in those zones.
- *Codex fallback reach.* It fires only when no tab is labelled "Overview",
  which is the translated-page case. On an English page whose limit wording
  changes, the extractor keeps waiting and ends on "extractor retry limit
  exceeded" as before. Sign-in verification (`src/aigauge/webview/targets.py`,
  `VERIFY_TARGETS["codex"]`) is still label-only. **Trigger:** a report from a
  user whose page stays non-English despite the header, or the next ChatGPT
  wording change.
- *Antigravity page gap.* A tall gap under the status line; the form rows do
  not honour height-for-width. Cosmetic. **Trigger:** the next change to that
  page.
- *Antigravity on a paid plan.* The 5-hour rows rest on the fork's synthetic
  fixture, and the tile has no compact collapse. **Trigger:** a paid-plan
  reply from DraftingDreamer.

**Live page facts gathered 2026-10-08 (Pro plan, English).** The Overview page
has exactly two `<progress>` elements, with `max="100"` and `value` as
attributes. The Credits and "Usage limit resets" sections add none. On the
Analytics tab the same two stay in the DOM, hidden, with current values, and
no others appear.

## Summary

| Item | Decision | Size |
|---|---|---|
| N1. Claude sign-in detection and snap Brave (innoscoutpro) | Next release | 3 cherry-picks + 1 commit |
| N2. Codex weekly-only acceptance bug in our own `1075ca8` | Next release | 1 to 2 commits |
| N3. CI permission tightening and exit-code checks | Next release | 1 commit |
| N4. Timezone-fragile tests and harness encoding | Next release, optional | 1 commit |
| D1. Antigravity provider | Building now as a hand port (decided 2026-10-08) | about 5 commits |
| D2. Reset announcements and Windows toasts | Deferred, lean towards leaving in the fork | 3 commits + 1 optional |
| N5. Codex page in other languages (replaces the fork's zh-TW labels) | Next release candidate, no capture needed | 2 commits |
| D4. Build PATH isolation and packaged-GUI smoke test | Deferred | 2 commits |
| D5. Translated READMEs | Deferred | re-translation |
| D6. Settings dialog layout (tabs to a side list) | Deferred until Antigravity merges | 1 to 2 commits |

Nothing from `dd/main` can come in by cherry-pick: its feature commits are
interleaved (Antigravity and reset code share commits in `app.py`, `widget.py`,
`config.py`, `settings_dialog.py`) and its release commit `b63b854` conflicts
with `1075ca8` in at least 11 files.

## Next release

### N1. Claude sign-in detection and snap Brave

**Problem.** When Claude redirects the usage route to `/login` or `/logout`, or
serves the "Sign in - Claude" page, the scrape retries and ends on "page failed
to load" or "extractor retry limit exceeded". The user is never told to sign in
again. Separately, Brave installed as a snap is not detected, and snap
confinement cannot write the temporary sign-in profile under the app's hidden
config directory.

**Change.** Three commits by innoscoutpro, taken from the original author's
branch:

```
git cherry-pick -x b91170a a45e5f0 e097d92
```

- `b91170a` adds `_claude_auth_redirect` to `src/aigauge/webview/scraper.py`
  and uses it when the load fails or the extractor asks for a retry.
- `a45e5f0` extends `isLoggedOut` in `src/aigauge/providers/claude.py:193`.
- `e097d92` adds the `brave` launcher and `_browser_profile_root` to
  `src/aigauge/webview/external_login.py`. The cleanup guard now checks the
  profile against the root that was actually used.

**Follow-up commit.** `test_snap_brave_profile_uses_snap_writable_directory` in
`tests/test_external_login.py` fails on Windows and macOS because it never
patches the platform. Add `monkeypatch` to its arguments and
`monkeypatch.setattr(external_login.sys, "platform", "linux")` as its first
line.

**Verified.** Applied in a scratch worktree on `1075ca8`: the three picks apply
in order with no conflict, and with the test fix the full suite passes (591
passed, Windows, `QT_QPA_PLATFORM=offscreen`). The snap path itself was not
exercised on Linux.

### N2. Codex weekly-only acceptance

**Problem.** A plan with a weekly limit and no 5-hour limit can be rejected
permanently with "Codex usage page only rendered part of the usage cards;
retrying." The extractor reads the weekly card from the full page text, but the
Python acceptance check `_is_weekly_only_usage_layout`
(`src/aigauge/providers/codex.py:268`, used at `:361`) looks at `body_text`,
which the extractor truncates to 2,000 characters (`codex.py:92`, `:111`). It
also requires the literal subtitle "shared across". Dual-limit plans never
reach this check, which is why it does not show on a dual-limit account.

Confirmed by running these inputs against `main`:

| Input | Result on `main` |
|---|---|
| Weekly-only Overview body with about 2,600 characters of other page text before "Plan limits", through the Node harness | Extractor returns weekly 96; `_build_snapshot` returns ERROR |
| `Usage Overview Analytics Plan limits Weekly limit Resets in 12h 15m 75% left` (no "Shared across"), Python only | ERROR. Source: `inno:tests/test_codex.py:252`, also `:267` |
| Analytics body `Usage Overview Analytics Usage history Plan usage history By feature Weekly limits Period % of limit used Sep 26 – Oct 3 Usage as of Oct 2, 12:00 AM UTC 58.7%` with the Analytics tab selected, through the Node harness | Overview is not clicked and no retry is requested. Source of the body: `inno:tests/test_codex.py:280` |
| Same long page on a dual-limit plan (control) | OK |

**Exposure, measured on the live page 2026-10-08 (Pro plan, English).** The
whole settings page is 1,046 characters of text and "Plan limits" starts at
342, so the first row is latent, not active: it would need the page to grow
past 2,000 characters before the limits. The second row only bites if OpenAI
rewords the subtitle. The third only bites if the page opens on Analytics
despite `?tab=overview`. With `EXTRACTOR_JS` from `main` run against that page,
both cards read correctly (5-hour 100% left, "5h 0m"; weekly 99% left,
"5d 22h"). A weekly-only page could not be seen from a Pro account; the only
real weekly-only sample is innoscoutpro's (`inno:tests/test_codex.py:227`),
which passes on `main`.

**Recommended approach.**

1. Have `EXTRACTOR_JS` report what it saw in the full limits text, for example
   `has_plan_limits` and `has_five_hour_heading`, and use those in place of the
   `body_text` and "shared across" test. Keep the existing rule that a 5-hour
   heading without data means the page is still loading. Keep the `body_text`
   path as the fallback for payloads that carry no flags.
2. Tighten the readiness test at `codex.py:46` from "any percent" to
   `%\s*left`, matching what `src/aigauge/webview/targets.py` already uses.
3. Give the weekly card a stop label of `5-hour limit` at `codex.py:106` and
   `:329`, so a weekly heading rendered before an unloaded weekly value cannot
   borrow the session's percent. Test to adapt:
   `dd:tests/test_codex_extractor_js.py:381`.

**Alternatives rejected.** Raising the 2,000 cap still guesses at page length,
and `body_text` goes into diagnostics, so a larger cap puts more page text in
logs. Dropping only the "shared across" requirement fixes the second row and
not the first. Replacing the parser with DraftingDreamer's `_codex_page.py` is
out: by trace it does not read the 5-hour limit on the real Overview page
(`dd:src/aigauge/providers/_codex_page.py:152`), and its English Overview
wording ("5 days 10 hours until reset", "94% remaining") matches neither our
capture nor innoscoutpro's.

**Risk.** Accepting a weekly-only result while the 5-hour card is still
loading. The flag must mean "the 5-hour heading is absent from the full limits
text", not "the 5-hour value is absent".

**Tests.** The four rows above as regression tests (three in
`tests/test_codex_extractor_js.py`, one in `tests/test_codex.py`), plus
`dd:tests/test_codex.py:477` for the JS-side flag.

**Selected tab, from the live page.** Overview and Analytics are plain
`<button>` elements with `aria-pressed="true"` or `"false"`. They carry no
`aria-selected`, no `role` and no `data-state`. `main` recognises the selected
one only because its class list happens to contain the word "active" or
"selected" (`codex.py:58`). Add `aria-pressed === 'true'` to the test at
`codex.py:55-58` so a styling change cannot make the extractor re-click
Overview on every retry. DraftingDreamer already does this and guards with one
click per document (`dd:src/aigauge/providers/_codex_page.py:50-67`).

### N3. CI hardening

All three are present on `main` and are small hand edits. Anchors on the fork
side show the target state.

| Change | `main` today | Target |
|---|---|---|
| Workflow-level `contents: read`; `contents: write` only on the `release` job | `.github/workflows/release.yml:8-9` gives the write token to the build matrix, which runs pip, pytest and PyInstaller | `dd:.github/workflows/release.yml:9-10`, `:219-220` |
| `permissions: contents: read` on the test workflow | `.github/workflows/test.yml` has no permissions block | `dd:.github/workflows/test.yml:10-11` |
| `if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }` after each native command in the Windows test step | `.github/workflows/release.yml:100-101`: only the last command's exit code fails the step | `dd:.github/workflows/release.yml:119`, `:121` |

Optional in the same commit: `workflow_dispatch` on both workflows with the
release job gated to tag pushes (`dd:.github/workflows/release.yml:7`,
`:20-43`, `:217`; `dd:.github/workflows/test.yml:4`). Drop the duplicate check
step at `dd:.github/workflows/release.yml:39-43`. On dispatch it can publish
workflow artifacts only.

Not for this commit: the proxy environment on pytest steps (the fork needed
`5f57fdc` to fix duplicate keys it introduced itself) and `actions/setup-node`.

No supply-chain concern was found in the fork's CI: no new secrets, nothing
that downloads and executes remote content, permissions only tightened. Every
action is pinned by tag, not SHA, on both sides.

### N4. Test portability (optional)

None of these fail on `main` in CI or in a US timezone.

- `tests/test_local_usage_trend_tab.py`: weekly fixtures use 5-hour windows on
  a UTC date and break at UTC+5 and east, and between UTC-6 and UTC-9. Take the
  file from `dd/main` (`dd:tests/test_local_usage_trend_tab.py:56-63`,
  `:192-198`).
- `tests/test_local_usage_usage_tab.py`: `NOW` at 18:00 UTC makes "Today" empty
  at UTC+6 and east. Take the file from `dd/main` (`:39`, `:68`).
- Add `encoding="utf-8"` to the Node harness `subprocess.run` in
  `tests/test_codex_extractor_js.py:46`, `tests/test_claude_extractor_js.py:80`
  and `tests/test_login_window.py:41`. Latent today because the fixtures are
  ASCII; N2 adds an en dash.
- `tests/conftest.py`: have `isolated_appdata` also redirect `LOCALAPPDATA`,
  `HOME`, `USERPROFILE` and `XDG_CONFIG_HOME` (`dd:tests/conftest.py:31-36`).
  Take that hunk only; the rest of the fork's conftest imports modules that do
  not exist on `main`.

`main` has not touched the two local-usage test files since `b34dcf8`. The
timezone failures were derived from the code, not reproduced.

## Deferred

### D1. Antigravity provider

**What it is.** A provider that runs the local `agy` CLI
(`agy --print /quota --output-format json --mode plan --sandbox
--print-timeout 30s`, `dd:src/aigauge/providers/antigravity.py:65-77`) and
shows Gemini and Claude+GPT 5-hour and weekly quotas on one tile. Off by
default (`dd:src/aigauge/config.py:118`), no shell, fixed arguments, follows
the `Provider` pattern.

**Trigger to start.** A user asks for it upstream, or John starts using
Antigravity, and a real `agy` reply is available as a fixture. The fork's
fixture is synthetic and no test runs a real `agy`.

**Getting a real reply without a paid plan.** `agy` is not installed on the dev
machine (checked 2026-10-08). Third-party pricing pages describe a $0
individual Antigravity tier that includes the CLI, with a weekly quota only.
If that holds, a free account gives a real capture of the envelope and the
weekly rows, but not the 5-hour rows that paid plans have. Those still need a
capture from a paying user such as DraftingDreamer. This was not verified
against Google's own pages.

**Status: shipped in 0.8.5, built on branch `feature/antigravity-english-profile`
and merged to `main`.** Nine commits (three cherry-picks authored by
DraftingDreamer, six hand commits with a co-author trailer). Full suite passed
(668). The required changes below are all in, except the error-label rewrite
and the two-row collapse, which were left out on purpose. Still open after the
release (each is listed below as it stood on 2026-10-08):

- Done 2026-10-08: a run against the real `agy` on a free account. The
  provider's own call returned in 3.6 s with `num_turns` 0 and `total_tokens`
  0 (no quota spent), and gave two rows, "Gemini weekly" and "Claude+GPT
  weekly". Real bucket ids are `gemini-weekly` and `3p-weekly`, as the fork
  assumed. That reply is now `tests/fixtures/antigravity/quota_free_weekly.json`
  with a test.
- A look at the tile itself in the running app. The run above exercised the
  provider, not the widget.
- A real paid-plan reply. The 5-hour rows still rest on the synthetic fixture.
- The process-tree kill on macOS and Linux, covered by a fake only. On Windows
  it was run once against a stand-in tree and returned in 3.3 s.
- CI on macOS, Ubuntu and Python 3.11; the tests ran on Windows 3.12 only.
- Done 2026-10-08: changelog entry and version. Cut as 0.8.6, then folded
  into 0.8.5 the same day, because 0.8.5 itself had not been published. A
  patch number was chosen because the provider is opt-in (0.7.5 added the MCP
  guard the same way) and because the fork has already published its own
  "0.9.0".

Known limits: the tile has no compact collapse (it behaves like Copilot's), the
widget's minimum width grows from 240 to 280 px while a four-row tile is shown,
and an unkillable `agy` tree would leak one process per refresh.

**Decision 2026-10-08: build it now as a hand port.** John will test with a
free account. Choices made for the port, beyond the required changes below:

- Settings stay in the fork's own "Antigravity" tab. The tab layout as a whole
  is D6.
- The two-row collapsed view from `ce57a38` is left out. Antigravity uses the
  same collapsed rendering as other providers. **Trigger to revisit:** a
  paid-plan user (four rows) finds the collapsed tile unusable.
- The `_short_error_reason` rewrite from `72196a6` is left out; `main`'s labels
  stay.
- A free account has a weekly quota only, so weekly-only and single-group
  replies get their own parser tests.
- A real `agy` reply is still owed as a fixture. John captures one from the
  free plan; the 5-hour rows need a paid-plan capture.

**Original recommendation, superseded.** Ask DraftingDreamer for a PR rebased
on `main` that contains only this feature, with the changes below. They keep
authorship and CI runs on it. A hand port is about 5 commits: cherry-pick `949553d`, `12a74f4`,
`f21426d` (these apply cleanly), then one commit taking `antigravity.py` at
`dd/main` with the editable path and tests, then one for the collapsed view.
Do not release the three picks alone: without the later `1fc8742` hunk, an
offline reply latches polling off until restart.

**Changes required.**

- The "refuse non-agy paths" check compares only the filename stem
  (`dd:src/aigauge/providers/antigravity.py:286`) and does not apply to the
  auto-detected path. Describe it as a typo guard in the settings hint and
  README, and add a test for a different binary named `agy.exe`.
- On Windows `subprocess.run` kills only the direct child on timeout and then
  waits on the pipes. Refresh is serial (`dd:src/aigauge/app.py:675-682`) with
  no watchdog, so a hung `agy` could stall every provider. Use `Popen` and kill
  the process tree. This was inferred, not reproduced.
- The settings path field calls `is_file()` on every keystroke on the GUI
  thread (`dd:src/aigauge/settings_dialog.py:1729-1750`). Check on
  editing-finished.
- `72196a6` rewrites `_short_error_reason` (`dd:src/aigauge/widget.py:366-396`)
  for all providers ("timeout · stale", "offline · stale"). Decide that
  separately.
- Decide whether to keep the two-row collapsed layout, which adds about eight
  `provider == "antigravity"` branches in `widget.py` (`:1119`, `:1248`,
  `:1275`, `:1298`, `:1306`, `:1322`, `:1341-1374`), or use the generic compact
  row.

**Risk.** Total dependence on an undocumented CLI output format. A structural
change fails closed with a named error. If `/quota` stops being a built-in, one
prompt is spent per app start before the latch stops polling.

### D2. Reset announcements and Windows toasts

**What it is.** Two optional tiles fed by `https://codex-resets.com/api/v1/status`
and `https://claude-resets.com/api/resets`, with tray notifications and native
Windows toasts. Both feeds are unofficial third-party trackers, and the fork
says so in settings and the README. The Codex tracker classifies one X
account's posts with AI. Off by default; polled at most once per 15 minutes on
the normal refresh cycle; sends a User-Agent and nothing else.

**What it adds over the gauges.** A reset that has already happened shows up
in the gauges on the next refresh, so the feature adds nothing there. The parts
the gauges cannot show are advance notice of an announced reset, the tracker's
forecast that one may be coming, and a notification when nobody is looking at
the widget.

**Recommendation.** Leave it in the fork. It is adjacent to what the app does
(usage gauges), it makes the app depend on two hobby endpoints with no schema
contract, and it adds a settings tab, two tiles, a notification queue, five
`winrt-*` dependencies and an HKCU registry key.

**Trigger to start.** Users ask for it upstream. Then prefer a PR from the
author, feeds and tiles first, toasts as a separate decision.

**Changes required if it comes in.**

- Type-check remote `text` and `note` as strings
  (`dd:src/aigauge/providers/codex_resets.py:91`, `:114`;
  `dd:src/aigauge/providers/claude_resets.py:49`). A truthy non-string reaches
  `html.escape` at `dd:src/aigauge/widget.py:831` and raises on the GUI thread.
- Guard `self._widget.update_snapshot` at `dd:src/aigauge/app.py:751`. An
  exception there skips the code that starts the next provider and reschedules
  the timer (`:765-780`), which stops all gauge refreshes until restart. Read
  from code, not reproduced.
- Bound the fetch (`dd:src/aigauge/providers/_resets_common.py:295`, `:318`):
  body size cap, total deadline, and no redirects or a host check. The
  15-second timeout is per socket operation.
- Escape `snapshot.error` where it reaches rich text
  (`dd:src/aigauge/error_dialog.py:86-92`, `dd:src/aigauge/widget.py:1440-1443`),
  or stop storing raw exception text at `_resets_common.py:350`.
- Change the User-Agent from the fork URL (`_resets_common.py:286-291`), clamp
  remote text length, and consider a host allow-list for opened links (today:
  scheme check only, `_resets_common.py:34-41`).
- Renumber the hard-coded settings tab index 7
  (`dd:src/aigauge/settings_dialog.py:1369-1370`), which assumes the
  Antigravity tab exists.
- Toasts: gate on a frozen-build check that a toast appears. `build.ps1` gains
  no collect flags for `winrt`, a failed import falls back to the tray balloon
  silently, and `IconUri` comes from `__file__`
  (`dd:src/aigauge/windows_toast.py:23`), which is a temp path in a one-file
  build.

### N5. Codex page in other languages

Listed here because it started as a deferred item; it is a next-release
candidate.

**Problem.** `main` reads nothing on a non-English Overview page: no "Weekly
limit", no "% left", no "Overview" tab. DraftingDreamer hit this in Traditional
Chinese and added zh-TW labels for the weekly card
(`dd:src/aigauge/providers/_codex_page.py:58`, `:74`, `:77-78`, `:153`) and the
units 天, 小時, 分鐘 (`dd:src/aigauge/providers/codex.py:60-69`).

**Cause, from the live page 2026-10-08.** ChatGPT picks the page language from
the request's `Accept-Language` header. The same signed-in session, fetching
`/settings/usage` with `Accept-Language: zh-TW`, got `<html lang="zh-TW">` and
no `en-US` anywhere in the document; without the header it is `en-US`. The app
never sets that header (`src/aigauge/webview/profile.py:38-44` sets storage and
user agent only), so QtWebEngine sends the operating system's language.

**Recommended approach.**

1. Force English for the scrape session: `profile.setHttpAcceptLanguage("en-US,en")`
   next to `setHttpUserAgent` at `src/aigauge/webview/profile.py:44`. Profiles
   are per provider, so decide whether to set it for Codex only or for every
   scraped provider. Setting it everywhere would also cover a localised Claude
   page, which was not tested.
2. Add a fallback that does not read labels. Each limit card contains a
   `<progress max="100">` whose `value` is the percent remaining (live values:
   100 and 99, matching "100% left" and "99% left"). Two progress elements in
   the limits panel mean 5-hour then weekly; one means weekly-only. This covers
   a user whose language is fixed some other way. It yields the percent but not
   the reset time.

**Status: step 1 shipped in 0.8.5.** It was built on branch
`feature/antigravity-english-profile` (one commit, set in `get_profile` so it
applies to every scraped provider), now merged to `main`. A smoke check through
QtWebEngine against a local server showed `Accept-Language: en-US,en` on the
request and `navigator.language` as `en-US`. Still owed: a look at the real
Codex tile from that branch. Step 2 is not started.

**Does Claude need it too?** It gets it through the shared `get_profile`, and
that is harmless, but the benefit there is unproven. From a signed-in session,
`claude.ai/settings/usage` returned `<html lang="en">` whether the request
asked for `fr-FR`, `ja-JP` or `en-US`, and the active locale in the document
stayed `en-US`. So Claude does not pick its language from the header the way
ChatGPT does; it looks like an account or client-side choice. The profile
setting also changes the browser's own language list, which would cover
client-side detection, but that was not tested. The Claude extractor still
depends on English labels (`src/aigauge/providers/claude.py`, `ROW_LABEL_GROUPS`
and the "Plan usage limits|Your usage" test). **Trigger to revisit:** a report
of Claude usage failing for a user whose Claude is set to another language.

**Alternatives rejected.** Per-language label tables (the fork's route): each
language needs its own table and a real capture, and only the languages someone
contributes ever work. DraftingDreamer's table also lacks the section heading
and the 5-hour label, so it cannot be ported onto the "Plan limits" scoping.

**Risks.** The embedded sign-in window shares the provider profile, so it would
also appear in English. An explicit language choice in ChatGPT's own settings
may override the header; that was not tested because it means changing an
account setting. Whether `<progress>` elements also appear on the Analytics tab
or in the Credits section was not checked, so scope the fallback to the limits
panel.

**Test strategy.** No Chinese system is needed. In a dev run, set the profile
to `zh-TW` to reproduce the failure, then to `en-US` to confirm the fix against
the real page. Unit tests for the fallback use the Node harness with progress
elements added. The fork's zh-TW strings (`dd:tests/codex_dom_fixture.py:31`)
can serve as a non-English sample, flagged unverified.

**Size.** 2 commits: the header, then the fallback.

### D4. Build PATH isolation and packaged-GUI smoke test

- `dd:build.ps1:57-134`, `:164-168` runs PyInstaller in a child process with a
  rebuilt PATH, with `dd:tests/test_build_windows.py`. The stated cause (ICU
  DLLs from another install shadowing the system's) could not be checked.
  **Trigger:** a build that bundles a foreign DLL. Drop the UTF-8 BOM at
  `dd:build.ps1:1` and the comment at `:87-90`. Output is buffered until
  PyInstaller exits.
- `dd:tools/smoke_test_gui.py` starts the packaged GUI offscreen and waits for
  three log lines that exist on `main` (`src/aigauge/app.py:221`, `:256`,
  `:353`). `main` only smoke-tests the MCP helper. **Trigger:** a release whose
  GUI bundle fails to start, or the next change to `ai-gauge.spec`. Trim the
  fork-only config keys at `dd:tools/smoke_test_gui.py:87-89`, `:100-109`.

### D5. Translated READMEs

`README.zh-CN.md` and `README.zh-TW.md` translate the fork's README: fork URLs,
version 0.9.0, Antigravity and reset sections. **Trigger:** a contributor
offers a translation of upstream's README.

### D6. Settings dialog layout

**Problem.** The dialog is a `QTabWidget` with eight tabs in a 620 px window
(`src/aigauge/settings_dialog.py:650`, `:1146-1162`): General, Claude, Codex,
OpenCode, GitHub Copilot, OpenRouter, MCP, Local usage. Antigravity makes nine.
Past the window width Qt shows scroll arrows and hides the overflow, so some
tabs are not visible without scrolling, and every new provider makes it worse.
The "open this provider's settings" jump uses hard-coded tab numbers
(`settings_dialog.py:1227-1235`), which every inserted tab can break.

**Recommended approach.** Replace the tab bar with a list on the left and a
`QStackedWidget` on the right, the usual preferences layout. Group the list:
General; Providers (one row per provider, with an enabled marker); Tools (MCP,
Local usage). Each existing tab widget becomes a stack page unchanged, so this
is a change to the container, not to the pages. Address pages by provider key,
not by number.

**Alternatives considered.**

- One "Providers" tab with a drop-down to pick the provider: fewest tabs, but
  hides which providers exist behind a click.
- Widen the dialog or shorten tab names: buys one or two providers.
- Leave the scroll arrows: no work, and the last tabs (MCP, Local usage) are
  the ones that disappear.

**Risks.** Tests that address tabs by index or by `tabs.count()`. The per-page
scroll area that Local usage already has (`:1162`) needs to survive the move.
Minimum dialog size (`:651`) needs rechecking with a list column.

**Test strategy.** Existing settings tests keep passing with pages addressed by
key; one new test that every provider key resolves to a page; a look at the
dialog at minimum size.

**Trigger to start.** After the Antigravity port merges, since both edit
`settings_dialog.py`. Size: 1 to 2 commits.

## Not coming upstream

- Fork retargeting: repo URLs in `SECURITY.md`, `CONTRIBUTING.md`,
  `.github/ISSUE_TEMPLATE/config.yml`, `pyproject.toml`, `RELEASING.md`; version
  0.9.0; the fork's changelog sections; the fork screenshot.
- `dd:src/aigauge/providers/_codex_page.py` and `dd:tests/codex_dom_fixture.py`
  (hand-built pages, not captures).
- innoscoutpro's `5417cbd`: a Python-only fallback for the weekly card that
  `1075ca8` already covers. Its test inputs are used in N2.
- Legacy Codex layout support ("5 hour usage limit", "Weekly usage limit").
  **Trigger to revisit:** a user log showing those labels in a layout-changed
  diagnosis.

## Attribution

The repo is MIT with "AI Gauge contributors" as the copyright holder, both
forks inherit that, and the issue gives explicit permission to reuse.

- **Their commit, unchanged:** `git cherry-pick -x <hash>` from the original
  author's branch. They remain the author; the message records the source.
- **Their code or tests inside our commit:** add a trailer.
  ```
  Co-authored-by: innoscoutpro <innoscoutpro@gmail.com>
  Co-authored-by: DraftingDreamer <264591489+DraftingDreamer@users.noreply.github.com>
  ```
- **Their idea or report, our code:** changelog credit only.

| Item | Credit |
|---|---|
| N1 | innoscoutpro as commit author; changelog thanks |
| N2 | innoscoutpro co-author (test inputs); DraftingDreamer in the changelog (JS-side flag idea) |
| N3, N4 | DraftingDreamer co-author |

Changelog form, as used for PR #3:
`([#11](https://github.com/jpajak/ai-gauge/issues/11), thanks [@innoscoutpro](https://github.com/innoscoutpro) and [@DraftingDreamer](https://github.com/DraftingDreamer).)`

innoscoutpro has not been in contact. Mention them on #11 when replying.

**Asks to put in the reply on #11.**

1. Separate PRs against `main`, one per feature, for Antigravity (and resets if
   wanted), with the changes listed above.
2. A real zh-TW capture of the Overview page text, including the heading and
   the 5-hour row.
3. A real `agy --print /quota --output-format json` reply.

## What was verified and what was not

- **Run:** N1 (full suite, 591 passed) and the four N2 inputs.
- **Checked on the live ChatGPT usage page (Pro plan, English, Chrome):** the
  extractor from `main` against the real DOM, the page text length, the tab
  attributes, the `<progress>` elements, and the `Accept-Language` response.
- **Not checked live:** a weekly-only plan, the page as rendered in Chinese,
  the same behaviour inside the app's QtWebEngine, and whether an explicit
  ChatGPT language setting overrides the header.
- **Read only:** everything else. No fork code was executed, no feed URL was
  fetched, `agy` was not run, and no packaged build was made.
