# Security

AI Gauge is a personal open-source local Windows desktop utility. It is not
an AloeDesk product, and it is not affiliated with Anthropic, OpenAI, GitHub,
or Microsoft.

## Reporting a Vulnerability

Please do not open a public issue for a vulnerability that exposes session
cookies, GitHub tokens, or other secrets.

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

The app stores provider sessions locally on the user's machine:

- Claude.ai and ChatGPT Codex session cookies are stored under
  `%APPDATA%/ai-gauge/secrets.dat`.
- GitHub Copilot personal access tokens are stored in Windows Credential
  Manager when available.
- Legacy token storage may be migrated out of `secrets.dat` when possible.
- Embedded browser profiles are stored under
  `%APPDATA%/ai-gauge/profiles/{provider}/`.

`secrets.dat` is encrypted with Windows DPAPI (`CryptProtectData`).

### What DPAPI does and does not protect against

DPAPI binds the ciphertext to the **Windows user account**, not to AI Gauge.
That has two consequences worth being explicit about:

- **Same-user processes can decrypt it.** Any process running under the same
  Windows user — including a malicious script, another browser extension
  host, or a user-mode malware sample — can call `CryptUnprotectData` and
  recover the plaintext. This is the same threat model Chrome's pre-v127
  cookie storage used.
- **Other Windows users on the same machine cannot decrypt it.** A different
  local account or a service account running as `LOCAL SYSTEM` will not be
  able to read `secrets.dat` without first impersonating the user.

The secrets stored here are session tokens, not just passwords — recovery of
a Claude or ChatGPT session cookie is functionally equivalent to taking over
the account in a browser until the cookie expires. Treat your Windows user
profile accordingly.

On non-Windows hosts (used for cross-platform development), the secret-store
write path is **disabled by default**. Setting
`AIGAUGE_ALLOW_PLAINTEXT_SECRETS=1` opts into a plaintext fallback for
testing purposes only; production code paths should never reach this branch.

## Embedded Browser

The sign-in window uses an in-process `QWebEngineView` with a per-provider
profile under `%APPDATA%/ai-gauge/profiles/{provider}/`. Cookies it acquires
are kept inside that profile and are not shared with your real Chrome or
Edge browser.

Navigation in the embedded browser is restricted to an allowlist of
provider auth domains (Claude, ChatGPT, and their known OAuth/identity hops
plus the magic-link delivery surfaces). Off-allowlist navigations are
blocked as defense-in-depth against an open-redirect bug on either provider
sending the embedded browser to an arbitrary URL.

## Privacy

AI Gauge does not include telemetry or a backend service. Provider requests
are made from the local app to Claude.ai, ChatGPT, and GitHub endpoints needed
to read usage information.

Diagnostic logs are written locally to
`%APPDATA%/ai-gauge/ai-gauge.log`. Logs are intended to avoid recording
raw cookies, personal access tokens, and sensitive response bodies. Review logs
before sharing them in an issue.

## Scope and Limitations

This project relies on provider web pages and APIs that may change without
notice. Authentication, rate limits, page structure, and usage calculations are
controlled by the upstream providers.

Users are responsible for deciding whether this tool fits their provider terms,
company policies, and personal security expectations.
