# usage-view — Handoff

A Windows desktop monitor for **Claude.ai**, **ChatGPT Codex**, and **GitHub Copilot** usage limits. Compact always-on-top widget, system tray, manual + auto refresh.

## Quick start (incoming dev)

```powershell
py -m venv .venv
.venv\Scripts\pip install -e .[dev]
.venv\Scripts\python -m usage_view
.venv\Scripts\pytest -q
```

27 tests pass. Tests cover everything that *can* be tested without a live browser session: config, DPAPI secret storage, cookie parsing, Copilot REST helpers (mocked), models. Provider scrapers (Claude/Codex DOM extraction) are validated only by hand.

## What works today (verified)

- ✅ App launches, widget appears, settings dialog auto-opens on first run
- ✅ Compact always-on-top frameless widget, draggable, with three tiles
- ✅ System tray icon (color-coded), click to toggle visibility, right-click menu
- ✅ Settings dialog with dark theme, opens above the widget (stays-on-top fix landed)
- ✅ Refresh interval (1–60 min) and auto-refresh timer
- ✅ DPAPI-encrypted cookie storage (handles >8KB ChatGPT JWTs)
- ✅ ChatGPT cookie paste handles current `next-auth.session-token`, legacy
  `__Secure-next-auth.session-token`, split `.0`/`.1` cookies, `name=value`
  rows, and full `Cookie:` headers. Full headers are preferred because ChatGPT
  can also need companion cookies such as `__Secure-oai-is`.
- ✅ Claude cookie paste now prefers a full `Cookie:` header from a
  `claude.ai/settings/usage` network request and rejects wrong-site headers
  that do not include `sessionKey`.
- ✅ GitHub Copilot REST API path with fine-grained PAT and the current
  `2026-03-10` GitHub REST API version header. Supports personal user billing
  and optional organization billing via
  `/organizations/{org}/settings/billing/premium_request/usage?user={username}`.
- ✅ Copilot allowance math uses `grossQuantity`/`quantity` for included premium
  requests consumed. `netQuantity` is billable overage and can be 0 while the
  user has consumed most of their Pro/Pro+ monthly allowance.
- ✅ QtWebEngine console noise for Permissions-Policy/GSI/Intercom/preload
  warnings is filtered by `webview/page.py`.
- ✅ Cookie hydration on startup → WebEngine profile pre-authed for scrape
- ✅ All Python imports clean; PyInstaller build script in `build.ps1`

## What's likely broken / unverified

These need a real account session to actually validate:

