"""Generate a PyInstaller Windows version resource for AI Gauge."""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _version() -> str:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _version_tuple(version: str) -> tuple[int, int, int, int]:
    parts = [int(part) for part in re.findall(r"\d+", version)[:4]]
    parts.extend([0] * (4 - len(parts)))
    return tuple(parts[:4])


def _string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def render(version: str) -> str:
    version_tuple = _version_tuple(version)
    dotted = ".".join(str(part) for part in version_tuple)
    strings = {
        "CompanyName": "AloeDesk",
        "FileDescription": "AloeDesk AI Gauge utility",
        "FileVersion": dotted,
        "InternalName": "ai-gauge",
        "LegalCopyright": "Copyright (c) AloeDesk",
        "OriginalFilename": "ai-gauge.exe",
        "ProductName": "AI Gauge",
        "ProductVersion": version,
    }
    string_rows = ",\n          ".join(
        f"StringStruct('{_string(key)}', '{_string(value)}')" for key, value in strings.items()
    )
    tuple_text = ", ".join(str(part) for part in version_tuple)
    return f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({tuple_text}),
    prodvers=({tuple_text}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          {string_rows}
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python tools/write_pyinstaller_version_info.py <output>", file=sys.stderr)
        return 2
    output = Path(argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(_version()), encoding="utf-8")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))