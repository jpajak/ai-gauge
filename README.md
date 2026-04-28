# usage-view

Compact always-on-top Windows monitor for **Claude.ai**, **ChatGPT Codex**, and **GitHub Copilot** usage limits. Manual + auto refresh, system tray, draggable frameless widget.

Current version: **0.4.3**. See [CHANGELOG.md](CHANGELOG.md) for release notes.

usage-view is a personal open-source project and unofficial local desktop
utility. It is not an AloeDesk product, and it is not affiliated with
Anthropic, OpenAI, GitHub, or Microsoft. Provider pages and APIs may change
without notice.

## Run from source

```bash
py -m venv .venv
.venv\Scripts\pip install -e .[dev]
.venv\Scripts\python -m usage_view
```

The first launch opens the Settings dialog. Configure providers there.

## First-time setup per provider

| Provider           | Setup                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Claude.ai**      | **Sign in (recommended):** opens an embedded browser. <b>Don't click "Continue with Google"</b> — Google blocks embedded browsers. If your account is Google-linked, just type that same email into the **Enter your email** box and use the **magic link** sent to your inbox. **Paste cookie:** alternative if magic-link is unavailable; see below.                                                                                      |
| **ChatGPT Codex**  | Same as Claude — use email + magic link in the embedded browser, or paste cookie.                                                                                                                                                                                                                                                                                                                                                           |
| **GitHub Copilot** | Create a **fine-grained PAT** at <https://github.com/settings/personal-access-tokens/new>. For personal Pro/Pro+, add **Account permissions → Plan → Read**. Paste into Settings; set your monthly quota (Pro=300, Pro+=1500, Business=300, Enterprise=1000). If Copilot is billed through an organization, enter the billing org and use a token/account with org billing access and **Organization permissions → Administration → Read**. |

Sessions persist between runs in `%APPDATA%/usage-view/profiles/{provider}/`. The GitHub PAT is stored in **Windows Credential Manager** when available, with the same DPAPI-encrypted file fallback used for pasted cookies at `%APPDATA%/usage-view/secrets.dat`.

usage-view does not include telemetry or a backend service. Provider requests
are made from the local app to the configured providers. See
[SECURITY.md](SECURITY.md) for security and privacy notes.

### Paste cookie (Google sign-in users)

Google blocks all embedded browsers from signing in. Workaround: copy your existing session cookie from your real browser into the app. Cookies last weeks before they need re-pasting.

1. Sign into <https://claude.ai> (or <https://chatgpt.com>) in **Chrome / Edge / Firefox** as you normally do.
2. For ChatGPT, press **F12** → **Network**, reload the page, click a
   `chatgpt.com` request, and copy the full **Request Headers → Cookie:** value.
   This includes split session cookies plus companion auth cookies such as
   `__Secure-oai-is`.
3. For Claude, press **F12** → **Network**, reload `https://claude.ai/settings/usage`,
   click a `claude.ai` request, and copy the full **Request Headers → Cookie:**
   value. It must include `sessionKey`.
4. In the app: Settings → click **Paste cookie** next to the provider, paste, Save.

## Daily use

- The widget floats above other windows by default. Drag anywhere to move; close (✕) hides to tray.
- Tray icon turns yellow ≥75% / red ≥90% based on the highest tile reading.
- Right-click tray → Refresh / Settings / Quit. Left-click toggles widget visibility.
- Auto-refresh is adaptive: manual refresh or changed usage enters the active
  cadence, then unchanged results back off toward the configured max interval.
  Defaults are 5 min active and 60 min idle max.
- Enable **Start with Windows** in Settings if you want it to run as a daily tray utility.

## Build a standalone .exe

No Python install required on the target machine.

Recommended release build:

```powershell
.\build.ps1
```

Output goes to `dist/usage-view/usage-view.exe` (~150-200 MB because the Chromium runtime is bundled). Distribute the whole `dist/usage-view` folder; user data still lives outside it under `%APPDATA%/usage-view/`.

For a single-file binary (slower first launch):

```powershell
.\build.ps1 -OneFile
```

The equivalent manual PyInstaller command is:

```powershell
.venv\Scripts\pip install pyinstaller
.venv\Scripts\pyinstaller `
    --windowed --name usage-view `
    --paths src `
    --collect-all PyQt6.QtWebEngineWidgets `
    --collect-all PyQt6.QtWebEngineCore `
    pyinstaller_entry.py
```

Add `--onefile` to the manual command only if you want the single-file build.

Public release builds are expected to be unsigned unless noted otherwise, so
Windows may show a SmartScreen warning for downloaded executables. See
[RELEASING.md](RELEASING.md) for maintainer release steps.

## Tests

```bash
.venv\Scripts\pytest
```

Tests cover: config round-trip, Copilot REST helpers (with mocked HTTP), and snapshot models. Provider scrapers (Claude/Codex) require a live browser session and are validated manually.

## Notes / limitations

- **Why an embedded browser instead of reading Chrome cookies?** Chrome 127+ added App-Bound Encryption (mid-2024) that blocks every external Python library from decrypting Chrome/Edge cookies. Owning the browser session ourselves is the only reliable workaround.
- **Claude / Codex layouts may change.** If a provider tile shows "error" after a UI update upstream, the page-extractor JS in `src/usage_view/providers/{claude,codex}.py` needs adjusting — the rest of the app keeps working.
- The Copilot REST endpoint returns the _current calendar month_ of premium-request usage. The widget tracks gross premium requests consumed against the included allowance; net quantity is only the billable overage. Reset is computed as the 1st of the next month. GitHub does not currently expose a reliable personal-plan quota field, so Settings uses a plan dropdown with a Custom fallback.
