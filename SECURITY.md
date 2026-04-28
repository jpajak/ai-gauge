# Security

usage-view is a personal open-source local Windows desktop utility. It is not
an AloeDesk product, and it is not affiliated with Anthropic, OpenAI, GitHub,
or Microsoft.

## Reporting a Vulnerability

Please do not open a public issue for a vulnerability that exposes session
cookies, GitHub tokens, or other secrets.

Until a private reporting channel is configured for this repository, contact
the maintainer directly through their GitHub profile and include:

- A short description of the issue.
- Steps to reproduce it.
- The affected version or commit.
- Whether any token, cookie, log, screenshot, or local file content was exposed.

Please avoid sending real cookies, access tokens, or account-identifying log
snippets. Redacted examples are enough for initial triage.

## Secret Storage

The app stores provider sessions locally on the user's machine:

- Claude.ai and ChatGPT Codex session cookies are stored under
  `%APPDATA%/usage-view/secrets.dat`.
- GitHub Copilot personal access tokens are stored in Windows Credential
  Manager when available.
- Legacy token storage may be migrated out of `secrets.dat` when possible.
- Embedded browser profiles are stored under
  `%APPDATA%/usage-view/profiles/{provider}/`.

`secrets.dat` uses Windows DPAPI when available, so it is intended to be
readable only by the same Windows user account. If DPAPI is unavailable, the
app may fall back to local file storage for compatibility.

## Privacy

usage-view does not include telemetry or a backend service. Provider requests
are made from the local app to Claude.ai, ChatGPT, and GitHub endpoints needed
to read usage information.

Diagnostic logs are written locally to
`%APPDATA%/usage-view/usage-view.log`. Logs are intended to avoid recording
raw cookies, personal access tokens, and sensitive response bodies. Review logs
before sharing them in an issue.

## Scope and Limitations

This project relies on provider web pages and APIs that may change without
notice. Authentication, rate limits, page structure, and usage calculations are
controlled by the upstream providers.

Users are responsible for deciding whether this tool fits their provider terms,
company policies, and personal security expectations.