- ⚠️ **Claude DOM scraper** — extractor JS is in [src/usage_view/providers/claude.py:14-54](src/usage_view/providers/claude.py#L14-L54). Looks for "Current session", "All models", "Claude Design" text in the rendered settings/usage page. Has *not* been confirmed against real signed-in HTML. The page is React; the extractor walks DOM after a 5s wait. **Likely needs adjustment.**
- ⚠️ **Codex DOM scraper** — same situation, [src/usage_view/providers/codex.py:14-54](src/usage_view/providers/codex.py#L14-L54). Looks for "5 hour usage limit" and "Weekly usage limit" cards.
- ⚠️ **Copilot org/enterprise permissions** — organization billing is wired in,
  but requires the user to enter the billing org and use a token/account with
  organization billing visibility plus Administration read permission. If they
  are not an org admin/billing manager, GitHub will return 403.
- ⚠️ **Cookie hydration timing** — cookies are pushed into `QWebEngineProfile.cookieStore()` synchronously at startup and on save. There's a race where the headless scraper might fire before the cookie is committed to the underlying store. If first-after-paste refresh fails but the second succeeds, that's the bug.

## Critical context: why each design choice

These are non-obvious and a future contributor will undo them if they don't know why:

### 1. Why embedded WebView, not browser cookie extraction
**Chrome 127+ added App-Bound Encryption** (mid-2024). `browser_cookie3`, `rookiepy`, and every other Python library is permanently broken on current Chrome and Edge — the encryption key is now bound to the Chrome binary itself via a SYSTEM-privileged Elevation Service. There is no user-mode workaround that doesn't involve malware-style techniques. Firefox still works with `browser_cookie3` but most users don't use Firefox.

So we own the browser session ourselves via QtWebEngine.

### 2. Why "Paste cookie" exists alongside embedded login
**Google blocks all embedded browsers** for sign-in (FedCM + WebView detection). If the user signs into Claude/ChatGPT with Google (most users do), the embedded login window cannot complete sign-in. The "Paste cookie" path lets them copy `sessionKey` (Claude) or `__Secure-next-auth.session-token` (ChatGPT) from their real browser's DevTools → app encrypts it via DPAPI → injects into the WebEngine profile's cookie store → headless scrape loads as signed-in.

### 3. Why DPAPI file storage instead of `keyring` for cookies
**Windows Credential Manager caps the credential blob at ~2560 bytes.** ChatGPT's session JWT is 5–10KB. Saving via `keyring` raises `WinError 1783: 'The stub received bad data'`. The `secret_storage` module wraps `crypt32!CryptProtectData/CryptUnprotectData` via ctypes (no new deps) and stores everything in `%APPDATA%/usage-view/secrets.dat`. Same per-user encryption strength as Credential Manager, no size limit.

The GitHub PAT is short and stays in `keyring` — the dual storage is intentional.

### 4. Why Qt imports are deferred in providers
`copilot.py` imports `QRunnable`/`QThreadPool` lazily inside `_run_async`, not at module top. `providers/base.py` lazily constructs `ProviderSignals`. This lets the test suite import provider modules **without PyQt6 installed**, which speeds CI and unit tests dramatically. Don't move them back to top-level imports without a reason.

### 5. Why the main widget no longer sets a stylesheet
Setting `widget.setStyleSheet(...)` cascades into every child dialog and breaks their default rendering (dark-on-dark, group titles overlapping fields). The widget's background is drawn in `paintEvent` instead. Dialogs have their own complete `_DARK_STYLESHEET` constants.

### 6. Why dialogs need explicit `WindowStaysOnTopHint`
The main widget is stays-on-top, so without the same flag on dialogs, they render *underneath* the widget. `SettingsDialog`, `CookieDialog`, `LoginWindow`, and the popup-OAuth view all set this flag explicitly.

## Architecture map

```
src/usage_view/
├── __main__.py              # entrypoint
├── pyinstaller_entry.py     # PyInstaller launcher; avoids relative-import crash
├── app.py                   # App controller, tray, refresh loop, dialog wiring
├── config.py                # Pydantic Config + paths + keyring (PAT) + cookie shim
├── secret_storage.py        # DPAPI file storage for >2.5KB secrets
├── models.py                # UsageSnapshot dataclass, SnapshotStatus enum
├── widget.py                # Compact always-on-top window + tiles + drag-to-move
├── settings_dialog.py       # Refresh interval, sign-in/paste-cookie buttons, PAT
├── cookie_dialog.py         # Per-provider DevTools instructions + paste textbox
├── providers/
│   ├── base.py              # Provider ABC + lazy ProviderSignals (Qt-free import)
│   ├── copilot.py           # GitHub REST + PAT + threaded fetch
│   ├── codex.py             # Headless WebView scrape of /codex/cloud/settings/analytics
│   └── claude.py            # Headless WebView scrape of /settings/usage
└── webview/
    ├── profile.py           # Per-provider QWebEngineProfile (persistent on disk)
    ├── scraper.py           # HeadlessScraper: load page → wait → runJavaScript
    ├── login_window.py      # Embedded-Chromium dialog + popup OAuth handler + Google warning
    └── cookies.py           # inject_session_cookie() — push into profile cookie store
```

## Data flow on a refresh tick

```
QTimer (5min default) ──┐
Refresh button ─────────┼──→ App.refresh_now()
Tray menu "Refresh" ────┘         │
                                   ▼
                     For each enabled provider:
                       provider.refresh(callback)
                                   │
              ┌────────────────────┼─────────────────────┐
              ▼                    ▼                     ▼
        CopilotProvider       CodexProvider        ClaudeProvider
              │                    │                     │
        QThreadPool worker   HeadlessScraper       HeadlessScraper
              │             (offscreen QWebEngine) (offscreen QWebEngine)
        requests.get(GitHub)       │                     │
              │              load(URL) → wait 5s    load(URL) → wait 5s
              ▼              runJavaScript(extractor) runJavaScript(extractor)
        UsageSnapshot              │                     │
              │                    ▼                     ▼
              │              UsageSnapshot          UsageSnapshot
              └─────────┬─────────┴─────────────────────┘
                        ▼
              ProviderSignals.snapshot_ready (Qt main thread)
                        │
                        ▼
              App._on_snapshot → widget.update_snapshot → tray icon refresh
```

## Open follow-ups (priority order)

1. **Validate scrapers against real signed-in pages.** Sign in via "Paste cookie", refresh, watch for "error" status on tiles. If error: get the rendered HTML (open `claude.ai/settings/usage` in real browser, View Source after JS runs), update the JS extractors in `providers/claude.py` and `providers/codex.py`. The extractors are intentionally text-based ("Current session", "5 hour usage limit") rather than CSS-class-based — those classes are React-generated and change weekly.
2. **Reverse-engineer JSON endpoints.** The DOM scrape works but is slow (~5s per refresh) and brittle. Better: pull cookies from the WebEngine profile, hit the underlying API (`/api/organizations/{id}/usage` for Claude, `/backend-api/codex/usage` for Codex) with `requests`, parse JSON. Endpoint shapes need confirming with a logged-in DevTools Network tab.
3. **Cookie expiry detection.** When a session cookie expires upstream, the next scrape returns `AUTH_REQUIRED` and the tile shows "not signed in". Today the user has to re-paste manually. Could add a "Re-paste" action on the tile itself.
4. **Auto-launch on Windows startup.** Add a checkbox to Settings that toggles a Run-key entry. Trivial via `winreg`.
5. **Widget size / opacity persistence.** Persists position and size on close, but not on resize/opacity-change events. Minor.
6. **Better Copilot UX.** Today the user has to enter their monthly quota (Pro=300, Pro+=1500). Could detect the plan from `GET /user` or the billing endpoint response.
7. **PyInstaller build verified end-to-end.** `build.ps1` is written but I haven't actually built and run a packaged `.exe` on this machine yet. Probably works (standard QtWebEngine recipe) but worth confirming before distribution.

## Common debugging recipes

**Cookie not taking effect:**
```python
# In a Python REPL with the venv:
import sys; sys.path.insert(0, 'src')
from usage_view.config import get_provider_cookie
print(get_provider_cookie('codex'))  # should be the JWT, not None
```

For split ChatGPT cookies, the saved text should parse to both pieces:
```python
from usage_view.webview.cookies import _parse_cookie_pairs
print([name for name, _ in _parse_cookie_pairs('codex', get_provider_cookie('codex') or '')])
# ['__Secure-next-auth.session-token.0', '__Secure-next-auth.session-token.1']
```

**Scrape returning empty:** the React app probably needs longer than 5s. Bump `wait_ms=5000` in `providers/{claude,codex}.py` to 8000.

**Want to see what the headless WebView is actually loading:** in `webview/scraper.py`, change `self._view.resize(1280, 900)` to `self._view.show()` and the offscreen window becomes visible.

**Reset everything:** delete `%APPDATA%/usage-view/` (config + profiles + secrets). Re-launch.

**Logs:** the app prints WebEngine JS console messages to stdout (those `js: ...` lines you see in the terminal). Useful for debugging extractor errors. Real logging via `logging` module is not set up — easy add if needed.

## Files / locations cheat sheet

- Config: `%APPDATA%/usage-view/config.json` (Pydantic-validated JSON)
- Cookies (encrypted): `%APPDATA%/usage-view/secrets.dat` (DPAPI per-user)
- GitHub PAT: Windows Credential Manager, target `usage-view`, account `github-pat`
- WebView profiles: `%APPDATA%/usage-view/profiles/{claude,codex}/`
- Plan / design notes: `C:\Users\John\.claude\plans\i-regulary-check-usage-serialized-rivest.md`

## Things to NOT do (footguns)

- **Don't add `parent=self._widget` back to dialog constructors.** It re-introduces the stylesheet cascade. Keep `super().__init__(None)`.
- **Don't switch cookie storage back to `keyring`.** ChatGPT JWTs exceed the 2.5KB limit; you'll get `WinError 1783`.
- **Don't import PyQt6 at module top in `providers/copilot.py` or `providers/base.py`.** It breaks unit tests that don't have PyQt6 installed.
- **Don't try to make Google sign-in work in the embedded WebView.** Google deliberately blocks WebViews for anti-phishing; no UA spoof or feature flag will get past it. The "Paste cookie" path is the workaround.
- **Don't try to read Chrome cookies directly.** Chrome 127+ App-Bound Encryption broke this for everyone in mid-2024.
- **Don't suggest `manage_billing:user` as a classic PAT scope.** It's no longer in GitHub's UI. Direct users to fine-grained PAT with "Plan" account permission (Read).
