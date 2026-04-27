# Changelog

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
- LoginWindow and CookieDialog share the same session-verify helper.

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
