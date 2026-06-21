"""Generate a ready-to-paste Microsoft Defender false-positive submission sheet.

Windows Defender / SmartScreen sometimes flag the unsigned PyInstaller build.
The fix is to report the exact release artifact to Microsoft as a software
developer false positive. The portal is a web form (no public API for indie
submissions), but everything it asks for can be pre-filled from the built exe.

Run after building, from the repo root:

    .venv\\Scripts\\python.exe tools/wdsi_submission.py

It reads dist/ai-gauge/ai-gauge.exe by default, computes the SHA256, reads the
git tag/commit and Authenticode status, and prints a submission sheet. Paste the
sheet into the portal's "Additional information" box and upload the same exe.

The only two fields you read off the Defender alert itself are the detection
name and the security intelligence version; pass them in to fill the blanks:

    .venv\\Scripts\\python.exe tools/wdsi_submission.py ^
        --detection Trojan:Win32/Wacatac.B!ml --intel 1.0.0.0

Write the sheet to a file (e.g. as a CI artifact) with --out.
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EXE = REPO_ROOT / "dist" / "ai-gauge" / "ai-gauge.exe"
PORTAL_URL = "https://www.microsoft.com/en-us/wdsi/filesubmission"
REPO_URL = "https://github.com/jpajak/ai-gauge"
PLACEHOLDER = "<read off the Defender alert>"


def _version() -> str:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _tag() -> str:
    exact = _git("describe", "--tags", "--exact-match")
    if exact:
        return exact
    nearest = _git("describe", "--tags")
    return nearest or "(untagged)"


def _authenticode(path: Path) -> str:
    """Best-effort Authenticode status via PowerShell; 'n/a' off Windows."""
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-AuthenticodeSignature -LiteralPath '{path}').Status",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        status = out.stdout.strip()
        return status or "Unknown"
    except (OSError, subprocess.CalledProcessError):
        return "n/a (not checked on this platform)"


def render(exe: Path, detection: str, intel: str, contact: str) -> str:
    version = _version()
    sha = _sha256(exe)
    tag = _tag()
    commit = _git("rev-parse", "HEAD") or "(unknown)"
    authenticode = _authenticode(exe)
    return f"""\
================ Microsoft Defender false-positive submission ================
Portal:  {PORTAL_URL}
  Sign in, choose "I'm a software developer", upload the exe below, select
  "Incorrectly detected as malware/malware family", then paste this sheet
  into the "Additional information" box.

--- Upload this exact file ---
File:            {exe}
SHA256:          {sha}
Authenticode:    {authenticode}

--- Read these off the Defender alert and fill in before submitting ---
Detection name:  {detection}
Intelligence ver:{intel}

--- Paste into "Additional information" ---
This file is a false positive. It is AI Gauge {version}, a desktop usage
monitor for AI coding tools (Claude, Codex, GitHub Copilot, OpenRouter),
published by AloeDesk as open source.

Product name:    AI Gauge
Company:         AloeDesk
Version:         {version}
Git tag:         {tag}
Git commit:      {commit}
Source / build:  {REPO_URL}
Contact:         {contact}

The binary is built in CI from the tagged commit above using PyInstaller
(one-folder, no UPX), a common source of heuristic and behavioral false
positives on low-prevalence, unsigned builds.

If this is a behavioral/persistence verdict (e.g. Behavior:Win32/Persistence):
the only persistence AI Gauge creates is an optional "Start at login" setting
the user must explicitly enable in Settings. It registers a per-user Task
Scheduler entry named "AI Gauge" that launches the app's own executable at
logon, runs at LeastPrivilege with no elevation, and is removed when the user
turns the setting off. It is not silent and runs no remote or downloaded code.
The reproducible build and SHA256 can be verified from the public repository.
=============================================================================
"""


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--exe",
        type=Path,
        default=DEFAULT_EXE,
        help=f"path to the built exe (default: {DEFAULT_EXE})",
    )
    parser.add_argument(
        "--detection",
        default=PLACEHOLDER,
        help="detection name shown in the Defender alert",
    )
    parser.add_argument(
        "--intel",
        default=PLACEHOLDER,
        help="security intelligence version shown in the Defender alert",
    )
    parser.add_argument(
        "--contact",
        default=_git("config", "user.email") or "<your contact email>",
        help="contact email for the submission (default: git config user.email)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="also write the sheet to this file",
    )
    args = parser.parse_args(argv)

    if not args.exe.exists():
        print(f"exe not found: {args.exe}\nBuild it first with .\\build.ps1", file=sys.stderr)
        return 1

    sheet = render(args.exe, args.detection, args.intel, args.contact)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(sheet, encoding="utf-8")
        print(f"wrote {args.out}")
    print(sheet)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
