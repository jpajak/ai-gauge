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

The `loadAllCookies()` baseline in `get_profile` emits nothing in practice.
The first source run (2026-11-02 21:19) logged no `sessionKey` line, although
the Console check succeeded with it. So the watcher only logs cookies a server
sets after launch, and any `sessionKey` line means the server re-issued it.

- **No `sessionKey` line for `claude_api` after 48 hours:** the checks do not
  renew. Build the fix.
- **A `sessionKey` line with an expiry about 7 days after it:** the checks do
  renew. The October expiry then needs another cause. Look for where the
  lines stop.
- **Two `sessionKey` entries with different domains:** a duplicate cookie.
  Note which one the server keeps setting.

The `claude` and `codex` profiles get the same lines. On the first run,
ChatGPT re-issued `__Secure-next-auth.session-token.0` and `.1` with a 90-day
expiry on a single page load, which is why the Codex tile never signs out.
claude.ai did not re-issue `sessionKey`.

### Result (source run 2026-11-02 21:19 to 2026-11-05 19:24)

- **The run is valid.** One process for 70 hours (heartbeat `uptime_s`), and
  the Windows System log has no sleep, hibernate or restart since the run
  began (last boot 2026-10-26, only lock and unlock transitions). In the
  surviving log, 156 of 157 `claude_api` checks returned `result=ok`. The one
  `AbortError` (13:18) recovered on the next check.
- **Log rotation lost the first 54 hours.** `ai-gauge.log` rotates at 512 KB
  with two backups (`src/aigauge/logging_setup.py:33-36`), which holds about
  16 hours at the current volume. The Codex page's console warnings are a
  third of all lines. The surviving window is 2026-11-05 03:36 to 19:24.
- **No `sessionKey` or `sessionKeyV3` line for `claude_api`.** The Console
  set only `activitySessionId` (12-hour expiry, moved on every check) and
  `intercom-session-lupk8zyo` (14 days, on `.claude.com`). Any renewal
  cadence under 16 hours would have logged at least one line in the window.
  A once-a-day renewal inside the lost hours cannot be ruled out from the
  log alone, but the October timeline already rules it out: that session
  died at sign-in plus 7 days after six days of checks.
- **claude.ai now re-issues its `sessionKey`.** Profile `claude` logged
  `sessionKey`, `sessionKeyLC`, `sessionKeyV3` and `sessionKeyV3LC` renewed
  at 09:30 and 15:33, one re-issue each, each moving the expiry 6 hours to
  about four weeks out. That differs from the first run's single page load,
  and it shows the watcher does catch a `sessionKey` renewal when a server
  sends one.
- **Codex is unchanged.** `__Secure-next-auth.session-token.0` and `.1` and
  `oai-client-session-epoch` move on every check, 90 days out.
- **Verdict:** the checks do not renew the Console session. Build step 1.
- **Prediction:** without a renewal, the current Console sign-in (2026-11-02,
  just before the run) expires about 2026-11-09 in the evening.

## Recommended fix

Build in order and stop at the first step that makes the watcher log a
`sessionKey` renewal.

