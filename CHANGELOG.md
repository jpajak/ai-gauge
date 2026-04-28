# Changelog

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
- Claude Design usage is now hidden by default with an optional Settings checkbox for users who want to track that separate limit.
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
- Rotating diagnostic log at `%APPDATA%/usage-view/usage-view.log` with an "Open log folder" button in Settings.
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
