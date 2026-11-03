# Console session renewal

**Status:** measuring. The session cookie watcher is built; the fix waits on
its log.

**Trigger to build the fix:** after a fresh Claude API sign-in, 48 hours of
normal running log a `session cookie seen` line for `sessionKey` on the
`claude_api` profile and no `session cookie renewed` line for it.

**Size:** 1 to 3 commits, one per step under "Recommended fix", each verified
by the watcher before the next.

## Problem

The Claude API tile signs out about 7 days after each sign-in, while the
user's own Chrome stays signed in to the Console for weeks.

- The Console's session cookies (`sessionKey`, `sessionKeyV3`) carry a 7-day
  expiry (P1 in `claude-api-credit-plan.md`, under "P1 result").
- Sign-in was 2026-10-23 about 14:56 (the `claude_api` profile folder's
  creation time). The app read the billing page about every 5 minutes until
  2026-10-29 14:04. The machine was then idle until 2026-11-02 20:28, when the
  Console answered 403 `account_session_invalid`.
- If AI Gauge's checks extended the session, the last one on Oct 29 would have
  pushed expiry to about Nov 5. The session died before Nov 2.
- The organization has no Session security setting (its Settings >
  Organization page has no such section). Anthropic's help article on session
  security says that without a cap "sessions remain active as long as the user
  stays active".

So the Console renews the session for a real browser and not for AI Gauge's
background check.

## Ruled out

- **Cookie shape at import.** `import_browser_cookies` clears the profile's
  cookies first and keeps host-only versus domain cookies apart
  (`src/aigauge/webview/cookies.py:223-251`).
- **Keyring re-hydration overwriting a renewed cookie.** Hydration skips any
  profile that already has a Cookies file (`cookies.py:290-358`).
- **Persistence.** The profile forces persistent cookies
  (`src/aigauge/webview/profile.py:41-43`).
- **A service worker answering from cache.** The Console's bundle unregisters
  stray service workers (its `WorkerUpdater`), so every check reaches the
  server.
- **Renewal in page code.** The session cookies are HttpOnly, so only a
  server `Set-Cookie` can renew them. The bundle reads `sessionKeyLC` and
  `sessionKeyV3LC` only as markers that the session changed.

## Current wiring

- The scraper builds a `QWebEngineView` that is never shown
  (`src/aigauge/webview/scraper.py:116-122`), so the page is hidden to the
  Page Visibility API. It loads `CLAUDE_API_BILLING_URL`
  (`src/aigauge/providers/claude_api.py:29`) with `wait_ms=3000`
  (`claude_api.py:682`).
- The extractor fetches `/api/organizations` and
  `/api/organizations/{id}/prepaid/credits` (`claude_api.py:114-142`). A
  check takes about 6.5 seconds.
- `_cleanup` stops the view and sets the page lifecycle to `Discarded`
  (`scraper.py:343-367`). No `pagehide` work gets a chance to run.
- A visible billing page in Chrome also calls
  `/api/event_logging/v2/batch`, `/prepaid/credit_expiry`, `/invoices` and
  `/contracts/postpaid` (resource timing, 2026-11-03).

## Hypotheses

1. **Activity the check never produces.** The Console extends the session
   when it records user activity, and the event-logging batch is sent on a
   timer, on visibility change or on `pagehide`. A hidden page discarded
   after 6 seconds sends none. This is the lead.
2. **Rate-limited renewal on a request the check skips.** The server
   re-issues the cookie at most once a day, and only on a request the
   extractor's two fetches do not make.
3. **A `.claude.com` cookie that sign-in drops.** `_provider_cookies` keeps
   only `.platform.claude.com` cookies
   (`src/aigauge/webview/external_login.py:116-131`). If renewal checks a
   device cookie on `.claude.com`, AI Gauge never sends it.

## Measurement (built)

`src/aigauge/webview/session_cookies.py`, installed on every profile in
`get_profile`, logs each session cookie the first time it is seen and again
whenever its expiry moves by an hour or more. It never logs values. It also
logs any cookie with "session" in its name, so `activitySessionId` and a
future rename show up.

Read it with: `grep "session cookie" %APPDATA%\ai-gauge\ai-gauge.log`.

- **`renewed ... name=sessionKey` lines during normal checks:** AI Gauge's
  checks do renew. The October expiry then needs another cause. Look for
  where the renewed lines stop.
- **Only `seen` for `sessionKey` after 48 hours:** the checks do not renew.
  Build the fix.
- **Two `sessionKey` entries with different domains:** a duplicate cookie.
  Note which one the server keeps setting.

The `claude` and `codex` profiles get the same lines, which shows whether
their sessions slide too.

## Recommended fix

Build in order and stop at the first step that makes the watcher log a
`sessionKey` renewal.

1. **Daily keep-alive visit.** Once every 24 hours, the Claude API check
   keeps the page alive for about 60 seconds with `page.setVisible(True)`
   (available in the installed PyQt6) and lifecycle `Active`. It then
   navigates to `about:blank` before discarding the page, so queued activity
   is sent. Every other check stays as it is. Tests the first hypothesis.
2. **Keep the `.claude.com` cookies at sign-in.** Import the Console-related
   cookies from `.claude.com` as well, without Cloudflare's `__cf_bm`. Tests
   the third hypothesis.
3. **Fallback if neither renews.** Show the sign-in expiry in the tile's note
   and warn a day ahead. This makes the weekly sign-in visible rather than
   fixing it.

## Alternatives considered

- **Expiry warning only.** Leaves a weekly manual sign-in. Kept as the
  fallback.
- **Keep the sign-in Chrome profile so re-signing in takes one click.** This
  stores a full Google session on disk to save a weekly click, and it is still
  manual. Rejected unless renewal turns out to be impossible.
- **Check more often.** Checks every 5 minutes already failed to renew, so
  frequency is not the variable.
- **Admin API key.** The Admin API has usage and cost reports, but no credit
  balance route that we know of. Rejected.
- **Reuse the claude.ai session.** It is a different site, and its cookie is
  never sent to platform.claude.com (`claude-api-credit-plan.md`, "The
  claude.ai session cannot read it"). Rejected.

## Risks

- The keep-alive sends real activity events for the user's account, the same
  as a manual visit. The cost is about 60 seconds of a background page once a
  day.
- **Cookie rename.** The Console bundle has `consoleSessionKey`,
  `consoleSessionKeyLC` and `consoleSessionKeyV3LC` ready, behind a selector
  that still returns the old names. When the switch happens, the `claude_api`
  `CookieSpec` (`src/aigauge/providers/registry.py:209`) and sign-in readiness
  need the new names. The watcher will show the new names in the log first.

## Test strategy

- Watcher: `tests/test_session_cookies.py`.
- Keep-alive: unit tests for the 24-hour gate and for the visibility,
  lifecycle and navigation calls against a fake page. The real check is a
  source run after a fresh sign-in, with the watcher log showing a
  `sessionKey` renewal within the hour after the first keep-alive visit.
