# Console session renewal

**Status:** step 3 works (read 2026-11-17), shipping in 0.8.7. The session signed in at 22:05
EST on 2026-11-09 was re-issued by the server at 17:06 EST on 2026-11-15,
during an ordinary check, 29 hours before it would have ended. Before the
fix the app's session ran its full 7 days with no re-issue (step 2). Steps 3
and 5 built 2026-11-09 (`0e3e444`, `1797d5a`, `a41b27f`). One renewal seen;
the next is due before 2026-11-22 11:06 EST. No expiry on screen.

**Trigger to build the fix:** met 2026-11-09 (step 2 result below).

**Size:** 2 commits, steps 3 and 5 under "Recommended fix". Step 3's verdict:
renews (see "Result" under step 3).

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
   - **Overnight confirmation (same process, 2026-11-05 21:28 to 2026-11-06
     08:28):** 79 `claude_api` checks, all `scrape ok` with `orgs_status=200`,
     no warnings, and no `claude_api` `sessionKey` line in 11 hours. claude.ai
     re-issued its `sessionKey` set again at 03:37, about 6 hours after 21:33.
     Codex: 79 of 79 checks ok, no deferred sign-out.

**Chrome check (DevTools, 2026-11-06 about 14:08Z): Chrome is not renewed
either.**

- Chrome's `sessionKey` and `sessionKeyLC` are on `.platform.claude.com`,
  expiring 2026-11-10T01:59:44.821Z. That is exactly 7 days after
  2026-11-03T01:59:44Z (2026-11-02 20:59 EST), when `routingHint` on
  `.platform.claude.com` was set with the same millisecond (30-day expiry).
  Billing-page visits at 13:33Z and 14:08Z, about 84 hours into the session,
  did not move it. The visits did set `GAESA`, `activitySessionId`,
  `lastActiveOrg` and the analytics cookies.
- Chrome has no `sessionKeyV3` on the Console. The app's sign-in copy
  (`_dd_s_v2`, `activitySessionId`, `anthropic-device-id`, `routingHint`,
  `sessionKey`, `sessionKeyLC`, from the `cookie hydration
  provider=claude_api` line) matches Chrome's auth cookies, so the V3 idea
  is dead.
- The only HttpOnly cookies on `.claude.com` are Cloudflare's `__cf_bm` and
  `_cfuvid`, and `claude_visited` (a marketing-site marker). There is no auth
  or refresh cookie there for sign-in to have dropped. A second, host-only
  `routingHint` was set at 2026-11-03T02:55:24Z.
- **Step 2 is rejected** (see Alternatives). Chrome, with every cookie and
  real activity, gets no re-issue in the first 84 hours either.

**The October timeline does not rule out a last-day renewal.** Sign-in
2026-10-23 14:56, last check 2026-10-29 14:04, expiry 2026-10-30 14:56: the
checks stopped 24 hours 52 minutes before expiry, 52 minutes before the final
day began. The "already rules it out" line in the source-run result above is
wrong on this point. What the evidence now allows:

4. **Renewal only near expiry.** The server re-issues only when less than
   about 3.5 days remain (nothing so far has had less). October's checks
   never reached the last day because the machine went idle.
