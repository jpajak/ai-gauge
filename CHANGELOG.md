# Changelog

## Unreleased

### Added

- Optional MiniMax Coding Plan tile showing the 5-hour and weekly limits. It is off by default; turn it on from its Settings page and enter each account's Coding Plan API key and region (International or China). The key is sent only to that region's API host. ([#13](https://github.com/jpajak/ai-gauge/issues/13), thanks [@innoscoutpro](https://github.com/innoscoutpro).)

## 0.8.5 - 2026-10-08

### Added

- Optional Antigravity tile showing the quota that Google's `agy` CLI reports for Gemini and for Claude and GPT models. It is off by default; turn it on from its Settings page. ([#11](https://github.com/jpajak/ai-gauge/issues/11), thanks [@DraftingDreamer](https://github.com/DraftingDreamer).)
- Providers can be put in any order. Drag one by its grip in the Settings sidebar, or press Alt+Up / Alt+Down; the widget and the tray indicator follow.

### Changed

- Settings now lists its pages down the left side. The row of tabs had run out of room, so the window also opens larger.
- Each provider is now turned on or off from its own Settings page. The checklist on the General page is gone, and a provider that is off shows dimmed in the sidebar.
- Removed the obsolete OpenCode browser-session, cookie-paste, and webpage-verification implementation. OpenCode usage now uses only the authenticated Go API introduced in 0.8.4, with a separate API key stored in the system credential store for each configured account. Claude and Codex browser sign-in are unchanged.

### Fixed

- Restored Codex usage refreshes after ChatGPT moved usage settings to `chatgpt.com/settings/usage?tab=overview`. The collector now reads **5-hour limit**, **Weekly limit**, and **% left** from **Plan limits**, converts remaining allowance to usage, and handles reset countdowns such as **6d 10h**. It selects Overview if Analytics opens, ignores unrelated credit discounts and purchased resets, and updates sign-in verification and usage-page links to the new page. ([#11](https://github.com/jpajak/ai-gauge/issues/11), thanks [@innoscoutpro](https://github.com/innoscoutpro) and [@DraftingDreamer](https://github.com/DraftingDreamer).)
- AI Gauge now asks for usage pages in English whatever the system language is. Codex usage failed on non-English systems because ChatGPT translated the page. ([#11](https://github.com/jpajak/ai-gauge/issues/11), thanks [@DraftingDreamer](https://github.com/DraftingDreamer).)
- If the Codex usage page still cannot be read by its labels, AI Gauge takes the percentages from the page's progress bars and shows them without a reset time. ([#11](https://github.com/jpajak/ai-gauge/issues/11), thanks [@DraftingDreamer](https://github.com/DraftingDreamer).)
- A signed-out Claude session is now reported as signed out. It used to show as a page load or retry error. ([#11](https://github.com/jpajak/ai-gauge/issues/11), thanks [@innoscoutpro](https://github.com/innoscoutpro).)
- Browser sign-in now works with Brave installed as a snap on Linux. (Thanks [@innoscoutpro](https://github.com/innoscoutpro).)

## 0.8.4 - 2026-09-19

### Changed

- Replaced OpenCode's expired browser-session scraping flow with its authenticated Go usage API. OpenCode accounts now use masked per-account API-key fields in Settings, store those keys in the system credential store, and report clear missing-key, invalid-key, subscription, network, and response-shape errors.

## 0.8.3 - 2026-09-19

### Changed

- Made session and weekly trend history complete without weakening comparisons. Total estimated cost is now visible by default; capped windows and the current partial window remain visible and labeled in the table and chart, while rolling averages and before/after comparisons continue to use only completed comparable windows.
- Made a fully used Claude weekly allowance explicit: the provider header now shows **limit hit** until reset instead of leaving the state implicit in a 100% bar or showing a burn-rate estimate that is no longer actionable.

## 0.8.2 - 2026-09-18

### Changed

- Made the **By model** table explain both sides of local usage: API-equivalent cost and the underlying token volume. The former output-only columns now show total non-overlapping tokens across uncached input, cache reads, cache writes and output, plus the blended average cost per million tokens. Hover details break out each token category and clarify that repeated cache reads count each time while reasoning tokens are already included in output.

## 0.8.1 - 2026-09-17

### Changed

- Refined the **Trend** view around a rough, usage-weighted ballpark metric. Its everyday view is chart-first: one compact recent estimate sits above raw points and a connected rolling average, with unusually high raw points clipped into hoverable edge markers so they do not flatten the useful trend. Marked limit changes now preserve older history, and **Compare <date>** optionally reveals zero-based before/recent bars, exact-period reference lines, comparison percentages and mixed-signal context. Method and model/cache-mix explanations stay in tooltips.
- Usage details now show concise Claude model names such as **Opus 5**, **Fable 5.1**, and **Haiku 4.5**. Exact model identifiers remain available on hover, and unfamiliar future identifiers are left unchanged rather than guessed.

## 0.8.0 - 2026-09-14

### Added

- Optional local usage tracking reads Claude Code and Codex logs on this computer to show API-equivalent cost next to the quota percentages. It is off by default and turned on in the new **Local usage** Settings tab. Design: [docs/local-usage-cost-plan.md](docs/local-usage-cost-plan.md).
- **Import history** in that tab brings in the usage the logs still hold, without closing Settings. The import runs in the background, can be cancelled, and resumes where it stopped.
- The usage details dialog gains a **Usage and cost** tab with cards for the current session, week and Fable limit (cost, allowance used, cost per 1%), and views by model, by day and over time.
- A **Trend** view answers whether your allowance is going as far as usual: a plain-language verdict based on the median of the last five sessions (or the latest week), a chart of cost per 1% over time, and a note when a different model or cache mix explains a change. Windows it can't compare fold into one line with their reasons. **Limits changed…** marks the date a provider changed its limits, so the comparison starts fresh from there.
- A **Usage details** menu entry opens the dialog before the session ratio has calibrated.

### Changed

- Made **Usage and cost** the default page of usage details when local tracking is enabled. **Session vs weekly** remains available as the secondary tab, and remains the only view when local tracking is off.
- Kept wide model-table columns compact and remembered the last selected view and range.
- Made unavailable model pricing less intrusive: totals stay readable, internal Codex review usage is labeled **Automatic review**, and concise notes say what the displayed cost excludes.
- Kept usage cards aligned when one has no secondary note to show.

### Fixed

- Kept Claude usage refreshing after a weekly limit is reached. Row detection now uses labels and their associated values instead of depending on exact status sentences, so wording such as **Paused until your week resets** and similar minor copy changes no longer make the fully loaded usage dialog time out.
- Kept the current Session and Weekly cards in **Usage and cost** when Claude temporarily omits a row or its reset time at the weekly cap. The dialog now falls back to the last persisted open window while preferring any fresh webpage reading.

## 0.7.8 - 2026-09-12

### Fixed

- Restored Claude refreshes before a five-hour session has started after the usage dialog changed its idle copy from **Starts when a message is sent** to **Starts with your first message**. The new wording is recognized as a fully loaded `0%` Session row, so active Weekly and Fable values are reported instead of leaving the previous snapshot stale after a timeout.
- Stopped Codex rejecting a valid unused weekly limit when the five-hour session already has usage. A Weekly card showing **100% remaining** without a reset time is now accepted alongside an active Session card instead of being mistaken for a partial render, retried, and ultimately shown as an error with stale cached values.

## 0.7.7 - 2026-09-10

### Changed

- Promoted the AI Gauge artwork from a tiny window-chrome detail to a visible part of the app's identity. The full and compact panel marks are larger, Settings now opens with a 56px branded header and version tagline, and the README presents the icon as its public-facing hero artwork.

### Fixed

- Restored Claude usage refreshes after Claude renamed **Plan usage limits** to **Your usage**, **All models** to **This week**, and **Fable** to **Fable this week**. The extractor accepts both the old and new labels, including the new Fable subtitle and reset-time wording, so accounts on either side of the rollout continue to report Session, Weekly, and Fable usage instead of timing out while waiting for the old rows.

## 0.7.6 - 2026-08-26

### Changed

- Moved the refresh status out of the title bar and onto the resize-grip row, where it reads **Updated 1m ago · next 4m** next to the grip and is separated from the provider tiles by a hairline. The title bar keeps the app name, version, and controls; the footer keeps the status visible when **Show header** is off, dims to grey when refreshing is idle, and refreshes immediately when clicked (a drag that starts there still moves the window). Its tooltip gives the exact last and next refresh times.
- Let the full panel be dragged noticeably narrower. The gauge bar's minimum width dropped from 120px to 80px and the metric-label column's floor from 70px to 56px, so a panel showing Claude, Codex, OpenCode, and OpenRouter now bottoms out near 250px instead of ~295px. Labels, gauges, percentages, and reset times all stay on one line at the new floor.
- Shortened OpenRouter's summary row to **Balance $49.96 · Today $0.00 · Month $232.25**. The words *left* and *Spend* said nothing the figures did not, and the row now fits a narrower panel. The full wording moved to the row's tooltip.

### Fixed

- Fixed the panel springing to a taller layout the moment a width drag ended. Tiles only re-flow on release, and the resize floor counted percentage-gauge rows only — so a drag could pass the width where OpenRouter's summary row stacks its spend under its balance, and the row jumped onto two lines as soon as the mouse came up, leaving the panel to be widened again by hand. The floor now includes that row, so letting go settles on exactly what the drag showed.
- Fixed compact view always reserving room for a second row of chips. Its height floor was a fixed 58px — roughly two rows — so a pill wide enough for one row, or one with the header hidden, kept the empty space anyway. The floor is now a single chip row, and extra rows or a visible header add their own height.
- Fixed compact chips being clipped instead of wrapping when the pill narrowed itself, most visibly when **Show header** was turned off.
- Restored OpenCode's missing short-window gauge after its usage page renamed **Rolling Usage** to **5-hour Usage**. Both labels are accepted and continue to use the existing Rolling metric identity and five-hour pacing window.
- Fixed the download growing by roughly 36 MB in 0.7.5. The packaged `ai-gauge-mcp` helper bundled Pillow and pygments, reached through the MCP SDK's HTTP client, which a stdio JSON-RPC server never uses. Excluding them cuts the helper by about 30% (~8.6 MB off every platform's archive). The helper still carries its own copy of the Python runtime that the application folder already ships; sharing that is tracked separately.

## 0.7.5 - 2026-08-23

### Added

- Added an optional local stdio MCP server that exposes sanitized per-account usage, safe account recommendations, usage resources, and cooperative pause guards without exposing cookies, tokens, or provider responses. The integration is disabled by default and publishes no cache until enabled.
- Added per-account MCP pause thresholds in Settings with explicit fixed-account binding for each MCP client profile.
- Release archives now include the `ai-gauge-mcp` helper: inside the `ai-gauge` folder on Windows and Linux, and beside `ai-gauge.app` on macOS. macOS users must clear quarantine on the helper separately from the app bundle (`xattr -dr com.apple.quarantine ai-gauge-mcp`), otherwise Gatekeeper stops it silently when an MCP client launches it.

### Fixed

- Fixed MCP usage becoming stale or unavailable while AI Gauge was hidden in the system tray. Cache publication now uses unique temporary files and retries brief Windows sharing violations, allowing multiple MCP chats to read usage while background refreshes publish new snapshots.
- Configured MCP guards now fail closed for stale, failed, malformed, or ineligible usage data, ignore OpenRouter model-share percentages, and never recommend an account that is already blocked.
- Packaged MCP helpers now complete a real protocol handshake in CI and shut down cleanly after stdio clients disconnect.

## 0.7.4 - 2026-08-18

### Added

- Added an explicit, remembered sign-in-browser choice for Chrome, Edge, Brave, or Chromium. First use asks which installed browser to use, the preference can be changed under **Settings → General**, and the embedded browser remains available as a fallback.
- Added the complete window context menu requested in [#9](https://github.com/jpajak/ai-gauge/issues/9), even when a system tray is available. **Compact view**, **Show header**, and **Always on top** are independent, remembered choices, while Refresh, Settings, window visibility, and Quit remain accessible when the header is hidden.
- Added magnetic corner snapping for the Windows/Linux floating widget. It previews the corner while dragging, detaches if the pointer keeps moving away, and commits an anchor on release that survives content and layout size changes.

### Fixed

- Fixed Claude timing out at the start of a new five-hour session. Claude now renders the idle Session row as **Starts when a message is sent · 0% used** while the Weekly row can still have active usage; the extractor understands that mixed state, along with the equivalent unused-Fable row, instead of waiting forever for the old reset copy.
- Fixed the unpredictable external sign-in flow reported in [#8](https://github.com/jpajak/ai-gauge/issues/8). Chromium process handoffs are followed through the live DevTools connection, and closing an unfinished browser returns to the chooser instead of unexpectedly opening the embedded fallback.
- Fixed embedded Claude sign-in remaining open after a successful login. Verification now recognizes Claude's current **New chat - Claude** title and authenticated app shell, still rejects login pages, and refreshes the provider as soon as the dialog closes.

### Changed

- Reworked sign-in into separate compact chooser, installed-browser waiting, and embedded-browser pages. The primary action names the selected browser, the embedded fallback and its limitations are clearly secondary, and authenticated embedded sessions are recognized automatically. **I'm signed in** remains available for unusual login flows.
- Improved the no-OpenGL diagnostic for browser-backed providers by naming the `AIGAUGE_FORCE_WEBENGINE=1` override for systems where Chromium can obtain a context even though Qt's probe cannot.

## 0.7.3 - 2026-08-16

### Added

- Added a **Square corners** setting. The rounded outline is masked out of the window shape, which is correct everywhere but leaves an aliased edge; square corners skip the mask entirely and are the safer choice on remote desktops that render window masks poorly.
- The collapsed pill can now be resized. Its width follows its own content instead of being pinned to the panel's 340px, the resize grip floats over its corner rather than claiming a footer row, and the header sheds the version, the "Xs ago" stamp, the cadence and finally its name as the pill narrows — down to a 150px floor. One provider no longer means a mostly empty strip.

### Fixed

- Fixed light-grey notches in the widget's four corners on any system using a light theme. Qt erases a top-level widget with the palette's Window brush before `paintEvent` runs, and `paintEvent` only covered the rounded rect, so the corners kept the system theme's colour. Invisible in dark mode, which is why it survived this long.
- Fixed a crash on any session whose X server offers no GLX — a headless X11 display, an XRDP desktop, a VM without a GPU. AI Gauge imported QtWebEngine unconditionally at launch, and QtWebEngine is Chromium: the window itself appeared, then the first automatic refresh drove a page load and Chromium aborted the process. That is the "and then nothing more" in the original report, reproduced 5 runs out of 5. The gauge is plain QtWidgets and needs no OpenGL at all, so WebEngine is now imported on first use and only after a GL probe succeeds. API-key providers (GitHub Copilot, OpenRouter) are unaffected on such machines; providers that scrape through a browser report that they need one rather than taking the app down. Set `AIGAUGE_FORCE_WEBENGINE=1` to skip the probe — worth trying if a machine has working OpenGL that Qt itself cannot reach, where Chromium can still find it on its own.

### Changed

- The one-folder build is ~140 MB smaller (~520 MB to ~380 MB). `--collect-all` on the WebEngine modules was collecting Chromium's debug resource packs and the DevTools front-end (~88 MB, never loaded), plus Qt translations and Chromium locale packs for every language (~52 MB; AI Gauge's own UI is English-only). `tools/prune_bundle.py` now removes them after PyInstaller runs; pass `--keep-localisation` to retain the language files.

## 0.7.2 - 2026-08-02

### Added

- Added a **Show weekly Fable limit** setting for each Claude account, on the Claude tab, surfacing Claude's separate weekly Fable limit as its own gauge alongside Session and Weekly. Plan tier belongs to a subscription rather than to the provider, so the toggle sits per account: one signed-in account can be Max while another is not. It is on by default, including for existing configurations — plans that do not publish the limit report no such row, so those accounts are unchanged rather than showing an empty or wrong gauge. AI Gauge had been quietly watching two thirds of a Max subscription.
- Added Node-backed tests that run the injected Claude usage extractor against a mocked usage dialog. The script only ever ran inside the webview before, so row-matching regressions could not be caught by the test suite. These tests skip when Node is unavailable.

### Fixed

- Usage rows are now matched only where a label actually heads a row, instead of anywhere the label text appears. Claude's usage dialog describes models in prose banners, and a container holding such a banner next to a different row could otherwise be read as that model's row, reporting its neighbour's percentage.

## 0.7.1 - 2026-07-28

### Added

- Added the AI Gauge logo as the global runtime window/dialog icon and as the embedded Windows, macOS, and Linux packaging icon. AI Gauge had carefully measured every percentage, then somehow forgot 100% of its own branding and arrived dressed as Python.
- Added packaged PNG, ICO, and ICNS icon assets generated from the canonical application artwork.
- Replaced the Windows/Linux severity dot in the system tray with the AI Gauge logo. Detailed usage remains visible in the tray tooltip and widget, while the tray now consistently represents the application itself.
- Added the logo directly to the full and compact custom widget headers. The frameless main window has no native title-bar icon slot, so it now renders its own instead of relying on the global window icon.

### Fixed

- Prevented the main widget from flashing when Settings opens by leaving its topmost state intact for the app-owned dialog. External connection dialogs change topmost state without recreating the window.

## 0.7.0 - 2026-07-25

### Added

- Added support for multiple named OpenCode subscriptions. Each account has its own workspace Go usage URL, browser profile, saved session, gauge colors, tile, snapshot identity, and sign-in controls. Existing single-OpenCode configurations migrate automatically without losing their URL or colors.
- Added distinct title-bar controls in both full and compact views: down/up switches between full and compact layouts, the dash hides the widget to the system tray, and ✕ quits AI Gauge completely.
- Added expanded-window horizontal resizing with a dedicated bottom-right grip. The chosen width persists across launches, compact mode retains its fitted size, and expanded height follows visible content automatically.
- Added per-account gauge customization for Claude, Codex, and OpenCode accounts, plus Copilot and OpenRouter. Users can tune three percentage cutoffs and choose all four band colors, including the final 95%+ band; live range labels and **Reset defaults** keep the controls understandable. Supplied defaults preserve the existing behavior: green below 60%, yellow at 60–79%, orange at 80–94%, and red at 95%+.
- Added the ability to remove the original Claude, Codex, or OpenCode account as well as secondary accounts. Empty provider sections retain their add-account action, and removed original accounts stay removed after restart.

### Changed

- The expanded widget uses a horizontal-only resize handle and a content-derived minimum width, never below 280 pixels. Comparable gauges within each provider share label, percentage, and reset-time columns so their bars use the same length and scale, and the resize handle stops before those gauge rows would clip or wrap. At measured content limits, compact account gauges can use a full-width second row, OpenRouter text can wrap, and secondary header details compact while core controls remain visible. Automatic height is applied after the resize drag and remains capped to the available screen.
- The expanded header shows the full refresh cadence at wider sizes and just the countdown below the responsive breakpoint; compact mode also shows only the countdown.
- Percentage and reset-time columns now size to their actual text instead of reserving fixed blank space between values and day labels.
- Settings spin boxes and decimal controls now use explicit, high-contrast arrow buttons in the dark theme, including the account-specific gauge-color dialog.

### Fixed

- Copilot usage above 100% now renders a full severity-colored bar while preserving the actual overage label. Previously, passing an above-maximum value to Qt could leave the progress chunk unpainted.
- The resize grip now occupies its own footer instead of overlaying the last provider row, preventing it from obscuring Copilot and other bottom-most gauges.
- Gauge color descriptions now update immediately as cutoffs change, and custom ranges/colors apply consistently to expanded bars, compact summary chips, the Windows/Linux tray indicator, and macOS menu-bar status dots.

## 0.6.5 - 2026-07-18

### Added

- Added **Clear sign-in** controls for Claude, Codex, and OpenCode accounts. Clearing an account removes its saved cookie and live embedded-browser session from AI Gauge without affecting sessions in other browsers or devices.

### Changed

- Claude, Codex, and OpenCode sign-in now opens a real installed Chrome-family browser and automatically imports the authenticated provider session over a temporary loopback-only DevTools connection. Google and passkey sign-in no longer require copying cookies; the embedded browser and paste-cookie paths remain as fallbacks. ([#3](https://github.com/jpajak/ai-gauge/pull/3), thanks [@gitupofftheflooranddosomework](https://github.com/gitupofftheflooranddosomework).)

### Fixed

- External browser sign-in now bypasses proxy settings for its loopback DevTools connection, shuts down its worker and browser process reliably, cleans up temporary profiles, and handles profile setup failures without leaving background work behind.
- OpenCode external sign-in now waits for both the current `auth` session cookie and the rendered authenticated workspace shell. Unrelated cookies and the pre-login OAuth cookie can no longer cause a hang or close the browser before sign-in is complete.
- OpenCode sign-in verification now checks for the authenticated workspace shell instead of requiring visible usage meters, so valid accounts without usage rows are no longer reported as signed out.
- Real-browser session import now replaces stale profile cookies and preserves host-only cookie scope. This prevents an older signed-out OpenCode `auth` cookie from taking precedence over the newly imported signed-in session.
- Clearing or removing an account now clears both its persisted and live browser sessions, and an older in-flight refresh can no longer restore stale signed-in state after the account was cleared.

## 0.6.4 - 2026-07-14

### Fixed

- Codex usage tracking no longer mistakes the current non-interactive **Personal usage** heading for a selectable tab, and now accepts analytics pages that expose only the shared Weekly usage limit. This avoids exhausted extractor retries and an `error · stale` state while the five-hour Session card is unavailable. Session tracking remains supported and will reappear automatically whenever Codex restores that card; sign-in verification also accepts the weekly-only layout. ([#2](https://github.com/jpajak/ai-gauge/pull/2), thanks [@gitupofftheflooranddosomework](https://github.com/gitupofftheflooranddosomework).)
- Codex hides its session-to-weekly burn-rate headline while the Session metric is unavailable, avoiding a stale sessions-per-week estimate beside a weekly-only snapshot. Stored calibration history is preserved and the headline returns with the Session metric.

## 0.6.3 - 2026-07-10

### Added

- Added **OpenCode** usage tracking with Rolling, Weekly, and Monthly meters, a configurable workspace Go usage URL, Settings controls, and provider tile support in both expanded and compact views.
- Added OpenCode cookie-paste setup for accounts that cannot complete Google sign-in inside the embedded browser.

### Changed

- Claude, Codex, and OpenCode provider rows can collapse to compact inline metric chips, while the session-to-weekly burn-rate label remains available again when the row is re-expanded.
- OpenCode Rolling usage now uses the same 5-hour pacing window as the provider, so the elapsed-time tick marker appears on the Rolling bar.

### Fixed

- OpenCode cookie injection now targets the `opencode.ai` profile correctly, accepts full `Cookie:` headers copied from DevTools, and avoids forcing HttpOnly on OpenCode cookies so the signed-in page can hydrate normally.
- OpenCode refreshes requested while another provider is already in-flight are queued instead of leaving the tile stuck at `loading...`.
- The OpenCode sign-in dialog now explains the Google embedded-browser block and points users to the paste-cookie fallback.
## 0.6.2 - 2026-06-25

### Added

- Added a **Fade when inactive** setting. When enabled, the floating widget fades while it is unfocused and the mouse is away, then returns to full opacity when hovered, focused, or dragged.

### Changed

- **Fade when inactive** now defaults to 80% opacity, keeping the widget easier to read while still getting it out of the way.

### Fixed

- Expanding or collapsing the OpenRouter model breakdown now immediately refits the floating widget height, so the model rows appear or disappear without needing to drag or move the window first.

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
