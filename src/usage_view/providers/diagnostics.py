from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse, urlunparse


def _safe_url(value: Any) -> tuple[str, bool, bool]:
    raw = str(value or "")
    if not raw:
        return "", False, False
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw.split("?", maxsplit=1)[0].split("#", maxsplit=1)[0][:200], "?" in raw, "#" in raw
    safe = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    return safe[:300], bool(parsed.query), bool(parsed.fragment)


def page_diagnosis(
    payload: dict[str, Any],
    expected_rows: tuple[str, ...],
) -> dict[str, Any]:
    """Small, non-sensitive summary of what the rendered provider page looked like."""
    title = str(payload.get("title") or "")
    body = str(payload.get("body_text") or "")
    page_text = f"{title} {body}".lower()
    url, has_query, has_fragment = _safe_url(payload.get("url"))
    return {
        "url": url,
        "url_has_query": has_query,
        "url_has_fragment": has_fragment,
        "title": title[:120],
        "logged_out": bool(payload.get("logged_out")),
        "rows": {key: payload.get(key) is not None for key in expected_rows},
        "body_len": len(body),
        "has_percent": "%" in body,
        "has_usage_text": any(
            marker in page_text
            for marker in (
                "usage",
                "plan usage limits",
                "usage limit",
            )
        ),
        "has_security_text": any(
            marker in page_text
            for marker in (
                "cloudflare",
                "verify you are human",
                "just a moment",
                "security verification",
            )
        ),
        "has_connectivity_text": any(
            marker in page_text
            for marker in (
                "can't reach",
                "check your connection",
                "try again",
            )
        ),
        "has_signed_in_shell_text": any(
            marker in page_text
            for marker in (
                "new chat",
                "recents",
                "projects",
                "codex",
                "tasks",
            )
        ),
    }


def log_page_diagnosis(
    logger: logging.Logger,
    *,
    provider: str,
    classification: str,
    payload: dict[str, Any],
    expected_rows: tuple[str, ...],
    level: int = logging.INFO,
) -> None:
    diagnosis = page_diagnosis(payload, expected_rows)
    rows = ",".join(
        f"{key}={int(present)}" for key, present in diagnosis["rows"].items()
    )
    flags = (
        f"percent={int(diagnosis['has_percent'])},"
        f"usage={int(diagnosis['has_usage_text'])},"
        f"security={int(diagnosis['has_security_text'])},"
        f"connectivity={int(diagnosis['has_connectivity_text'])},"
        f"signed_in_shell={int(diagnosis['has_signed_in_shell_text'])}"
    )
    logger.log(
        level,
        "provider page diagnosis provider=%s classification=%s url=%s "
        "url_query=%s url_fragment=%s title=%r logged_out=%s rows=%s flags=%s body_len=%s",
        provider,
        classification,
        diagnosis["url"],
        diagnosis["url_has_query"],
        diagnosis["url_has_fragment"],
        diagnosis["title"],
        diagnosis["logged_out"],
        rows,
        flags,
        diagnosis["body_len"],
    )
