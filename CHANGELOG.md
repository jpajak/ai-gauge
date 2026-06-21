# Changelog

## 0.6.1 - 2026-06-20

### Fixed

- The widget no longer vanishes at high Windows display scales (175%/200%) while the tray icon and Settings stay reachable. The last position is saved in device-independent pixels; raising the OS scale shrinks the logical desktop, so a spot that was on-screen at 100–150% could fall entirely outside the visible area — leaving the app running but the widget parked off-screen. The saved position is now clamped onto the nearest visible screen both when it is restored and on every show, so a scale or monitor change can never strand the widget out of view.

## 0.6.0 - 2026-06-19

### Added

- A **UI scale** setting (Settings → General) resizes the whole widget from 75% up to 400% — enlarging it for high-resolution (4K) displays where the otherwise fixed-pixel layout could render very small, or shrinking it for a more compact footprint. It is applied through Qt's display scaling (`QT_SCALE_FACTOR`) so fonts, bars, and icons stay crisp; changing it offers to restart AI Gauge so the new size takes effect immediately.

### Changed

- Windows Start at login now creates a named Task Scheduler entry instead of writing an `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` value, reducing Defender false-positive risk from Run-key persistence.
- Windows PyInstaller builds now include generated executable version metadata for product, company, description, filename, and version fields.

### Fixed

- **Start at login** on Windows now actually registers, and saving Settings with it enabled no longer crashes the app. The Task Scheduler entry had two bugs: it was written as UTF-8 (rejected by `schtasks /XML` as malformed — "unable to switch the encoding"; now UTF-16), and its logon trigger/principal had no user scope, so Windows treated it as an all-users task and refused to register it without admin ("Access is denied"). It is now scoped to the current user via `UserId`, so no elevation is needed. Settings are also persisted *before* autostart is wired up, and any remaining autostart failure surfaces a warning instead of aborting the app.
- Unhandled exceptions are now written to the log via an excepthook, so a crash in a Qt slot leaves a diagnosable trace instead of silently terminating a windowed build.
- The app no longer quits when a dialog or message box is the last window dismissed: `setQuitOnLastWindowClosed(False)` is now applied to the live `QApplication` instead of (ineffectively) before it was constructed, so the tray-resident app stays running.
- Settings dropdown and spin-box arrows now render as proper chevrons instead of empty grey blocks.
- The sign-in window's **I'm signed in** check no longer hangs and falsely reports `Could not load verification page (timeout)` right after a successful sign-in. Because the embedded browser was already on `claude.ai/new`, navigating it to the `…/new#settings/usage` verification URL was a same-document (fragment-only) change that never emits a load-finished event, so verification waited out its full timeout. It now polls for the signed-in marker on a timer instead of depending on that event, while still fast-failing a genuine load error.

## 0.5.9 - 2026-06-01

### Changed

- Claude usage scraping now targets Claude's app-shell usage dialog route and no longer carries the retired separate design-generation limit through settings, config, or tile rendering.

### Fixed

- The session-to-weekly burn rate now treats a mid-week weekly reset (Claude occasionally zeroes the weekly counter while keeping the same reset date) as the same week rather than a new one. Previously any weekly percent drop was read as a rollover, which could record a spurious partial week and restart the current week's estimate. Now only a forward jump of the weekly reset date starts a new week; a same-date drop is skipped as a discontinuity while the week's accumulation is kept, so the estimate just drifts toward the new ratio that week and locks onto it the following week.
- Session-to-weekly history now normalizes old split records that were finalized for the same weekly reset, so stale fragments like a 1% coverage tail no longer appear as fake prior weeks. The history dialog now labels rows by their actual observed dates and shows `n/a` for low-confidence ratio values instead of displaying noisy estimates.
- Claude refreshes now wait for the Session and Weekly usage rows specifically, so unrelated percentage text in the shell no longer causes a premature stale error while the usage dialog is still hydrating.
- Stale error snapshots now pull the next automatic refresh forward to a short recovery retry instead of drifting into the idle backoff cadence.