5. **No renewal for anyone.** The Console caps sessions at 7 days. Chrome
   "stays signed in" only by signing in again, either silently (for example
   Google's automatic sign-in for returning users) or with a click nobody
   remembers. Chrome's own session started 2026-11-02 20:59 EST, the same
   evening the app's session died, which fits.

2. **Watch the current session's last day (no code).** Separates 4 from 5.
   Keep the source run going, machine awake, and do not sign in to the
   Console again in the app before the session lapses: a fresh sign-in
   resets the clock. The app's session expires about 2026-11-09 21:00 EST
   (sign-in between the 20:28 403 and the 21:19 run start on Nov 2; its
   cookie file is locked while the app runs, so the exact time is unread).
   Chrome's expires 2026-11-09 20:59 EST.
   - **App:** `grep -E "profile=claude_api name=sessionKey" ai-gauge.log`
     from 2026-11-08 21:00 EST onward. Since `073da2a` the log keeps five
     1 MB files, about 140 hours at the current volume, so the whole window
     survives.
   - **Chrome, during Nov 9 before 20:59 EST:** open the billing page and
     read `sessionKey`'s Expires in DevTools. Moved from
     2026-11-10T01:59:44Z means a near-expiry renewal.
   - **Chrome, after 20:59 EST Nov 9 if not renewed:** open the Console.
     Login page, or straight in with a new `sessionKey`?
   - **Reading the outcome.** A `claude_api` `sessionKey` line in the last
     day: hypothesis 4. The app already renews while the machine is awake,
     and October was the idle machine. Remove the keep-alive and build step 3
     for sessions that lapse while the machine is off. No line and Chrome
     shows the login page: hypothesis 5 with a click. Build step 3 and
     remove the keep-alive. No line and Chrome gets in without a click:
     hypothesis 5, silent. Note which cookies changed. The app could only
     copy it by keeping a Google session (rejected below), so build step 3.
   - **Result (2026-11-09): the app is not renewed, Chrome is.**
     - **App.** No `claude_api` `sessionKey` line in any of the five logs
       (2026-11-05 onward). Last good check 21:02:08 EST, first 403
       `account_session_invalid` at 21:07:32, so the session lasted 7 days to
       within minutes of sign-in. The machine slept 2026-11-08 21:18 to
       2026-11-09 08:20 EST (System log), then the app made 110 good checks
       on Nov 9, the first at 08:22:13, plus keep-alive visits at 15:14 and
       19:24. None re-issued anything but `activitySessionId` and
       `intercom-session-lupk8zyo`.
     - **Chrome.** At 21:16 EST it went straight into the billing page,
       `/api/organizations` 200, no redirect. Its `sessionKeyLC` and a new
       `sessionKeyV3LC` (absent on Nov 6) both expire
       2026-11-16T08:22:37.741Z (read with `cookieStore.get`, which shows
       expiry but not HttpOnly cookies). The machine was asleep until 08:20:56
       EST, so the cookie was set after that, most likely at 08:22:37: 163
       hours of life, where the Nov 2 sign-in got exactly 168. claude.ai's
       renewals in the app have the same shape (28 days minus 6 hours, every
       `renewed` line since Nov 5). So this was a renewal, not a sign-in,
       24 seconds after an app check that had the same time left and got
       nothing.
     - **Not a sign-in (Chrome history).** No visit to any Claude or Google
       sign-in page between 08:15 and 08:35, and no Anthropic email in Gmail
       for Nov 7 to 10. Chrome's Console visits since Nov 6: billing 08:33
       and dashboard 09:08 on Nov 6 (the DevTools check), then nothing until
       a Google sign-in at 17:38 Nov 9 ending on
       `platform.claude.com/oauth/code/callback`, the page Claude Code's login
       shows its code on. That one set `GAESA` (expires 2026-12-09T22:38:14Z)
       but not the session cookie. So the 08:22 renewal most likely came from
       a Console tab left open since Nov 6, calling the API in the background
       when the machine woke. Chrome restarted about 20:05 Nov 9, so its
       session file cannot confirm the tab.
     - **Verdict.** Hypothesis 5 is out: the Console does renew, near the end
       (none at 84 hours, one at about 155). Hypothesis 4 holds for Chrome
       but not for the app, so the app lacks something Chrome sends.

6. **Browser identity.** The app sends a fixed
   `Chrome/130.0.0.0` user agent (`src/aigauge/webview/profile.py:18-22`,
   applied at `profile.py:47`) from a Chromium 140 engine (Qt WebEngine
   6.11.1), so its user agent and its `Sec-CH-UA` client hints disagree, and
   neither matches the Chrome 157 (`Google Chrome/157, Chromium/157`) the
   Console session was signed in from (external sign-in, then
   `import_browser_cookies`). Renewal may be refused for a request that does
   not look like the browser that owns the session, while plain reads are
   still served. It fits claude.ai, which renews in the app every 6 hours and
   is the one provider signed in inside the app (`cookie hydration
   provider=claude stored=False`), so its session was born under the identity
   it is checked with. Codex renews on every load regardless.

3. **Look like the sign-in Chrome on the `claude_api` profile.** One commit,
   one try. It bundles the two differences left between the app and the
   Chrome tab that renewed, so one session gives an answer. Splitting them
   would cost a full session per variable, and the goal is renewal, not
   knowing which part did it.
   - **Cookies.** Keep the `.claude.com` cookies at sign-in
     (`_provider_cookies`, `src/aigauge/webview/external_login.py:116-131`).
     The Nov 6 rejection rested on Chrome holding no auth cookie there, but
     a device or bot-check cookie can still gate renewal, and it costs a
     filter change.
   - **Identity.** At external sign-in, call `Browser.getVersion` on the CDP connection
     that already reads the cookies (`src/aigauge/webview/external_login.py:327`)
     and store `userAgent` with them.
   - In `get_profile`, for `claude_api` only, set that user agent and the
     matching client hints (`QWebEngineProfile.clientHints()`, Qt 6.8+:
     brands `Google Chrome` and `Chromium` at the browser's major version,
     full version list, platform `Windows`). Fall back to the current UA when
     nothing is stored. Leave `claude` alone: its session was born under the
     current identity and renews.
   - **Built.** `0e3e444` adds `src/aigauge/webview/browser_identity.py`
     (version from the highest dotted-quad folder beside the exe on Windows,
     `Info.plist` on macOS, `--version` on Linux) and applies it in
     `get_profile` for `_SIGNIN_IDENTITY_PROVIDERS` (`profile.py:26`). It
     reads the installed browser at launch, so it needs no sign-in.
     `1797d5a` adds `cookie_domain_in_scope` (`cookies.py:196`), used by both
     `_provider_cookies` and `import_browser_cookies`: the importer had its
     own copy of the old filter and would have dropped `.claude.com` again.
     Only `claude_api` gains cookies (`claude` and `codex` sit on apex
     domains). Storage is the DPAPI file `secrets.dat`, no size limit.
   - **The cookie half needs a fresh sign-in**, since cookies are only
     captured then. John signed in at 21:38 on 2026-11-09 with the old
     filter, so: restart on this branch, sign out of the Claude API tile,
     sign in again.
   - **Check after restart:** `browser identity provider=claude_api
     browser=chrome version=157... applied=True`, and the `scrape profile
     provider=claude_api` line showing `Chrome/157.0.0.0`. **After the
     sign-in:** `external sign-in captured provider=claude_api cookie_names=`
     lists more than the seven `platform.claude.com` names (`GAESA`,
     `_dd_s_v2`, `activitySessionId`, `anthropic-device-id`, `routingHint`,
     `sessionKey`, `sessionKeyLC`).
   - **Test started 2026-11-09 22:05 EST** (frozen build from this branch,
     launched 22:03). Restart logged `browser identity provider=claude_api
     browser=chrome version=157.0.8103.39 applied=True` and `Chrome/157.0.0.0`
     on `scrape profile`; no keep-alive. Sign-in captured 11 cookies, adding
     `__cf_bm`, `__ssid`, `_cfuvid` and `ajs_anonymous_id`. `sessionKey`
     expires 2026-11-17T03:05Z (Mon Nov 16 22:05 EST). Checks `ok` since.
     Verdict: still signed in after that time, or a `session cookie renewed
     profile=claude_api name=sessionKey` line, means it renews. The machine
     must be awake with the app running for part of the last day.
   - **Result: renews (read 2026-11-17).** At 17:06:45 EST on 2026-11-15,
     139 hours in, an ordinary scheduled check (`scrape start` 17:06:41, `ok`
     17:06:46, no sign-in) received a new `sessionKey` and `sessionKeyLC` on
     `.platform.claude.com` and new `sessionKeyV3` and `sessionKeyV3LC` on
     `platform.claude.com`, all expiring 2026-11-22T16:06Z (Sun Nov 22 11:06
     EST). Chrome renewed between 84 and 155 hours, so 139 sits in its range.
     Checks stayed `ok` past the old expiry, through 22:31 on Nov 17. The line
     reads `session cookie seen`, not `renewed`: the app restarted at 08:35 on
     Nov 13, the stored `sessionKey` logged nothing at that restart, so the
     tracker had no earlier expiry to compare. The grep under "How to read it"
     matches it anyway. Re-issued cookies run 162 hours, not 168; `claude`
     re-issues fall 6 hours short of 28 days the same way. `secrets.dat` still
     holds the sign-in copy, expired since Nov 17, so after a first renewal a
     hydrate from the keyring (profile with no Cookies file) signs out.
   - **Known gaps.** `hydrate_all_from_keyring` re-sets stored cookies as
     `.platform.claude.com` domain cookies when a profile has no Cookies
     file, so the `.claude.com` ones lose their domain in that case only.
     Brave on macOS reports Brave's own version in `Info.plist`, not the
     Chromium major. Neither applies to this machine.
   - **How to read it.** `grep -E "profile=claude_api name=sessionKey"
     ai-gauge.log` as the session's last day comes round, machine awake.
     Chrome renewed between 84 and 155 hours, so no line before 84 hours
     says nothing. A line: done. No line by the end: stop trying, and
     accept a weekly sign-in (see "If step 3 fails").
4. **Expiry on the tile: dropped** (John, 2026-11-09: a sign-in expiry on
   screen is not wanted). The signed-out state at expiry already exists
   (`snapshot auth_required`) and stays the only signal. If a reminder is
   ever wanted, the page can read the expiry with
   `cookieStore.get('sessionKeyLC')`, which moves when the server renews.
5. **Remove the keep-alive.** Step 1 showed activity does not renew, and its
   24-hour gate fired twice on Nov 9 (15:14 and 19:24), so the in-memory
   gate does not survive whatever re-created the provider. Removing it with
   step 3 keeps that test to one variable.

**If step 3 fails.** Accept a weekly sign-in, with nothing on screen until
the tile signs out. The one change worth considering then is making that
sign-in quicker: today the sign-in browser profile starts empty, so every
sign-in is a full Google sign-in. Keeping that profile makes it one click,
at the cost of a Google session stored in the app's folder (see
Alternatives). John's call at that point.

## Alternatives considered

- **Expiry warning on the tile.** Rejected by John 2026-11-09.
- **Sign in to the Console inside the app (embedded window, email code).**
  No code, and the session is born under the app's identity, as claude.ai's
  is. Rejected as the main fix: Google sign-in does not work in an embedded
  browser, and it would not help if the Console's gate is "looks like a
  current Chrome" rather than "matches the sign-in browser". A cheap side
  test if step 3 fails.
- **Raise the UA for every profile.** Would change `claude`'s identity under
  a session that renews today. Rejected: scope to `claude_api`.
- **Keep the `.claude.com` cookies at sign-in (was step 2).** Rejected
  2026-11-06 as a fix on its own: Chrome holds no auth cookie on `.claude.com`
  (only Cloudflare's and `claude_visited` are HttpOnly there). Folded into
  step 3 on 2026-11-09, since renewal only happens near the end and the
  84-hour Chrome check could not test it.
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
- **Hypothesis 6 rests on one Chrome renewal.** The missing ingredient could
  instead be a cookie only Chrome holds or a long-open Console tab. Step 3's
  verdict arrives only near the end of a session, so step 4 is built
  regardless.
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
