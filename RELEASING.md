# Releasing

This document is for maintainers publishing a public GitHub release of
AI Gauge.

## What GitHub Releases Are

A GitHub Release is a named snapshot of the repository, usually tied to a Git
tag such as `v0.4.2`. It gives users a stable page with release notes and
downloadable files.

For AI Gauge, the release page should include:

- The source code snapshot that GitHub attaches automatically.
- One build artifact per supported OS:
  - Windows: zipped `dist/ai-gauge/` folder
  - macOS: tar.gz of the `.app` bundle
  - Linux: tar.gz of `dist/ai-gauge/`
- SHA256 checksums for each downloadable artifact.
- A short note that the app is unsigned unless code signing has been added.
- Windows artifact SHA256 and Authenticode status for Defender triage.

## Release Checklist (automated path)

The recommended path uses [.github/workflows/release.yml](.github/workflows/release.yml):
pushing a `v*` tag fans out a 3-OS build matrix (Windows, macOS, Ubuntu).
Each runner runs the test suite, builds its OS's standalone bundle, packages
it, computes a SHA256, and uploads both files as job artifacts. A final job
collects all artifacts and attaches them to a **draft** release on GitHub.
You publish the draft from the web UI.

1. Confirm `pyproject.toml`, `src/aigauge/__init__.py`, `README.md`, and
   `CHANGELOG.md` all show the new version. The release workflow runs
   `tools/check_versions.py` and also rejects mismatched tag/pyproject
   versions, but a local pre-flight catches issues sooner:

   ```powershell
   # Windows
   .venv\Scripts\python.exe tools\check_versions.py
   .venv\Scripts\python.exe -m pytest
   .\build.ps1
   .\dist\ai-gauge\ai-gauge.exe   # smoke-test
   ```

   ```bash
   # macOS / Linux
   .venv/bin/python tools/check_versions.py
   .venv/bin/python -m pytest
   ./build.sh
   open dist/ai-gauge.app          # macOS smoke-test
   ./dist/ai-gauge/ai-gauge        # Linux smoke-test
   ```

2. Commit the release prep changes to `main`.
3. Create and push the version tag:

   ```powershell
   git tag v<version>
   git push origin main
   git push origin v<version>
   ```

