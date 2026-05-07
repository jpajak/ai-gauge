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

## Signing Notes

Release artifacts are unsigned on every OS:

- **Windows** — Microsoft Defender SmartScreen warns on new downloads.
- **macOS** — Gatekeeper blocks first launch (quarantine attribute).
- **Linux** — no signing layer, so no warning.

Code signing / notarization can reduce friction but costs money and adds
maintenance. It is reasonable to wait until there is real external usage
before investing.
