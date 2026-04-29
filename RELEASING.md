# Releasing

This document is for maintainers publishing a public GitHub release of
AI Gauge.

## What GitHub Releases Are

A GitHub Release is a named snapshot of the repository, usually tied to a Git
tag such as `v0.4.2`. It gives users a stable page with release notes and
downloadable files.

For AI Gauge, the release page should include:

- The source code snapshot that GitHub attaches automatically.
- A Windows build artifact, preferably a zipped `dist/ai-gauge/` folder.
- SHA256 checksums for downloadable artifacts.
- A short note that the app is unsigned unless code signing has been added.

## Release Checklist (automated path)

The recommended path uses [.github/workflows/release.yml](.github/workflows/release.yml):
pushing a `v*` tag triggers a Windows GitHub-Actions runner that runs the
test suite, builds the standalone .exe, zips it, computes a SHA256, and
attaches both files to a **draft** release on GitHub. You publish the draft
from the web UI.

1. Confirm `pyproject.toml`, `src/aigauge/__init__.py`, `README.md`, and
   `CHANGELOG.md` all show the new version. The release workflow runs
   `tools/check_versions.py` and also rejects mismatched tag/pyproject
   versions, but a local pre-flight catches issues sooner:

   ```powershell
   .venv\Scripts\python.exe tools\check_versions.py
   .venv\Scripts\python.exe -m pytest
   .\build.ps1
   .\dist\ai-gauge\ai-gauge.exe   # smoke-test
   ```

2. Commit the release prep changes to `main`.
3. Create and push the version tag:

   ```powershell
   git tag v0.5.0
   git push origin main
   git push origin v0.5.0
   ```

4. Watch the **release** workflow under the Actions tab. On success it
   creates a draft release on the [Releases page](https://github.com/jpajak/ai-gauge/releases)
   with `ai-gauge-<version>-windows.zip` and the matching `.sha256` attached.
5. Open the draft release, paste the relevant changelog notes into the body
   (the workflow auto-generates a commit list, but the changelog reads
   better), and click **Publish release**. Mark as prerelease if you want a
   soft launch.

## Manual fallback

If the automated workflow is unavailable (e.g. you're publishing from a
fork without Actions enabled), the manual flow still works:

1. Run the same local pre-flight in step 1 above.
2. Zip the full `dist\ai-gauge\` folder. Do not upload only the executable
   from a one-folder build.
3. Create a checksum:

   ```powershell
   Get-FileHash .\ai-gauge-0.5.0-windows.zip -Algorithm SHA256
   ```

4. Push the version tag, then in GitHub go to **Releases** → **Draft a new
   release**, select the tag, paste the changelog notes, and attach the zip
   plus the SHA256 file.

## Suggested Release Notes Shape

```markdown
## AI Gauge 0.5.0

Compact always-on-top Windows monitor for Claude.ai, ChatGPT Codex, and GitHub
Copilot usage limits.

### Highlights

- ...

### Download

- `ai-gauge.zip` contains the Windows app folder.
- Extract it and run `ai-gauge.exe`.
- Windows may show an unsigned-app warning.

### Verification

SHA256:

`...`
```

## Signing Notes

Unsigned Windows executables commonly trigger Microsoft Defender SmartScreen
warnings for new downloads. This does not necessarily mean the file is unsafe,
but users will need to decide whether they trust the project.

Code signing can reduce friction, but it costs money and adds maintenance. It
is reasonable to wait until there is real external usage before buying a
certificate.