1. **Daily keep-alive visit (built, `5946d92`).** Once every 24 hours, the
   Claude API check keeps the page alive for about 60 seconds with
   `page.setVisible(True)` and lifecycle `Active`. It then navigates to
   `about:blank` before discarding the page, so queued activity is sent.
   Every other check stays as it is. Tests the first hypothesis.
   - The gate is `ClaudeApiProvider._keep_alive_linger_ms`
     (`src/aigauge/providers/claude_api.py`), in memory, so the first check
     after launch is a keep-alive. The hold is `linger_ms` on
     `HeadlessScraper` (`src/aigauge/webview/scraper.py`).
   - Qt ignores `setVisible` until the first `load()` has created the web
     contents, so it is called right after that load. The view is detached
     first so the page's visibility can never reach the offscreen widget. On
     the offscreen platform an attached page did not pop up a window either,
     but the detach stays.
   - **How to read it.** Restart from source on this branch without signing
     in again, so the only `sessionKey` writer is the Console server. Then
     `grep -E "keep-alive|profile=claude_api name=sessionKey" ai-gauge.log`.
     Expect `claude_api keep-alive due`, `keep-alive start ...
     visibility=visible` and, about 60 seconds later, `keep-alive end ...
     visibility=visible event_logging_requests=N`.
     - A `session cookie seen profile=claude_api name=sessionKey` (or
       `sessionKeyV3`) line within the hour after `keep-alive end`, expiring
       about 7 days later: the visit renews. Stop here.
     - `visibility=hidden`: the page was not visible, so the hypothesis was
       not tested. Fix that first.
     - Visible, but no `sessionKey` line within the hour: the visit is not
       enough. If `event_logging_requests=0`, the page sent no activity at
       all, which is worth a look before moving on. Then step 2.
   - **Result (2026-11-05, restart 19:52):** the visit ran as designed
     (`visibility=visible`, 60.2 s) but logged `event_logging_requests=0`,
     and no `sessionKey` line appeared by 20:54. Checks kept returning `ok`.
     Step 1 alone does not renew.
   - **Chrome comparison (same evening):** the billing page in the user's
     Chrome, visible, focused and untouched, sent
     `/api/event_logging/v2/batch` at 3, 17 and 63 seconds, plus Datadog RUM
     (`/api/v2/rum`). It had 188 resource entries at 7.5 s, so a 3-second
     batch would have fitted in the app's default 250-entry buffer: the
     app's zero is real. Its readable cookies include `anthropic-device-id`,
     `ajs_anonymous_id`, `__ssid`, `_dd_s` and `intercom-device-id-lupk8zyo`
     (domains not read).
   - **Open before step 2:** the app's page differs from Chrome's in cookies
     (hypothesis 3), focus (no window, so `document.hasFocus()` is false)
     and drawing (the detached page may produce no animation frames). Next:
     add `hasFocus`, an animation-frame tick count, total resource entries
     and the `/api/` paths called (ids redacted) to the `keep-alive end`
     line, restart once, and pick from the result. No frames: keep a sized
     view attached, and check on real Windows that no window appears.
     Frames but no focus: address focus. Both fine: build step 2.
   - **Hosting fixed (`65b3a5d`, probe `9988621`):** Qt experiments on
     2026-11-05 showed the detached page reports `visible` but draws nothing
     on Claude's pages (0 animation frames, idle callbacks at the hidden
     rate, 0x0 viewport), so the first visit was not a fair test. The
     keep-alive now shows the view with `WA_DontShowOnScreen` (Qt treats it
     as shown; no native window became visible on Windows) and sends a
     synthetic `FocusIn` to its focus proxy, never `activateWindow()`, which
     takes the foreground. The page then draws, is 1280x900 and has focus.
     The `keep-alive end` line now carries `has_focus`, `raf_ticks`,
     `idle_ticks`, `timer_ticks`, `viewport`, `resources` and `api_paths`.
     On the signed-out login page no variant sent `event_logging`, so that
     page cannot stand in for the Console. Next: restart and read the line.
     `event_logging_requests` above 0 with no `sessionKey` line within the
     hour means activity is not enough: step 2.
   - **Fair-visit result (restart 2026-11-05 21:28):** `keep-alive end
     visibility=visible has_focus=True raf_ticks=3241 viewport=1280x900
     event_logging_requests=1`, with the billing page's full call set
     (`invoices`, `prepaid/credit_expiry`, `contracts/postpaid`,
     `payment_methods` and more, plus `/api/v2/rum`), matching the user's
     Chrome tab. No `claude_api` `sessionKey` line by 22:30, while claude.ai
     re-issued its own at 21:33. **Activity on the billing page does not
     renew the Console session.** Hypothesis 1 is ruled out, and so is the
     billing-page half of hypothesis 2. The keep-alive stays until step 2 is
     measured, in case renewal needs both the cookies and activity; remove it
     if step 2 renews without it.

**Before step 2, a one-minute check in Chrome.** In DevTools on the
Console billing page, Application > Cookies > `https://platform.claude.com`:
read the Expires of `sessionKey` and `sessionKeyV3`, and list the cookies
whose domain is `.claude.com`. An expiry about 7 days from now means Chrome
gets the cookie re-issued on a visit. An expiry fixed from an older sign-in
means Chrome never renews it and instead signs in again silently through
another cookie when it lapses, and that cookie is what step 2 must import.
The extension cannot read HttpOnly cookies, so this needs a person.
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
