# Plan: optional "Fable" weekly bar for Claude (Max plans)

**Status:** implemented (see "Outcome" at the end for what changed vs. this plan)
**Related:** the retired "Claude Design" limit bar — same pattern, removed in
`ad3dace` ("improved testing, and other cleanups"). That commit is the
reference diff: this feature re-adds the identical wiring under a new name.

## Problem

Claude's usage dialog (`claude.ai/new#settings/usage`) now renders a third
row for Max-plan accounts, under **Weekly limits**:

```
Current session   Resets in 3 hr 10 min   [bar]  18% used
All models        Resets in 22 hr 0 min   [bar]   2% used
Fable             Resets in 22 hr 0 min   [bar]   4% used   <-- new
```

AI Gauge currently scrapes only `Current session` and `All models`, so the
Fable weekly limit is invisible. Pro/Free accounts don't have the row, so —
exactly like the old Design bar — it must be an **off-by-default option**,
not a required row.

## What already exists

- `config.py` — `ProviderToggles.claude_fable: bool = False` (uncommitted
  working-tree change). No migration needed: pydantic defaults the field for
  older configs.
- `_on_settings_finished` → `_build_providers()` in `app.py` already rebuilds
  providers after a settings save, so a constructor flag picks up toggle
  changes on the next refresh with no extra plumbing.
- The widget renders whatever metrics a snapshot carries, so no widget code
  changes are required (details below).

## Changes

### 1. `providers/claude.py` — scrape the row (mirror old `show_design`)

- `EXTRACTOR_JS`:
  - Add `'Fable'` to `ROW_LABELS` so `findRowByLabel` penalizes containers
    that span multiple rows.
  - `const weeklyFable = readRow('Fable');` and add `weekly_fable` to **all
    three** return payloads (`null` in the two retry payloads).
  - Add `Fable` to the reset-text lookahead terminators
    (`(?:Daily|Weekly|All|Current|Claude|You)` → `(?:Daily|Weekly|All|Current|Claude|Fable|You)`)
    so the `All models` reset string can't bleed into Fable text if a
    wrapper container is matched.
  - Do **not** add fable to `requiredRowsReady` or the `idleUsagePanel`
    check — the row only exists on Max plans; gating readiness on it would
    break scraping for everyone else.
- `ClaudeProvider.__init__`: add `show_fable: bool = False`, thread it into
  `_build_snapshot` via the `_build` closure (exact shape of the old
  `show_design` param).
- `_build_snapshot(..., show_fable: bool = False)`: when true, append
  `("weekly_fable", "Fable", timedelta(days=7))` to `rows`. A missing/None
  card is already skipped by the loop, so Pro accounts with the toggle on
  just don't get the metric.
- Leave `_EXPECTED_ROWS` as `("session", "weekly_all")` — fable is optional,
  and including it would make `layout_changed` diagnostics noisy for
  non-Max accounts. (The old code did list `weekly_design` there; that was
  the noisier choice.)

### 2. `app.py` — pass the toggle

In `_build_providers()` (~line 405):

```python
self._providers[account.id] = ClaudeProvider(
    parent=self,
    show_fable=self._config.providers.claude_fable,
    account_id=account.id,
)
```

Global toggle applies to all Claude accounts, same as Design did.

### 3. `settings_dialog.py` — the checkbox

Directly under `self.claude_cb` (~line 723), mirroring the removed block:

```python
self.claude_fable_cb = QCheckBox("Show Claude Fable limit")
self.claude_fable_cb.setToolTip(
    "Show Claude's separate weekly Fable model limit (Max plans)."
)
self.claude_fable_cb.setChecked(config.providers.claude_fable)
providers_layout.addWidget(self.claude_fable_cb)
```

And in the save path (~line 1311):

```python
config.providers.claude_fable = self.claude_fable_cb.isChecked()
```

### 4. Widget — no code changes needed

- **Expanded tile:** metric rows render generically → a "Fable" row with
  bar + reset appears automatically.
- **Collapsed tile:** `_compact_metrics_from` includes all untagged metrics;
  `_compact_metric_code("Fable")` falls through to first-letter → an "F"
  chip beside S and W. Optional polish: add an explicit `"fable": "F"`
  entry to the mapping so the code is intentional rather than fallback.
- **Session/weekly ratio:** `ratio.py` filters to the `session`/`weekly`
  labels (~line 272), so the Fable metric is ignored — correct, since the
  ratio models the all-models weekly budget.
- **Idle-zero panel:** `idle_session_weekly_metrics()` stays Session+Weekly
  only (matches old Design behavior). The Fable bar appears once the page
  shows the row with a percent.
