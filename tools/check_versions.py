"""Verify the version string is consistent across files that reference it.

Run from the repo root:

    python tools/check_versions.py

Exits non-zero if any tracked file disagrees. Used both by CI (test.yml) and
the release checklist in RELEASING.md.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_pyproject_version() -> str:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    if not m:
        raise SystemExit("could not find version in pyproject.toml")
    return m.group(1)


def _read_init_version() -> str:
    text = (REPO_ROOT / "src" / "aigauge" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not m:
        raise SystemExit("could not find __version__ in src/aigauge/__init__.py")
    return m.group(1)


def _readme_mentions_version(version: str) -> bool:
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    return version in text


def _changelog_has_section(version: str) -> bool:
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    return re.search(rf"(?m)^##\s+{re.escape(version)}\b", text) is not None


def main() -> int:
    pyproject = _read_pyproject_version()
    init = _read_init_version()

    failures: list[str] = []

    if pyproject != init:
        failures.append(
            f"pyproject.toml version ({pyproject}) != src/aigauge/__init__.py version ({init})"
        )

    if not _readme_mentions_version(pyproject):
        failures.append(f"README.md does not mention version {pyproject}")

    if not _changelog_has_section(pyproject):
        failures.append(f"CHANGELOG.md has no '## {pyproject}' section")

    if failures:
        print("Version check FAILED:")
        for line in failures:
            print(f"  - {line}")
        return 1

    print(f"Version check OK: {pyproject}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
