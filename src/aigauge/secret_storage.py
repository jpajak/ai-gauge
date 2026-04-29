"""Encrypted on-disk storage for values too large for Windows Credential Manager.

Windows Credential Manager caps the credential blob at ~2560 bytes, which is fine
for short tokens (GitHub PAT) but fails for long session JWTs (ChatGPT's
__Secure-next-auth.session-token can be 5-10KB).

We store these in %APPDATA%/ai-gauge/secrets.dat, encrypted with DPAPI
(CryptProtectData) — same per-user encryption that pre-v127 Chrome used. No
new Python dependencies; calls into crypt32.dll via ctypes.

This module is Windows-only by design. On non-Windows hosts (used during
cross-platform development of pure-Python helpers), writes are routed to a
plaintext file under a sandboxed test directory and a loud warning is logged.
Production callers should never reach the non-Windows branch.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import logging
import os
import sys
from pathlib import Path

from .config import app_data_dir

log = logging.getLogger("aigauge.secret_storage")

_SECRETS_FILENAME = "secrets.dat"

# Opt-in escape hatch for the cross-platform test suite. When unset (the
# normal case) writes on non-Windows refuse loudly so a misconfigured macOS
# or Linux box cannot silently produce an unencrypted secrets.dat next to a
# real cookie.
_ALLOW_PLAINTEXT_ENV = "AIGAUGE_ALLOW_PLAINTEXT_SECRETS"


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wt.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


if sys.platform == "win32":
    _CRYPT32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _CRYPT32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wt.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wt.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    _CRYPT32.CryptProtectData.restype = wt.BOOL

    _CRYPT32.CryptUnprotectData.argtypes = _CRYPT32.CryptProtectData.argtypes
    _CRYPT32.CryptUnprotectData.restype = wt.BOOL


def _to_blob(data: bytes) -> _DataBlob:
    buf = (ctypes.c_byte * len(data)).from_buffer_copy(data)
    return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))


def _from_blob(blob: _DataBlob) -> bytes:
    out = ctypes.string_at(blob.pbData, blob.cbData)
    _KERNEL32.LocalFree(blob.pbData)
    return out


def _protect(plaintext: bytes) -> bytes:
    in_blob = _to_blob(plaintext)
    out_blob = _DataBlob()
    ok = _CRYPT32.CryptProtectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptProtectData failed")
    return _from_blob(out_blob)


def _unprotect(ciphertext: bytes) -> bytes:
    in_blob = _to_blob(ciphertext)
    out_blob = _DataBlob()
    ok = _CRYPT32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData failed")
    return _from_blob(out_blob)


def _secrets_path() -> Path:
    return app_data_dir() / _SECRETS_FILENAME


def _load_all() -> dict[str, str]:
    path = _secrets_path()
    if not path.exists():
        return {}
    try:
        raw = path.read_bytes()
        if not raw:
            return {}
        if sys.platform == "win32":
            decrypted = _unprotect(raw).decode("utf-8")
        else:
            decrypted = raw.decode("utf-8")  # plaintext fallback for non-Windows dev
        loaded = json.loads(decrypted)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _save_all(data: dict[str, str]) -> None:
    path = _secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data).encode("utf-8")
    if sys.platform == "win32":
        path.write_bytes(_protect(payload))
        return
    if os.environ.get(_ALLOW_PLAINTEXT_ENV) == "1":
        log.warning(
            "secret_storage: writing PLAINTEXT secrets.dat on non-Windows host "
            "(AIGAUGE_ALLOW_PLAINTEXT_SECRETS=1). This is a development-only "
            "escape hatch; do not use it with real provider cookies."
        )
        path.write_bytes(payload)
        return
    raise RuntimeError(
        "secret_storage: refusing to write secrets on non-Windows host. "
        "DPAPI encryption is unavailable, so writing here would store the "
        "value in plaintext. Run on Windows, or set "
        "AIGAUGE_ALLOW_PLAINTEXT_SECRETS=1 to opt in (development only)."
    )


def save_secret(name: str, value: str | None) -> None:
    data = _load_all()
    if value:
        data[name] = value
    else:
        data.pop(name, None)
    _save_all(data)


def load_secret(name: str) -> str | None:
    return _load_all().get(name)
