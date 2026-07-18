# Security

AI Gauge is an independent open-source local desktop utility for Windows,
macOS, and Linux. It is not affiliated with Anthropic, OpenAI, GitHub,
Microsoft, OpenCode, OpenRouter, or any other provider.

## Reporting a Vulnerability

Please do not open a public issue for a vulnerability that exposes session
cookies, GitHub tokens, OpenRouter keys, or other secrets.

Preferred channel: open a private security advisory at
<https://github.com/jpajak/ai-gauge/security/advisories/new>.

If that is not available, contact the maintainer directly through their
GitHub profile and include:

- A short description of the issue.
- Steps to reproduce it.
- The affected version or commit.
- Whether any token, cookie, log, screenshot, or local file content was exposed.

Please avoid sending real cookies, access tokens, or account-identifying log
snippets. Redacted examples are enough for initial triage.

## Secret Storage

The app stores provider sessions locally on the user's machine. Each OS
uses its native credential store; the threat model is the same shape on
all three: same-user processes can decrypt the data, but other local users
cannot.

| OS      | Cookies                                                      | GitHub PAT / OpenRouter keys |
| ------- | ------------------------------------------------------------ | ---------------------------- |
| Windows | DPAPI-encrypted `%APPDATA%/ai-gauge/secrets.dat`             | Windows Credential Manager   |
| macOS   | Login Keychain                                               | Login Keychain               |
| Linux   | Secret Service (GNOME Keyring / KWallet) via `keyring`       | same                         |

Embedded browser profiles live under `<app-data>/profiles/{account-id}/` on
every OS. The default Claude, Codex, and OpenCode account IDs are `claude`,
`codex`, and `opencode_go`; additional Claude/Codex accounts get their own
generated IDs and profiles.

### Why the split on Windows?

Windows Credential Manager caps each blob at ~2.5 KB, which is fine for a
GitHub PAT or OpenRouter key but smaller than some browser session cookies,
especially ChatGPT's `__Secure-next-auth.session-token` JWT.
On Windows we therefore keep cookies in `secrets.dat`, encrypted with DPAPI
(`CryptProtectData`), and keep the GitHub PAT and OpenRouter keys in
Credential Manager. macOS Keychain and the Linux Secret Service have no
comparable size limit, so on those platforms everything goes through
`keyring`.

### What the OS credential stores do and do not protect against

All three credential stores bind ciphertext to the **logged-in user
account**, not to AI Gauge specifically:

- **Same-user processes can decrypt the secrets.** Any process running under
  the same OS user — a malicious script, a browser extension host, a
  user-mode malware sample — can call the same APIs and recover the
  plaintext. This is the same threat model browsers use for cookie storage.
- **Other local users cannot decrypt them.** A different local account, a
  service account, or another macOS user's session will not be able to read
  AI Gauge's secrets without first impersonating the user.

The secrets stored here are session tokens and API keys, not just passwords.
Recovery of a Claude or ChatGPT session cookie is functionally equivalent to
taking over the account in a browser until the cookie expires. Recovery of a
GitHub PAT or OpenRouter key can allow API access within that token's scope.
Treat your OS user profile accordingly.

On non-Windows hosts the legacy `secret_storage` write path is **disabled
by default** (cookies go through `keyring` instead). Setting
`AIGAUGE_ALLOW_PLAINTEXT_SECRETS=1` opts into a plaintext fallback for
test fixtures only; production code paths should never reach this branch.

## Browser Sign-In

The default sign-in flow launches an installed Chrome-family browser with a
new temporary profile because Google intentionally blocks OAuth in embedded
user-agents. AI Gauge binds Chrome's DevTools endpoint to a random loopback-only
port, reads only cookies scoped to the selected provider domain, imports them
into the matching AI Gauge account profile, and closes the temporary browser.
The temporary browser profile is then deleted. Google cookies and cookies for
unrelated sites are not imported. The resulting provider session is stored by
the same OS-protected secret backend described above.

The DevTools port exists only for the lifetime of the sign-in window. As with
other local debugging ports, another process running as the same OS user could
attempt to connect while sign-in is active; this is within the same-user threat
model described above.

## Embedded Browser Fallback

The fallback sign-in window uses an in-process `QWebEngineView` with a per-account
profile under `<app-data>/profiles/{account-id}/`. Cookies it acquires are
kept inside that account profile and are not shared with your real Chrome or
Edge browser. Multiple Claude/Codex accounts are isolated from each other by
using separate profile directories and separate stored cookie secrets;
OpenCode uses its own `opencode_go` profile and cookie secret.

Navigation in the embedded browser is restricted to an allowlist of
provider auth domains (Claude, ChatGPT, OpenCode, and their known
OAuth/identity hops plus the magic-link delivery surfaces). Off-allowlist
navigations are blocked as defense-in-depth against an open-redirect bug on a
provider sending the embedded browser to an arbitrary URL.

## Privacy

AI Gauge does not include telemetry or a backend service. Provider requests
are made from the local app to Claude.ai, ChatGPT, OpenCode, GitHub, and OpenRouter
endpoints needed to read usage information.

Diagnostic logs are written locally to `<app-data>/ai-gauge.log`. Logs
are intended to avoid recording
raw cookies, personal access tokens, OpenRouter keys, and sensitive response
bodies. Review logs before sharing them in an issue.

## Scope and Limitations

This project relies on provider web pages and APIs that may change without
notice. Authentication, rate limits, page structure, and usage calculations are
controlled by the upstream providers.

Users are responsible for deciding whether this tool fits their provider terms,
company policies, and personal security expectations.
