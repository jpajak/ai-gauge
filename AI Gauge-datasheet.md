# Product Data Sheet: AI Gauge

## Product Summary

AI Gauge is a local desktop utility for monitoring AI service usage across Claude.ai, ChatGPT Codex, GitHub Copilot, and OpenRouter. The implemented app runs as a PyQt6 desktop application with a floating widget on Windows/Linux and a menu-bar item on macOS. It shows provider usage percentages, reset timing, account balance/spend details where available, and refresh status without using a hosted backend or telemetry service.

## Primary Users

- Individual AI power users who pay for multiple subscriptions and want a compact local view of quota/balance status.
- Developers or technical users using Claude, Codex, Copilot, and OpenRouter enough to care about session, weekly, monthly, or spend limits.
- Users comfortable configuring API keys, GitHub personal access tokens, browser sign-in sessions, pasted cookies, and local diagnostics.

## Core Workflows

- Launch the tray/widget utility on Windows/Linux or menu-bar utility on macOS.
- Enable/hide providers and adjust refresh, opacity, always-on-top, and start-at-login settings.
- Sign in to Claude or ChatGPT Codex through embedded Chromium, paste cookies as a fallback, and manage multiple named Claude/Codex accounts.
- Configure GitHub Copilot with a fine-grained PAT, optional username/billing organization, and a monthly AI credit allowance.
- Configure OpenRouter with an inference key, optional management key, and optional daily budget.
- Refresh usage manually or through adaptive auto-refresh, then inspect tiles, compact chips, error details, and local logs.

## Implemented Capabilities

- Cross-platform desktop app for Windows, macOS, and Linux, packaged with PyInstaller and runnable from source via `ai-gauge`.
- Floating widget on Windows/Linux, compact pill mode, tray/menu actions, no-tray Linux fallback, and native macOS menu-bar popover.
- Provider tiles for Claude, Codex, GitHub Copilot, and OpenRouter.
- Claude usage scraping from `https://claude.ai/settings/usage`, including session, weekly, and optional Claude Design limit.
- Codex usage scraping from `https://chatgpt.com/codex/cloud/settings/analytics#personal-usage`, including session and weekly limits.
- GitHub Copilot AI credit usage via GitHub REST billing summary endpoints for user or organization billing scopes, with a legacy premium-request fallback.
- OpenRouter account/key data via `/credits`, `/key`, and `/activity`, including balance, UTC day/month spend, optional daily budget gauge, and top model activity.
- Adaptive refresh cadence with active and idle intervals, manual refresh, and refresh pull-forward shortly after known reset times.
- Per-period peak history persisted locally in `current.json` and `history.jsonl`; no implemented history UI was found.
- Local diagnostic logging for auth, layout, API, and refresh lifecycle issues.

## Data Inputs and Integrations

- User-entered settings stored under per-OS app-data directories.
- Claude and Codex sessions from embedded Chromium profiles or pasted `Cookie:` headers, with separate storage per named account.
- GitHub Copilot fine-grained PAT stored in the OS credential store; optional username, billing organization, and AI credit allowance values.
- OpenRouter inference key and optional management key stored in the OS credential store; optional daily budget entered by the user.
- External integrations are direct local requests from the app to Claude.ai, ChatGPT, GitHub API, and OpenRouter API. No server-side app backend, public API routes, or web app routes were found in the codebase.
- Secrets use Windows Credential Manager/DPAPI, macOS Keychain, or Linux Secret Service depending on platform.

## Outputs and Artifacts

- On-screen usage dashboard tiles showing percentage used, reset timing, status, and explanatory notes.
- Compact chips and tray/menu-bar indicators with severity colors based on usage thresholds.
- OpenRouter balance, day/month spend, daily budget progress when configured, and model-activity rows.
- Auth-required and error states, clickable error details, and copyable diagnostics.
- Local app config, browser profiles, encrypted/credential-store secrets, diagnostic log file, current period state, and append-only usage history JSONL.
- Release artifacts are OS-specific archives; the running app does not appear to generate user-facing exports.

## Differentiators to Investigate

- Hypothesis: local-only operation with no telemetry or backend may matter to privacy/security-conscious users.
- Hypothesis: combining Claude, Codex, Copilot, and OpenRouter usage in one compact desktop surface may distinguish it from provider-specific dashboards.
- Hypothesis: multiple Claude/Codex account support is useful for users juggling personal/work subscriptions.
- Hypothesis: reset-aware refresh scheduling, pace indicators, and OpenRouter model breakdowns add context beyond raw percentages.

## Marketing-Relevant Constraints

- The app is unofficial and not affiliated with Anthropic, OpenAI, GitHub, Microsoft, OpenRouter, or other providers.
- Claude and Codex scraping depends on provider web page structure; layout, authentication, Cloudflare/security checks, or upstream UI changes can break reads.
- Copilot usage can lag GitHub's upstream reporting by hours and is described as a trailing indicator, not real time.
- Copilot's current usage-based model is tracked as AI credits rather than premium request counts; annual/request-based accounts may still rely on GitHub's legacy premium-request API fallback.
- OpenRouter activity uses the last 30 completed UTC days and excludes the current UTC day; balance and model activity require a management key.
- Same-user local processes can generally decrypt/access stored session tokens or keys through the OS credential model; do not imply process-level isolation.
- No implemented collaboration, alerting, cloud sync, mobile app, browser extension, team dashboard, or export workflow was found.

## Suggested Positioning Angles

- Local desktop monitor for AI subscription usage across several providers.
- Compact quota and spend visibility for users with multiple AI accounts.
- Technical utility for tracking Claude/Codex reset windows, Copilot monthly AI credits, and OpenRouter spend.
- Privacy-conscious angle based on local storage and direct provider requests, with caveats about credential-store threat models.
- Maintenance/support angle around diagnostics for provider layout and API changes.
