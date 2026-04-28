# Releasing

This document is for maintainers publishing a public GitHub release of
usage-view.

## What GitHub Releases Are

A GitHub Release is a named snapshot of the repository, usually tied to a Git
tag such as `v0.4.2`. It gives users a stable page with release notes and
downloadable files.

For usage-view, the release page should include:

- The source code snapshot that GitHub attaches automatically.
- A Windows build artifact, preferably a zipped `dist/usage-view/` folder.
- SHA256 checksums for downloadable artifacts.
- A short note that the app is unsigned unless code signing has been added.

## Release Checklist

1. Confirm `pyproject.toml`, `src/usage_view/__init__.py`, `README.md`, and
   `CHANGELOG.md` all show the same version.
2. Run the test suite:

   ```powershell
   .venv\Scripts\python.exe -m pytest
   ```

3. Build the recommended one-folder Windows package:

   ```powershell
   .\build.ps1
   ```

4. Smoke-test `dist\usage-view\usage-view.exe` on the release machine.
5. Zip the full `dist\usage-view\` folder. Do not upload only the executable
   from a one-folder build.
6. Create checksums:

   ```powershell
   Get-FileHash .\dist\usage-view.zip -Algorithm SHA256
   ```

7. Commit the release prep changes.
8. Create and push a version tag:

   ```powershell
   git tag v0.4.2
   git push origin main
   git push origin v0.4.2
   ```

9. In GitHub, open the repository, go to **Releases**, choose **Draft a new
   release**, select the tag, paste the changelog notes, and attach the zip.
10. Mark the release as a prerelease if you want early testers before a wider
    announcement.

## Suggested Release Notes Shape

```markdown
## usage-view 0.4.2

Compact always-on-top Windows monitor for Claude.ai, ChatGPT Codex, and GitHub
Copilot usage limits.

### Highlights

- ...

### Download

- `usage-view.zip` contains the Windows app folder.
- Extract it and run `usage-view.exe`.
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
