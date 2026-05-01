# Contributing to AI Gauge

Thanks for your interest. AI Gauge is a small cross-platform desktop utility,
so most contributions fall into one of three buckets:

- **Provider layout fixes** — when Claude or Codex change their usage page and
  a tile starts showing `error · layout changed`. These are the most common
  and most welcome PRs.
- **Bug reports and small bug fixes** — anything around tray, widget,
  cookie storage, settings, or refresh logic.
- **New providers or new features** — please open an issue first so we can
  agree on scope before you write code.

## Development environment

Requirements: **Windows 10/11, macOS, or Linux** and **Python 3.11+**. The
test suite runs headlessly under `QT_QPA_PLATFORM=offscreen`; manual smoke
testing is still useful on each OS because tray/menu-bar behavior and native
credential storage are platform-specific.

```powershell
git clone https://github.com/jpajak/ai-gauge.git
cd ai-gauge

py -m venv .venv
.venv\Scripts\pip install -e .[dev]
```

Run the app:

```powershell
.venv\Scripts\python -m aigauge
```

Run the tests:

```powershell
.venv\Scripts\pytest
```

The version-sync check that gates CI:

```powershell
.venv\Scripts\python tools\check_versions.py
```

## Pull request expectations

- Keep changes focused. One PR per logical change.
- Add or update tests when you change non-trivial logic.
- Run `pytest` and `tools/check_versions.py` locally before opening the PR;
  both run in CI on push and pull request.
- If you bump the version, update `pyproject.toml`, `src/aigauge/__init__.py`,
  `README.md`, and add a `CHANGELOG.md` section. CI will fail otherwise.
- Avoid logging cookies, PATs, or full provider response bodies. The logger
  is rotated under the per-OS app-data directory, and users may attach those
  logs to issues.

## Reporting bugs

Use the issue templates under [.github/ISSUE_TEMPLATE/](.github/ISSUE_TEMPLATE/):

- **Bug report** — generic crashes, UI issues, settings glitches.
- **Provider layout broken** — Claude or Codex tile started failing.
- **Feature request** — new ideas worth discussing before code.

For anything that exposes session cookies or tokens, please follow
[SECURITY.md](SECURITY.md) instead of opening a public issue.

## Code style

- Python: standard `from __future__ import annotations`, type hints encouraged,
  no formatter is enforced but please keep diffs minimal.
- Tests: use `pytest` and `pytest-qt`. Avoid mocking the database or the
  filesystem when an in-memory or `tmp_path` alternative works.
- Comments should explain *why*, not *what*. The repo's existing style favors
  short, sparse comments over docstring boilerplate.

## License

By contributing, you agree that your contributions are licensed under the
same MIT license that covers the rest of the project. See [LICENSE](LICENSE).