- **Tray icon/menubar summary:** uses `metrics[0]` (Session) — unaffected.

## Scrape robustness notes

- The usage page also shows an informational banner containing "Fable"
  ("Fable 5 is still included with your Max plan…"). `findRowByLabel`
  requires a `%` in the candidate's text and prefers the shortest match, so
  the banner alone can't win, and banner+row wrappers lose to the bare row.
- Substring matching means a future rename to "Fable 5" still matches
  `readRow('Fable')`.

## Tests

Run with `.venv\Scripts\python.exe -m pytest` (system Python 3.14 breaks).
Mirror the tests deleted in `ad3dace` (`git show ad3dace^:tests/...`):

- `tests/test_config.py` — `claude_fable` defaults off; round-trips through
  save/load.
- `tests/test_claude.py` —
  - payload with a `weekly_fable` card + `show_fable=True` → 3 metrics
    labelled Session/Weekly/Fable, fable window = 7 days;
  - `show_fable=True`, `weekly_fable=None` (Pro account) → 2 metrics, OK
    status;
  - `show_fable=False` with the card present → fable ignored;
  - add `"weekly_fable": None` to existing payload fixtures.
- `tests/test_settings_dialog.py` — checkbox reflects config and saves the
  toggle (the removed test was ~13 lines; same shape).
- `tests/test_app.py` (optional) — `_build_providers` passes the toggle.

## Changelog

Add under Unreleased: optional "Show Claude Fable limit" setting surfaces
the weekly Fable bar on Max plans (off by default).

---

## Outcome

Implemented as planned, with one correction the plan got wrong.

**The banner defence in "Scrape robustness notes" above was insufficient.**
The plan argued that requiring a `%` in the candidate plus preferring the
shortest match was enough to keep the "Fable 5 is still included with your
Max plan…" banner from being mistaken for the Fable row. That holds only
while a real Fable row exists to win the comparison. On a Max account where
the banner renders but the row does not, the smallest candidate containing
both "Fable" and a `%` is the container holding the banner *and* the
All-models row — so the extractor reported a **phantom Fable bar duplicating
the All-models percentage**. Verified by mocking the dialog under Node
before the fix (Fable read 2%, exactly the All-models value).

Fixed by adding a `headsARow(text, label)` predicate to the extractor: a
candidate qualifies only if some occurrence of the label is followed by the
reset text, a percentage, or `used`/`remaining` — the shape of a real row.
Prose mentions are rejected. An optional version number is absorbed so a row
renamed to "Fable 5" still matches, while the banner's "Fable 5 is still
included…" still fails the tail test. This applies to all rows, not just
Fable, so the same class of bug is closed for Session and Weekly.

**Extra scope taken on:** `tests/test_claude_extractor_js.py` runs the real
`EXTRACTOR_JS` under Node against a mocked usage dialog. The injected script
previously had no test coverage at all — the Python tests feed `_build_snapshot`
pre-extracted payloads, so no row-matching bug was catchable. The phantom-row
bug above was found this way and is now a regression test. Skips when Node is
absent; GitHub's runners all provide it.

Widget changes stayed at zero as predicted; only the optional `"fable": "F"`
entry in `_compact_metric_code` was added.

**The setting is per account, not global — this plan had it wrong.** Steps 2
and 3 above put `claude_fable` on `ProviderToggles` and a single checkbox in
the General tab's Providers box, copying the retired Design toggle. That box
is for provider *kinds* (Claude vs Codex vs Copilot), so the checkbox rendered
as a sixth peer provider, and the model implied plan tier is a property of the
app rather than of a subscription. `BrowserAccount` already carries per-account
display config (`colors`, `usage_url`), which is the right home.

Final shape: `BrowserAccount.show_fable`, one checkbox per Claude account row
on the Claude tab, and `_build_providers` reading `account.show_fable`.

Note this does *not* fix "wrong bar on a non-Max account" — the extractor
already returns `null` there, so the old global toggle was self-limiting. What
per-account buys is control across two or more Max accounts.

**Default is on, not off** (this plan assumed off, mirroring Design). Because
a plan without the limit yields no row at all, the gauge simply does not
appear there — so defaulting off only hid the limit from the Max users who
wanted it, behind a checkbox almost nobody would find. No migration is needed:
absent keys take the model default, so existing configurations opt in, while
an explicit `false` is preserved.

The default was flipped only *after* confirming against a real Max account
that the row scrapes correctly and disappears when disabled. Until that point
every test ran against a mock of the page, and the phantom-row bug above is
the reason that distinction was worth waiting on.

Full suite: 334 passed.