## 0.5.8 - 2026-06-01

### Added

- Claude and Codex tiles now show a session-to-weekly burn rate on the right of the tile header (e.g. `~9.2/wk`), meaning how many full sessions you can run before the weekly limit is used up. It is measured empirically from the readings AI Gauge already collects (no extra scraping): while a session is counting, weekly usage climbs in proportion, so the ratio of those increments gives a stable estimate. Hovering shows the percent-of-weekly-per-session framing plus a recent-weeks trend, and clicking opens a small history dialog with a sparkline and the last 26 weekly ratios so you can see how the providers retune their limits over time. The dialog also shows two at-a-glance views of the current ratio: how much each full session costs in weekly percent, and how many full sessions remain in the current week (based on the live weekly percent used), plus a `Typical (last N weeks)` median once a few weeks are recorded. The header value is the usage-weighted average across all sessions in the current weekly period and keeps refining as the week goes; at a new week it carries over last week's value (dimmed, with a `°` marker and the new week's calibration progress in the tooltip) until the new week has enough data to stand on its own. Idle/unused windows are ignored, and readings outside 2-99% (start-of-window floors such as Codex's idle `1%`, and saturated tails) are excluded so neither end can skew the estimate. The value reads `burn ~?` while calibrating until there is enough usage for a stable reading.

### Changed

- GitHub Copilot now reads the current billing usage summary endpoint and displays monthly AI credits instead of legacy premium requests. Plan defaults were updated for the June 1, 2026 credit model (Pro 1,500, Pro+ 7,000, Max 20,000), and request-based accounts still fall back to GitHub's legacy premium-request endpoint.

### Fixed

- Fixed Copilot credit parsing for GitHub's live `copilot_ai_unit` / `ai-units` billing SKU, so Pro accounts now show returned credit usage such as `68.6/1500` instead of `0/1500`.

## 0.5.7 - 2026-05-18

### Added

- Claude and Codex settings tabs now include an `Open usage in browser` button so you can jump straight to each provider's web usage page from the app.

### Fixed

- Claude's in-page usage-panel polling timeout is now treated as a retryable scrape failure, so the configured second full page-load attempt actually runs instead of surfacing `extractor retry limit exceeded` immediately.
- Provider tiles now keep showing the last successful metric rows when a later refresh fails, with an `error · stale` status so the stale values remain useful but clearly marked.
- Claude and Codex no longer treat incidental `Cloudflare` text inside normal signed-in page content as a browser-check interstitial. The shared security-verification detector now requires stronger challenge signals and ignores pages with usage-page evidence, preventing false `Click Connect` auth errors on valid usage pages.

## 0.5.6 - 2026-05-18

### Changed

- Claude and Codex now share a single `ScrapeRunner` that drives the headless scraper and retries the whole scrape once when the snapshot builder reports a transient layout error. Codex previously had no build-level retry, so a half-rendered analytics page would surface as an immediate error instead of recovering on the second pass.
- The Claude extractor now polls inside the same page load when the usage panel hasn't rendered yet (no `%` and no `Plan usage limits` text). This reuses the existing `__retry_after_ms` path that Codex uses for the Personal usage tab, so a slow-hydrating panel no longer requires tearing down the page and reloading it.
- Shared helpers (`normalize_percent`, `idle_session_weekly_metrics`, `is_security_verification_page`) are factored out of the Claude and Codex providers into `providers/_common.py` so the two paths stop drifting.

### Fixed

- Reduced AI Gauge log noise on Claude scrapes. claude.ai's `[IsolatedSegment]` analytics iframe and Datadog RUM bundle previously produced ~25 info-level console lines per refresh; those fragments are now filtered, and remaining JS info-level console messages are routed to DEBUG so only warnings and errors from the embedded pages reach the file log. Warnings, errors, and AI Gauge's own scrape lifecycle lines are unchanged.
- Cookie hydration no longer overwrites Chromium's persisted cookies for an account on every startup. The keyring's stored blob is only re-injected when the profile has no `Cookies` file yet (first launch after Paste cookie, or a wiped profile). Once Chromium has its own cookie store on disk, session tokens that the site rotates mid-session now survive AI Gauge restarts instead of being clobbered back to the original paste, which had been causing pasted-cookie accounts to drift into a "Sign in" state after a few restarts.

## 0.5.5 - 2026-05-07

### Changed

- OpenRouter model breakdown rows now strip the provider prefix (e.g. `anthropic/`, `openai/`, `google/`) from the displayed name and truncate names longer than 20 characters with an ellipsis. The full original slug is preserved in the row tooltip.
- OpenRouter model breakdown bars now line up at the same x position by sizing all model rows to a uniform label column width.

### Fixed

- Fixed Codex personal usage scraping after ChatGPT started dropping the `#personal-usage` fragment and defaulting the analytics page to Workspace usage. AI Gauge now selects the Personal usage tab inside the rendered page, waits for the tab content to hydrate, and only verifies Codex sessions once the actual personal usage rows are visible.

## 0.5.4 - 2026-05-07

### Added

- Claude and Codex now support multiple named accounts. Add extra accounts from the dedicated Claude or Codex Settings tab; each account gets its own browser profile, cookie storage, tile, snapshot history, and display name such as `Codex (Account 2)`.
- Settings now separates provider visibility from account management: General controls whether Claude/Codex groups appear, while the Claude and Codex tabs manage account names, sign-in, cookie paste, add, and remove actions.

### Changed

- The main widget now groups multiple Claude accounts before Codex accounts, keeps secondary account names visible in expanded and compact views, wraps compact chips onto additional rows when needed, and uses a scrollable dark tile area when many accounts are shown.
- Codex/OpenAI sign-in guidance now explicitly calls out Google and passkey accounts: use Paste cookie when the embedded browser cannot complete the Google/passkey flow.

### Fixed

- Fixed the multi-account widget scroll area inheriting Qt's default light background.
- Fixed secondary-account Settings rows being cramped in a single mixed provider list.

## 0.5.3 - 2026-05-06

### Added

- OpenRouter support with separate storage for the standard inference key and management key, plus settings for enabling the provider and optionally setting a daily spend budget.
- OpenRouter diagnostics now log non-secret endpoint status for `/credits`, `/key`, and `/activity`, including whether each key type is configured, payload field names, and activity row counts.

### Changed

- OpenRouter balance and spend now render as a single split row, e.g. `Balance $11.16 left` with `Spend today $0.00 / month $0.00` right-aligned; UTC details moved to the tooltip.
- OpenRouter daily spend only renders as a gauge when a daily budget is configured.
- OpenRouter model breakdown now uses the default `/activity` history window, shows up to six models, and labels it explicitly as `Models: last 30 completed UTC days`.
- OpenRouter refreshes before browser-scraped providers so its API-backed tile does not wait behind Claude/Codex page loads.
- Note-only OpenRouter rows, such as empty completed-day model activity, no longer render as empty gauges with `--`.
- Routine successful OpenRouter refresh diagnostics now log at debug level instead of filling the normal log on every refresh.

### Fixed

- OpenRouter `/activity` now uses the management key, matching OpenRouter's current API requirements, instead of incorrectly using the standard inference key and receiving HTTP 403 responses.
- OpenRouter management endpoints are skipped when no management key is configured, with visible tile guidance instead of failed background calls.

## 0.5.2 - 2026-05-03

### Added

- Lifecycle diagnostics: AI Gauge now writes a five-minute heartbeat plus explicit Qt `aboutToQuit` and Python `atexit` log lines with uptime, UI mode, enabled providers, in-flight refreshes, queued providers, next refresh delay, and idle-backoff count. This should make future unexplained exits easier to distinguish from clean quits, OS shutdowns, and mid-refresh process termination.

### Changed

- Settings is now more compact: general/window/provider controls share a shorter tab, GitHub Copilot details live on their own tab, and long helper text was tightened so the dialog fits more comfortably on smaller displays.

## 0.5.1 - 2026-04-30

### Added

- Pace indicator on every active time window: provider tile bars get a thin tick at the elapsed-time position, and compact-view chips get a small downward-pointing notch on the top edge, so quota used vs. elapsed session/weekly/monthly time is visible at a glance.
- **macOS and Linux support.** A new `aigauge.platforms` seam routes per-OS work (app-data directory, secret storage, auto-start) through `WindowsPlatform` / `MacOSPlatform` / `LinuxPlatform` impls. Windows behavior is unchanged.
- **Stats-style menu-bar UI on macOS.** Instead of the floating widget, macOS shows one tinted dot + percent per enabled provider directly in the menu bar (`● 42% ● 78% ● 15%`). Clicking opens the panel as a popover anchored under the menu-bar item; clicking outside dismisses. The pixmap is rendered at 2× DPR for Retina.
- **No-tray fallback on Linux.** Stock GNOME has no system tray; AI Gauge now detects this via `QSystemTrayIcon.isSystemTrayAvailable()`, keeps the floating widget visible, and serves the same Show / Refresh / Settings / Quit menu via right-click on the widget.
- **Cross-platform CI.** `test.yml` now runs on `windows-latest`, `macos-latest`, and `ubuntu-22.04` across Python 3.11 and 3.12. `release.yml` builds per-OS artifacts in parallel and attaches them to a single draft release.
- `build.sh` for macOS / Linux PyInstaller builds. On macOS it injects `LSUIElement=true` into the bundle's `Info.plist` so the `.app` runs as a menu-bar agent without a Dock icon.

### Changed

- The `start_with_windows` config field is renamed to `start_at_login` (with automatic migration); the matching Settings checkbox now reads "Start at login". UI strings that called out "Windows Credential Manager" now say "system keychain".
- Per-OS secret backends: macOS uses Keychain via `keyring`, Linux uses Secret Service via `keyring`, Windows keeps the existing DPAPI sidecar for cookies (Credential Manager's blob limit is too small for ChatGPT JWTs).
- Per-OS auto-start: LaunchAgent plist on macOS, `~/.config/autostart/ai-gauge.desktop` on Linux, the existing Run-key entry on Windows.
- App-data directory is now per-OS: `~/Library/Application Support/ai-gauge` on macOS, `$XDG_CONFIG_HOME/ai-gauge` on Linux, unchanged `%APPDATA%/ai-gauge` on Windows.

### Fixed

- Copilot monthly resets are now anchored to UTC midnight on the first of the month, so countdowns near month end match GitHub's reset boundary instead of local midnight.
- Claude scrapes now retry transparently after a wake-from-sleep timeout instead of giving up on the first attempt: the headless scraper retries up to twice on `timeout`, `page failed to load`, or null-extractor results, so a cold network on the first refresh after resume usually succeeds on the retry instead of surfacing as `error · timeout`.
- Claude usage panel that hadn't finished rendering when the extractor ran is no longer misclassified as the idle 0%/0% state. The signed-in-but-empty heuristic now requires positive evidence the usage panel rendered (the "Plan usage limits" header in the body) before declaring idle, and `ClaudeProvider` retries the whole scrape once on a transient layout-error result so the second attempt sees the populated rows.

## 0.5.0 - 2026-04-28

### Changed

- Renamed the project from `usage-view` to `ai-gauge`. Package import is now `aigauge`, console script is `ai-gauge`, app data lives under `%APPDATA%/ai-gauge/`, and the standalone build outputs `dist/ai-gauge/ai-gauge.exe`. Existing installs that wrote to `%APPDATA%/usage-view/` are not migrated automatically — copy the folder over if you want to keep history and saved sessions.

### Added

- Continuous integration on GitHub Actions: pytest runs against Python 3.11 and 3.12 on Windows for every push and pull request, gated by a `tools/check_versions.py` script that fails the build if `pyproject.toml`, `src/aigauge/__init__.py`, the README, and the changelog drift out of sync.
- Automated release workflow: pushing a `v*` tag spins up a Windows runner that runs the tests, builds the standalone `.exe` via `build.ps1`, zips `dist/ai-gauge/`, computes a SHA256, and attaches both files to a draft GitHub Release for review.
- Issue templates (bug report, provider layout broken, feature request) and a `CONTRIBUTING.md` with dev setup, test, and PR expectations.
- URL allowlist on the embedded sign-in browser: navigation is restricted to the auth-related domains for Claude and ChatGPT (and their known OAuth/identity hops). Off-allowlist navigations are blocked, hardening the embedded browser against open-redirect abuse on either provider's auth flow.

### Security

- `secret_storage` now refuses to write secrets on non-Windows hosts instead of silently falling back to a plaintext `secrets.dat` (an artifact of early cross-platform dev). Reads still succeed where possible so existing test fixtures keep working, but production write paths require DPAPI.
- `SECURITY.md` now spells out that DPAPI encryption is per-user, not per-process: any code running as the same Windows user can decrypt `secrets.dat`.

## 0.4.3 - 2026-04-28

### Added

- Added a single-instance lock so a Startup launch and a manual launch cannot run two full app trees at the same time.

### Fixed

- Fixed completed Claude/Codex offscreen scrapes retaining their `QWebEnginePage` owner, which could leave QtWebEngine renderer processes accumulating after repeated refreshes.
- Cleaned up cookie-verification WebEngine pages and OAuth popup windows more aggressively after they finish or close.

## 0.4.2 - 2026-04-28

### Added

- Persisted compact pill mode: header collapse button shrinks the panel to a single row of provider chips showing session percent, with severity-tinted fills and a one-click expand back to the full panel. Mode is saved across restarts.
- Indeterminate "skeleton" bars on provider tiles before the first snapshot arrives so a fresh launch shows animated placeholders instead of empty rows.
- Provider diagnostics logging: Claude/Codex page classifications (logged out, security verification, empty signed-in usage, layout changed, load failed) and Copilot API failure modes (missing PAT, unresolved username, HTTP errors with request id, unexpected exceptions) now emit structured log lines for support triage.

### Changed

- Scheduled and manual refreshes now keep existing tile values visible and just dim the tile while a new scrape runs, instead of resetting rows to `loading...`. Each tile un-dims as its own snapshot arrives.
- Settings dialog is now non-modal: it can stay open while the user interacts with the main panel or browser, and clicking the status panel raises the existing Settings window instead of opening a second one.
- Always-on-top suspension is reference counted, so overlapping suspensions (Settings + cookie paste + sign-in) no longer race and leave the panel pinned.
- Tile severity color bands shifted to 95% / 80% / 60% thresholds with a paired darker tone used for compact-mode chip fills so colors stay readable under white text.
- Claude's own "Can't reach Claude" interstitial is now reported as a load failure instead of a layout-changed scraper error.

## 0.4.1 - 2026-04-28

### Changed

- The next refresh is now pulled forward to shortly after a known reset time so the panel updates promptly when a session/weekly limit rolls over, instead of showing 100% for the full idle backoff.
- Fresh installs no longer auto-open Settings; the panel just shows provider tiles in their auth-required state with a Sign in button.
- Settings and cookie paste dialogs no longer pin themselves above other windows. While any of Settings, cookie paste, or sign-in is open, the main panel also drops out of always-on-top so the user can switch to their normal browser to grab a cookie or click a magic-link email.

## 0.4.0 - 2026-04-28

### Changed

- Scheduled refreshes now keep the existing tile values visible until fresh results arrive; only manual refreshes clear rows to `loading...`.
- The header now shows a live countdown to the next scheduled refresh instead of only the configured cadence.
- Widget width is fixed at the compact 340 px panel size, and height is clamped on load/refit/save so cross-monitor DPI changes cannot stretch the panel into an oversized banner.
- Claude signed-in pages with no usage yet now show idle zero rows instead of a layout-changed error.
- Codex signed-in pages with no usage yet now show idle zero rows instead of a layout-changed error.
- Claude weekly resets like `Mon 6:00 PM` are now parsed and displayed even when weekly usage is still 0%.
- Codex signed-out pages are now classified as `not signed in` instead of `layout changed` when the login page omits the expected link selector.
- Cloudflare / `Just a moment...` interstitials are now classified as authentication required instead of a generic layout error.
- Unused limits with no parsed reset time now consistently show `idle` instead of a blank reset label.
- Provider errors now log a compact sanitized raw payload summary.
- GitHub Copilot PATs now live only in Windows Credential Manager; legacy fallback-file PATs are migrated when possible. Settings can clear the saved PAT.
- PyInstaller builds now use `--clean` and `--noupx` to avoid stale bundles.

### Notes

- An external-Chrome / CDP refresh path was prototyped during 0.4 development and removed before release: Cloudflare's `cf_clearance` cookie expires every ~30 min on bot-flagged sessions and cannot be renewed from a non-interactive Chrome process, regardless of launch flags. Claude and Codex continue to refresh through an in-process `QWebEnginePage` whose cookie jar is kept warm between scrapes.

## 0.3.1 - 2026-04-27

### Changed

- Re-enabled provider tiles now return to the stable Claude, Codex, Copilot order instead of appearing at the bottom until restart.
- Refresh now immediately clears visible provider rows back to `loading...` so manual refreshes show progress while scans run.
- Codex's short-window usage label now displays as `Session` to match Claude.

### Fixed

- Existing in-flight Codex history using the old `5 hour` label is migrated to `Session`.

## 0.3.0 - 2026-04-27

### Added

- Verify-on-paste: pasting a cookie now loads the actual usage page and reports back whether the session authenticates, naming likely causes when it doesn't.
- Clickable error labels: provider tiles now show short reasons (`error · timeout`, `error · layout changed`, etc.) and open a details dialog with the raw payload, copy button, and shortcut to the log folder.
- Rotating diagnostic log at `%APPDATA%/ai-gauge/ai-gauge.log` with an "Open log folder" button in Settings.
- Refresh-cadence indicator in the widget header showing active vs idle mode and the current interval.
- Loading state for provider tiles before their first snapshot arrives.
- Per-period usage history: peak percent reached for each session/weekly/monthly window is appended to `history.jsonl` on rollover, with in-flight state in `current.json`. No UI yet — pure background record-keeping.

### Changed

- Widget panel height now auto-fits the visible providers — toggling a provider off shrinks the panel rather than leaving blank space.
- Copilot monthly quota changes now update the displayed metric immediately instead of waiting for the next refresh.
- Provider settings now include a hint and tooltips explaining that unchecking hides the tile from the panel.
- Cookie paste verifies the imported session before accepting it.

## 0.2.0 - 2026-04-27

### Added

- Added app version display in the widget header, tray tooltip, and Qt application metadata.
- Added adaptive auto-refresh with separate active and max intervals.
- Added opt-in Start with Windows support.
- Added Copilot plan/quota selection with common plan defaults and a Custom fallback.
- Added a PyInstaller launcher for reliable packaged builds.

### Changed

- Codex and Claude cookie setup now prefer full `Cookie:` request headers and validate provider-specific auth cookies.
- Copilot usage now tracks included premium requests consumed instead of billable overage.
- Claude and Codex unused limits now show `idle` instead of misleading future reset times.
- Provider refreshes now run sequentially to reduce peak background CPU, memory, and network usage.
- Default max auto-refresh interval is now 60 minutes, with 5 minutes as the active cadence.

### Fixed

- Fixed ChatGPT split-cookie handling.
- Fixed Claude weekly-limit extraction.
- Fixed PyInstaller one-file relative-import crash.
- Filtered noisy QtWebEngine console messages from third-party pages.
- Cleaned up offscreen WebEngine views after scrape completion.

## 0.1.0 - 2026-04-27

### Added

- Initial Windows tray/widget app for Claude.ai, ChatGPT Codex, and GitHub Copilot usage.
- Added DPAPI-backed secret storage, persistent WebEngine profiles, settings dialog, cookie paste flow, and test coverage for core helpers.
