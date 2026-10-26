# Plan: local usage, API-equivalent cost, and allowance trends

**Status:** released in 0.8.0 (phases 1 to 5).
**Date:** 2026-09-14 (revised after a review against real logs on the author's machine)
**Scope:** Claude Code and Codex usage read from local CLI logs, combined with AI Gauge's existing quota observations.

## 1. Goal and decisions

Help a user answer three questions:

1. How much did I use in this allowance window, and on which models?
2. What would that usage have cost at API rates, in total and per model?
3. Does the same amount of work use more or less of my allowance than it used to?

Decisions:

- **Opt in.** The feature is off until the user turns it on. While it is off, AI Gauge reads no log files, starts no timers, and writes no new data. The gauge and the existing details dialog behave exactly as they do today.
- **Backfill on enable.** Turning it on offers to import everything the logs still hold, even if that takes a while. The import runs in the background, shows progress, can be cancelled, and resumes where it stopped.
- **Per-model breakdown.** Show how much each model was used (tokens by category, message count) and what each model's usage would have cost, for the current window, a day, and longer ranges.
- **No ccusage.** AI Gauge reads the Claude and Codex logs itself. ccusage is not a runtime dependency, and Settings has no install link. See section 10 for why.
- **Compare whole windows.** Trends compare completed allowance windows. Capped and currently open windows remain visible for context, but do not enter rolling averages or comparisons. There is no engine that matches short intervals.
- **Save as you go.** Usage is saved during a window, not only at rollover, so history survives app shutdowns and Claude Code's log cleanup.
- **Headline numbers.** Dollars per quota point, shown next to output tokens per quota point. The second is a check that isn't skewed by cache reads, and it still works when a model has no known price.
- **Codex quota source.** For the cost comparison, use the quota readings Codex writes into its own logs. The gauge keeps using its current website readings.
- **One account per provider.** Local usage for each provider is assigned to exactly one account, chosen by stable ID. Unchanged from the first draft.

## 2. What the logs contain (measured 2026-09-14)

Measured with a throwaway script and ccusage 20.0.17 on the author's machine. The numbers below are the design inputs.

**Claude Code** (`~/.claude/projects/**/*.jsonl`, 384 files, 3.9 GB):

- Each `type: "assistant"` line has an ISO `timestamp`, `requestId`, `message.id`, `message.model`, the CLI `version`, `isSidechain`, and `message.usage`. Usage includes `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation.ephemeral_5m_input_tokens`, `cache_creation.ephemeral_1h_input_tokens`, `speed`, and `service_tier`.
- **The oldest file was exactly 30 days old**, which matches Claude Code's default `cleanupPeriodDays`. Anything older is gone unless AI Gauge has already saved it.
- **Messages repeat across lines.** 36% of `(message.id, requestId)` keys appear on more than one line. In CLI versions 2.1.260 to 2.1.267, the earlier lines hold placeholder `output_tokens` (for example 2, 2, 2, then 871). In all 1,982 cases that disagreed, the last line had the largest value. Keeping the first line lost 26% of output tokens on 2026-09-10.
- **Subagent logs matter.** Files under `subagents/` directories (`isSidechain: true`) held 29% of output tokens on 2026-09-10. They count against quota and must be included.
- Some lines have the model `<synthetic>`. These are not real model calls.
- No duplicate keys were found across different files within 7 days.
- A Python scan that skipped lines without `"usage"` before calling `json.loads` read 1.66 GB (the last 7 days) in 2.3 s.

**Codex** (`~/.codex/sessions/**/*.jsonl`, 268 files, oldest 2026-04-14, so no 30-day cleanup was seen):

- `event_msg` lines with `payload.type == "token_count"` carry a `timestamp`, `info.total_token_usage` (a running total for the session) and `info.last_token_usage`. Categories are input, cached input, cache write, output, and reasoning output.
- The same events carry `rate_limits`: `primary` and `secondary` `{used_percent, window_minutes, resets_at (epoch)}`, plus `limit_id`, `plan_type` and a credits block. 3,887 of 3,891 events in 30 days had them. `used_percent` was always a whole number.
- 35 events repeated the previous running total, so summing `last_token_usage` would double count. Add up the changes in `total_token_usage` instead.
- The published protocol makes `info`, `rate_limits`, `window_minutes` and `resets_at` optional. Handle them being missing.
- Scanning 30 days took 0.9 s.

**ccusage comparison:** for 2026-09-10, ccusage reported 1.58M Claude output tokens. The logs contain 2.79M when the last line of each message is kept (1.98M in main session files, 0.81M in subagent files). The cause was not found. ccusage is therefore not a reliable reference for tests.

## 3. Existing code this builds on

| Code | Role |
| --- | --- |
| [app.py:231-232](../src/aigauge/app.py#L231-L232) | Creates `HistoryStore` and `RatioStore`. The new `LocalUsageService` is created here, but only when the feature is enabled. |
| [app.py:620](../src/aigauge/app.py#L620) `_on_snapshot` | Already feeds history and ratio ([app.py:659-665](../src/aigauge/app.py#L659-L665)). After a successful snapshot for an assigned account, trigger an incremental import and update the window summary. |
| [app.py:1002](../src/aigauge/app.py#L1002) `open_ratio_history` | Opens the details dialog; also passes local usage data when enabled. |
| [ratio_dialog.py:139](../src/aigauge/ratio_dialog.py#L139) `RatioHistoryDialog` | Gains tabs when the feature is enabled. |
| [widget.py:925](../src/aigauge/widget.py#L925) `ratio_history_requested` | Today the dialog only opens from the ratio label. Add a Details menu action so it can be reached before the ratio has calibrated. |
| [history.py:36](../src/aigauge/history.py#L36) `PeriodRecord`, [history.py:81](../src/aigauge/history.py#L81) `record_snapshot` | Supplies Claude window identity (`resets_at`), the last reading (`last_seen_at`, `peak_pct`) and closed periods for backfill. `snapshot.provider` is the account ID. |
| [ratio.py:37-38](../src/aigauge/ratio.py#L37-L38) | Existing thresholds for readings too low or too close to 100% to count. Reuse them for the trend's exclusion rules. |
| [config.py:70](../src/aigauge/config.py#L70) `app_data_dir`, [config.py:140](../src/aigauge/config.py#L140) `BrowserAccount.id`, [config.py:179](../src/aigauge/config.py#L179) `Config` | Location of the new database; stable account IDs for assigning usage; new settings fields. |
| [settings_dialog.py:1130-1135](../src/aigauge/settings_dialog.py#L1130-L1135) | Tab list; add a "Local usage" tab. |

The existing ratio feature, its history files and its "Coverage" label stay unchanged. New labels never reuse "Coverage".

## 4. Reading the logs

New package `src/aigauge/local_usage/`.

### Claude parser (`claude_logs.py`)

- Default roots: `~/.claude/projects` and `~/.config/claude/projects`. A `CLAUDE_CONFIG_DIR` value is read from the app's own environment when present. The user can override roots in Settings. If the same file is reached through two roots, read it once (compare resolved paths).
- For each line: skip it unless it contains `"usage"` and `"assistant"`, then parse the JSON. Keep only `type == "assistant"` lines that have `message.usage`.
- Skip `<synthetic>` models.
- Messages are identified by `(message.id, requestId)`. **When a key appears again, the later line replaces the earlier one.** Store the database row as an upsert.
- Include sidechain and subagent lines, and store `is_sidechain` so the display can split them out later.
- Stored fields: timestamp (UTC), model, input, output, cache read, cache write 5m, cache write 1h, speed, service tier, CLI version, sidechain flag. No prompt text, no tool output, no project paths.

### Codex parser (`codex_logs.py`)

- Default root: `$CODEX_HOME` or `~/.codex`, reading both `sessions/` and `archived_sessions/`. The root can be overridden in Settings.
- Token usage: for each session, add up the changes in `info.total_token_usage` between `token_count` events. If the total drops, start a new baseline and never record negative usage. Ignore repeats of the same total.
- Model: taken from the session's `turn_context` events. Confirm this with a sample log in phase 1. If a token event can't be tied to a model, store it as `unknown` rather than guessing.
- Quota readings: store each `rate_limits` reading as `(timestamp, limit_id, primary|secondary, used_percent, window_minutes, resets_at, plan_type)`. Skip missing fields without failing.

### Import progress and changed formats

- For each file, record the path, size, modification time, the byte position read up to, and a hash of the bytes just before that position. When a file grows, read from that position. When it shrinks or the hash no longer matches, re-read the whole file; the upserts make that safe. A half-written last line is not recorded as read.
- A new state, **"logs found, usage not recognized"**, applies when files exist and contain assistant or token_count lines but nothing parses. It shows as a status in Settings and the dialog, never as zero usage.
- Record the Claude CLI versions and Codex log formats seen. Anything outside the versions covered by test fixtures appears as an informational note in Settings. Importing continues.

## 5. Opting in and backfill

### Settings: new "Local usage" tab

- **Track local usage** checkbox, off by default.
- For each provider (Claude, Codex): an enable switch, the assigned account (preselected when there is only one; with several, the user must choose, and it is saved by ID), the detected log folder with Browse and Reset, and a status line: files found, oldest log date, last import, and any version or format note.
- **Import history** button (runs the backfill again), **Clear local usage data**, the rate table version, and the path of an optional rate override file.
- The disclosure text from section 9.

Removing the assigned account pauses tracking for that provider; it never moves to another account. Changing the account or log folder starts a new comparison period, so old and new windows are never compared with each other.

### What happens when the user turns it on

1. A scan reads only folder listings and file sizes, then a dialog explains what can be imported. For example: "Claude logs go back to Aug 15 (3.9 GB). Codex logs go back to Apr 14. Importing reads these files in the background and may take a little while." Buttons: **Import history** (default) and **Start from now**.
2. The import runs in a worker thread with a progress bar (bytes read out of total) in the Settings tab and in the dialog, plus a Cancel button. The gauge and quota refreshes are never blocked. The saved progress from section 4 lets a cancelled or interrupted import resume.
3. After importing, build window summaries for past windows:
   - **Codex:** from its own quota readings, for every window the logs cover.
   - **Claude:** from closed periods in `history.jsonl` whose whole window is covered by the imported logs. These are marked *backfilled*. Old `history.jsonl` timestamps have no timezone, so they are read as local time with the current offset. Periods within a daylight saving change are marked *time uncertain* and left out of the trend baseline.
4. "Start from now" imports nothing older than the moment tracking was enabled. The Import history button can still run a backfill later.

Turning the feature off stops the import and all file reading, and keeps the saved data. Clear local usage data deletes only the new database. It never touches CLI logs, credentials, or the existing history and ratio files.

### Keeping up to date

While tracking is on, run an incremental import in the worker after each successful quota refresh of an assigned account ([app.py:620](../src/aigauge/app.py#L620)), and when the details dialog opens (at most once a minute). The dialog shows saved data immediately and updates when the import finishes. No separate fast timer.

## 6. Storage

One SQLite database, `app_data_dir()/local_usage.sqlite`, using the standard library `sqlite3`, with a `schema_version` table. Timestamps are stored as UTC ISO strings.

| Table | Contents |
| --- | --- |
| `source_files` | Provider, path, size, modification time, read position, hash of the last bytes read, last import time. |
| `claude_messages` | Primary key `(message_id, request_id)`. Timestamp, model, the six token categories, speed, service tier, CLI version, sidechain flag. |
| `codex_usage` | Session ID, timestamp, model, and the change in each token category. |
| `codex_quota_readings` | Timestamp, limit ID, primary or secondary, used percent, window minutes, reset time, plan type. |
| `window_summaries` | Account ID, metric label, window start, reset time, last reading time, percent at the last reading, **tokens by model and category (JSON)**, message count by model, origin (`live` or `backfill`), quality flags, comparison period ID. Updated during the window and finalized at rollover. |
| `daily_model_totals` | Local date, provider, model, tokens by category, message count. Kept after message rows are removed, so the daily and per-model history lasts. |

**Cost is calculated when data is read, not stored.** Every summary keeps its tokens by model and category, so costs always use the current rate table. A rate table update changes all history the same way and can never look like an allowance change. This drops "what did it cost at the time", which is acceptable because costs here are estimates.

**Retention:** message and usage rows for 90 days, then only daily totals and window summaries remain, and those are kept indefinitely because they are small. Around 32K Claude messages a week were seen here, so the database stays in the tens of MB.

## 7. Windows, costs and trends

### Window summaries

A window's usage is the total of activity from **window start** to the **last quota reading**, and its quota figure is **the percent at that reading**. The logs cover the whole window no matter when AI Gauge started, so no starting reading is needed.

- **Claude:** window start is `resets_at - window` (5 hours for Session, 7 days for Weekly). The reading comes from `history.py` (`last_seen_at`, `peak_pct`). `resets_at` comes from rendered text and can drift by a few minutes, which is negligible over a whole window. Phase 1 confirms that the start of a 5-hour window really is `resets_at - 5h`.
- **Codex:** the window comes from the log's `resets_at` and `window_minutes`. The reading is the last `rate_limits` reading at or before the time being measured.
- If the window start falls before the oldest imported log (a Claude gap after more than 30 days without the app running), the summary is marked *incomplete* and left out of trends.

### Pricing

- A bundled, versioned `local_usage/rates.json` has one entry per model: input, output, cache read, 5m cache write, 1h cache write, speed or tier multipliers, long-context thresholds where they apply, and currency. Values come from the providers' official API pricing pages when the file is written or updated, and each entry records its source and date.
- An optional `app_data_dir()/rates.override.json` lets users add or correct models without waiting for a release.
- A model with no entry is **unpriced**: its tokens still show, its cost shows "price unavailable", and there is never a substitute model's price or a zero.
- Totals stay readable and show the priced portion. A nearby note names the excluded usage and its share of tokens.

### Trend

For each completed, comparable window:

```text
dollars per point        = window cost / percent at last reading
output tokens per point  = window output tokens / percent at last reading
pooled value             = total usage / total percentage used
change vs baseline       = recent pooled value / baseline pooled value - 1
```

- Across windows, divide total cost by total points; never average the per-window ratios.
- Leave a window out when:
  - its percent is below `MIN_COUNTABLE_PCT` or above `MAX_COUNTABLE_PCT`,
  - it is incomplete or time-uncertain,
  - it has unpriced usage (for the dollar figure only),
  - it belongs to another comparison period (account, folder or plan changed; Codex `plan_type` changing counts).
- Baseline: the usage-weighted aggregate of at least 3 earlier comparable completed windows of the same metric. A marked limit change uses the immediately preceding segment as the baseline while keeping its points visible; only a window spanning the boundary is excluded. Show the range and the number of windows used. Before that, show "Collecting windows (n of 3)".
- Show cache share (cache read tokens ÷ all input tokens) and the top model next to every trend row, because a different cache or model mix changes dollars per point without any change to the allowance.
- No "your limit changed" alert. No influence on quota percentages, the ratio, or the MCP guard.
- Model-specific limits (the Claude Fable weekly bar) are not compared at first. The per-model data makes it possible later: Fable-only cost divided by the Fable percentage.

## 8. Display

When tracking is on for the account, `RatioHistoryDialog` gets two tabs. With tracking off, the dialog looks exactly as it does today.

| Tab | Contents |
| --- | --- |
| Session vs weekly | The existing summary, sparkline and table. Selected first. |
| Usage and cost | Everything below. |

**Usage and cost tab**

```text
Usage and cost                                   Updated 45 s ago
This computer · Claude (Work account)          [Importing 62% ✕]

                 Session (2h 10m left)     Weekly (4d left)
Est. API cost    $12.40                    $86.20
Quota used       21%                       38%
$ per point      $0.59                     $2.27
Output tok/pt    1.9K                      7.4K

Models   [Current session ▾]  (Current session · Current week · Today · Last 7 days · Last 30 days)
Model             Msgs   Input  Output  Cache rd  Cache wr   Est. cost   Share
claude-opus-5      412    9.1K   151K     18.2M     1.1M      $10.90    88% ████████▊
claude-sonnet-5    130    2.2K    31K      3.9M     0.3M       $1.50    12% █▏
claude-fable-5-1     6    0.1K     2K      0.1M       0K  price unavailable  n/a
Total              548   11.4K   184K     22.2M     1.4M      $12.40

[Daily]  [Allowance trend]
```

- **Model table:** one row per model, with message count, each token category (a column toggle adds 5m and 1h cache writes, reasoning tokens for Codex, and sidechain share), estimated cost, share of cost, and a small bar. Sorted by cost, with unpriced rows last. The range selector covers the current session, the current week, today, the last 7 days and the last 30 days; longer ranges use `daily_model_totals`.
- **Daily:** one row per local date with total cost and a bar split by model; clicking a day filters the model table to that day.
- **Allowance trend:** session or weekly windows with window, total cost, percent, dollars per point, output tokens per point, cache share, top model, origin (live or backfill) and status. Capped and current partial windows stay visible and labeled while completed comparable windows alone feed the rolling averages and comparisons. By default, a compact recent ballpark sits above a historical chart of raw points and the rolling average; the chart focuses its scale on that trend and marks off-scale raw values at the edge. If a marked limit change has enough history, an optional comparison mode adds zero-based before/recent bars and exact-period reference lines. Method and model/cache-mix caveats live in tooltips instead of explanatory body copy.
- Missing data shows as "unavailable", never 0. Old data shows its timestamp. While importing, numbers are marked "partial".
- A short footer: "This computer's logs only · API-equivalent estimate", with an info button for the full disclosure.
- The dialog resizes and scrolls, and works at small window sizes and high UI scaling.
- Nothing is added to the main gauge in this plan.

## 9. Disclosure text

Settings:

> Reads the Claude Code and Codex log files on this computer to estimate usage and API-equivalent cost. Only token counts, models and times are kept, never prompts or responses. Nothing is uploaded.

> Includes only activity recorded on this computer. Browser chats, cloud tasks and other computers use the same allowance but are not counted. Claude Code deletes its logs after 30 days by default; AI Gauge keeps its own summary once imported.

> Costs are estimates at current API prices, not charges. Trends compare this computer's recorded activity with the account's usage percentage; they cannot confirm a provider changed its limits.

Account selection:

> All of this provider's logs on this computer are counted against the selected account. If you also use other accounts or API keys here, the trend will be unreliable.

## 10. Alternatives considered

- **ccusage as a required dependency (first draft).** Rejected for four reasons:
  - Its JSON merges 5m and 1h cache writes (priced differently) and leaves out speed.
  - It reports Codex cost per day but not per model, and does not pass through Codex quota readings.
  - It priced `gpt-5.5` with a fallback price (`isFallback: true`).
  - Its 2026-09-10 output total was 43% below what the logs contain.

  It would also add a Node runtime dependency and a Windows launcher layer. It can still be used by hand while developing, but tests must rely on hand-checked sample logs.
- **Matching short intervals of local activity to quota changes (first draft).** Rejected. Readings are whole percentages taken every 5 minutes or more, so short intervals are dominated by rounding error. Whole windows with timestamped events give the same answer more simply.
- **Storing costs at the time of use.** Rejected. It turns rate table updates into false trend changes and needs price versions tracked per row. Storing tokens and pricing on read avoids both.
- **Keeping every message row forever.** Rejected for size. Daily totals and window summaries keep everything the display and trends use.

## 11. Risks

- **Log formats change.** Both formats are undocumented. Mitigations: the "not recognized" state, notes for untested versions, and sample logs for each version seen. The placeholder output bug shows that one CLI release can change the de-duplication rule.
- **Claude cleanup outpaces the app.** If AI Gauge doesn't run for more than 30 days, that stretch is lost. Summaries are marked incomplete, and the disclosure says so.
- **Mixed cache and model use** can move dollars per point with no change to the allowance. That's why cache share and top model sit next to every trend row, and why output tokens per point is shown too.
- **Usage the logs don't see** (web, cloud tasks, other machines) raises the percentage without adding cost, which lowers dollars per point. This can't be detected; it's covered in the disclosure.
- **The rate table goes out of date.** New models show as unpriced until the table is updated or overridden. Costs for old models may drift from actual API prices, but consistently across all history.
- **Codex `limit_id` values other than `codex`** (limits for specific models) may appear. Store them, and compare only `codex` at first.

## 12. Build order

Each phase ships on its own and leaves the feature working.

1. **Parsers and sample logs** (2 to 3 commits). `claude_logs.py` and `codex_logs.py` as pure functions over lines, plus sanitized sample logs covering: placeholder output lines, subagent and sidechain files, `<synthetic>`, repeated and dropping Codex totals, missing `rate_limits` or `info`, `turn_context` model attribution, and a half-written last line. Also confirm that a Claude 5-hour window starts at `resets_at - 5h`.
2. **Storage, import, opting in, backfill** (3 to 4 commits). The SQLite schema, import progress, the worker, the Settings tab, the enable dialog with Import history or Start from now, progress and cancel, retention, clearing, and turning off.
3. **Usage and cost tab with the model breakdown** (2 to 3 commits). `rates.json` and the override file, pricing on read, the dialog tabs, the model table with the range selector, the daily view, the Details menu action, and the disclosure text.
4. **Window summaries** (2 commits). Live updates after each refresh, finalizing at rollover, Codex summaries from its quota readings, and Claude backfill from `history.jsonl` with the time-uncertain flag.
5. **Allowance trend** (1 to 2 commits). The comparison rules, baseline, trend table and exclusion reasons.

## 13. Tests

Run with `.venv\Scripts\python.exe` and `QT_QPA_PLATFORM=offscreen`, as CI does. No live accounts; parsers and import run against sample logs in `tmp_path`.

- **Off by default:** with tracking off, no files are opened, no timers start, no database is created, and the dialog renders as it does today.
- **Claude parser:** placeholder lines are replaced by the final line; subagent usage is included; `<synthetic>` is skipped; all six categories are read; the parsed total matches totals worked out by hand for each sample log.
- **Codex parser:** repeated totals don't double count; a dropping total starts a new baseline; missing `info` or `rate_limits` doesn't fail; events without a model become `unknown`.
- **Import:** appends are read incrementally; a truncated or rewritten file is fully re-read without double counting; a half-written line is picked up on the next pass; two roots pointing at the same file count it once; a cancelled backfill resumes and ends with the same totals as an uninterrupted one.
- **Formats:** unrecognized lines produce the "not recognized" state, not zero.
- **Pricing:** unpriced models show tokens with "price unavailable" and never add $0; totals show the priced portion with a note naming exclusions; changing the rate table changes costs but not trend exclusions; the override file takes precedence.
- **Windows:** the Claude start is taken from `resets_at`; the Codex window comes from its readings; incomplete, time-uncertain and out-of-range windows are left out with their reason; the baseline needs 3 windows; totals are divided rather than ratios averaged.
- **Accounts:** renaming or reordering accounts keeps the assignment; removing the account pauses tracking; reassigning starts a new comparison period; other accounts never show the same totals.
- **UI:** empty, importing, partial, not recognized, unpriced, and long-history states; the model table range selector; the dialog at small sizes.
- **Unchanged behaviour:** the existing ratio, history and MCP tests still pass.