4. Watch the **release** workflow under the Actions tab. On success it
   creates a draft release on the [Releases page](https://github.com/jpajak/ai-gauge/releases)
   with three artifact pairs attached:
   - `ai-gauge-<version>-windows.zip` (+ `.sha256`)
   - `ai-gauge-<version>-macos.tar.gz` (+ `.sha256`)
   - `ai-gauge-<version>-linux.tar.gz` (+ `.sha256`)
5. Open the draft release, paste the relevant changelog notes into the body
   (the workflow auto-generates a commit list, but the changelog reads
   better), and click **Publish release**. Mark as prerelease if you want a
   soft launch.

## Manual fallback

If the automated workflow is unavailable (e.g. you're publishing from a
fork without Actions enabled), the manual flow still works — but you'll
need access to a machine of each OS you intend to ship for, since
PyInstaller cross-compilation isn't supported.

1. Run the same local pre-flight in step 1 above on each target OS.
2. Package the build:
   - Windows: zip the full `dist\ai-gauge\` folder.
   - macOS: `tar -C dist -czf ai-gauge-<ver>-macos.tar.gz ai-gauge.app`
   - Linux: `tar -C dist -czf ai-gauge-<ver>-linux.tar.gz ai-gauge`
3. Create a checksum:

   ```powershell
   Get-FileHash .\ai-gauge-<ver>-windows.zip -Algorithm SHA256
   ```

   ```bash
   shasum -a 256 ai-gauge-<ver>-macos.tar.gz
   ```

4. Push the version tag, then in GitHub go to **Releases** → **Draft a new
   release**, select the tag, paste the changelog notes, and attach all
   archive + `.sha256` pairs.

## Suggested Release Notes Shape

```markdown
## AI Gauge <version>

Compact monitor for Claude.ai, ChatGPT Codex, GitHub Copilot, and OpenRouter usage.
Native UI per OS: floating widget on Windows / Linux, menu-bar item on macOS.

### Highlights

- ...

### Download

- Windows: `ai-gauge-<ver>-windows.zip` → extract, run `ai-gauge.exe`.
- macOS: `ai-gauge-<ver>-macos.tar.gz` → drag `ai-gauge.app` to Applications.
  First launch needs `xattr -dr com.apple.quarantine ai-gauge.app` or right-click → Open.
- Linux: `ai-gauge-<ver>-linux.tar.gz` → extract, run `./ai-gauge/ai-gauge`.

### Verification

SHA256: see the `.sha256` next to each archive.
```

## Windows Defender / Reputation Notes

Windows releases are built with PyInstaller metadata generated from
`pyproject.toml` so `ai-gauge.exe` has a stable ProductName, CompanyName,
FileDescription, FileVersion, ProductVersion, OriginalFilename, and copyright.
The app's Start at login setting uses a named Task Scheduler entry (`AI Gauge`)
instead of writing to `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`.

### Why builds get flagged

The root cause is always the same: an **unsigned, low-prevalence PyInstaller
binary**. Until the artifact is code-signed (see Signing Notes), expect two
distinct kinds of Defender verdict, which are reported and submitted the same
way but mean different things:

- **Static file-reputation ML** — e.g. `Trojan:Win32/Bearfoos.A!ml`. A generic
  catch-all that fires on unsigned, packed Python executables almost regardless
  of their actual code. It is about the file's *reputation*, not its behavior.
- **Behavioral ML** — e.g. `Behavior:Win32/Persistence.A!ml`. Fires at runtime
  when the unsigned exe registers autostart (a Run-key value on older builds, or
  the Task Scheduler entry on 0.6.0+). The flagged items are the autostart
  artifacts, and the "executes commands from an attacker" text is generic
  boilerplate for the Persistence family, not a finding about our code.

Both collapse to the same fix — **code signing** — which is why it is the
priority over any change to the autostart mechanism. The heuristic mitigations
already in place (stable version metadata, `--noupx`, one-folder build, per-user
Task Scheduler autostart) reduce but do not eliminate these on unsigned builds.

### Recording artifact details

Before publishing a Windows release, record these from the built exe (needed for
the release page and for any false-positive submission):

```powershell
Get-FileHash ".\dist\ai-gauge\ai-gauge.exe" -Algorithm SHA256
Get-AuthenticodeSignature ".\dist\ai-gauge\ai-gauge.exe"
```

### Local development exclusion (your machine only)

Defender re-quarantines each fresh local build, which fights iterative work. On
your **own dev machine only**, add a Defender folder exclusion for the build
output (Windows Security → Virus & threat protection → Manage settings →
Exclusions → Add an exclusion → Folder → `C:\git\ai-gauge\dist`). Never put this
advice in user-facing docs or release notes — end users should not be told to
exclude folders.

### Submitting a Defender false positive

If Defender or SmartScreen flags a release, report the exact artifact to
Microsoft as a software-developer false positive. The portal is a web form
(there is no public API for indie submissions), but
[tools/wdsi_submission.py](tools/wdsi_submission.py) pre-fills everything it
asks for so the submission is copy-paste. Submit **once per distinct exe/hash**
that was flagged (e.g. a Run-key build under `C:\Tools` and a dev build under
`C:\git\...\dist` are two separate submissions).

1. Build (or locate) the flagged exe, then generate its submission sheet. Read
   the **detection name** and **security intelligence version** off the Defender
   alert ("Protection history" → expand the threat) and pass them in so the
   sheet has no placeholders:

   ```powershell
   # Default reads dist\ai-gauge\ai-gauge.exe:
   .venv\Scripts\python.exe tools\wdsi_submission.py `
       --detection "Behavior:Win32/Persistence.A!ml" --intel 1.0.0.0

   # Point --exe at any other flagged copy:
   .venv\Scripts\python.exe tools\wdsi_submission.py `
       --exe "C:\Tools\ai-gauge\ai-gauge.exe" `
       --detection "Trojan:Win32/Bearfoos.A!ml" --intel 1.0.0.0
   ```

   The sheet auto-fills the SHA256, Authenticode status, version, git tag/commit,
   repo URL, and contact (from `git config user.email`). Add `--out build\wdsi.txt`
   to also save it to a file.

2. Open the portal: <https://www.microsoft.com/en-us/wdsi/filesubmission>
   Sign in with a Microsoft account and choose **I'm a software developer**.
3. **Upload the exact exe** the sheet was generated from — the portal hashes it,
   so the SHA256 must match the sheet. Select **Incorrectly detected as
   malware/malware family** and enter the detection name from the sheet.
4. Paste the generated sheet into the **Additional information** box and submit.
   You will get a submission ID; Microsoft usually responds within hours to a
   couple of days, and you can check status on the same portal.
5. Once a signing certificate is in place (see Signing Notes), Microsoft can
   allow-list by **publisher** instead of per-file hash, so future signed builds
   are trusted without re-submitting each release.

## Signing Notes

Release artifacts are unsigned unless a maintainer provides signing material.
**Signing is the durable fix for both Defender verdict types above** — it gives
the file publisher reputation (static ML) and makes a persistence action by a
known publisher unremarkable (behavioral ML). A *self-signed* certificate does
**not** help; the certificate must chain to a CA Windows already trusts.

Per-OS situation:

- **Windows** — SmartScreen and Microsoft Defender warn on new or low-prevalence
  downloads. Authenticode signing with a consistent publisher certificate is the
  recommended fix before broad distribution. Note: since June 2023 all
  code-signing private keys must live on FIPS hardware (USB token or cloud HSM),
  so there are no cheap downloadable `.pfx` certs anymore. Options:
  - **SignPath Foundation** — free code signing for open-source projects. This is
    the intended path for AI Gauge. Apply at <https://about.signpath.io/open-source>,
    then the signing step can run in CI via SignPath's GitHub Action.
  - **Azure Trusted Signing** — Microsoft's own signing service (~$10/month).
    Best Defender/SmartScreen reputation for the cost; eligibility historically
    needs an organization with verifiable history, so check current terms.
  - **EV code-signing certificate** (~$250–600/yr, hardware token) — the only
    option that grants *immediate* SmartScreen reputation; overkill unless
    distributing at volume.
- **macOS** — Gatekeeper blocks first launch (quarantine attribute) until
  Developer ID signing/notarization is added.
- **Linux** — no OS signing layer, so no equivalent first-launch warning.

Code signing / notarization reduces friction but costs money and adds
maintenance. Until it is in place, lean on the false-positive submission flow
above. Unsigned internal builds should still come from CI, keep their SHA256
hashes, and avoid ad hoc binaries from developer machines.
