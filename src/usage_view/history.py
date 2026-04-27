from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from .config import app_data_dir
from .models import SnapshotStatus, UsageSnapshot

log = logging.getLogger("usage_view.history")

# A new resets_at must be at least this much later than the prior one to count
# as a period rollover rather than minute-level jitter from the rendered text
# ("2h 59m" vs "2h 55m"). The smallest real period (Claude/Codex session) is
# 5 hours, so a 2-hour gap is comfortably below that yet far above any jitter.
_ROLLOVER_THRESHOLD = timedelta(hours=2)


def _isoformat(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


@dataclass
class PeriodRecord:
    """One in-flight or finalized usage period for a (provider, label) metric.

    `peak_pct` is the highest percent observed within this period. Because
    percent-used is monotonic until reset, the latest observation also IS the
    peak — but we track explicitly to be robust against any non-monotonic
    surprises.
    """

    provider: str
    label: str
    resets_at: str
    started_at: str
    last_seen_at: str
    peak_pct: float


def _state_key(provider: str, label: str) -> str:
    return f"{provider}::{label}"


def _canonical_record_key(record: PeriodRecord) -> str:
    # Codex's short window used to be displayed as "5 hour"; keep any in-flight
    # history under the new shared "Session" label after upgrading.
    if record.provider == "codex" and record.label == "5 hour":
        record.label = "Session"
    return _state_key(record.provider, record.label)


class HistoryStore:
    """Tracks per-period peaks and appends one record to history.jsonl on rollover.

    State layout under app_data_dir():
        current.json     — overwritten each scrape; map of {provider::label: PeriodRecord}
        history.jsonl    — append-only; one line per closed period
    """

    def __init__(self, base_dir: Path | None = None):
        self._dir = base_dir or app_data_dir()
        self._current_path = self._dir / "current.json"
        self._history_path = self._dir / "history.jsonl"
        self._state: dict[str, PeriodRecord] = self._load_current()

    # ---- public API ----

    def record_snapshot(self, snapshot: UsageSnapshot) -> list[PeriodRecord]:
        """Update in-flight state from a snapshot. Returns any periods closed.

        Closed periods are also appended to history.jsonl. Errored or auth
        snapshots are ignored — no metrics to record.
        """
        if snapshot.status != SnapshotStatus.OK:
            return []

        closed: list[PeriodRecord] = []
        observed_at = snapshot.fetched_at
        seen_keys: set[str] = set()

        for metric in snapshot.metrics:
            if metric.percent_used is None or metric.resets_at is None:
                continue
            key = _state_key(snapshot.provider, metric.label)
            seen_keys.add(key)
            new_resets_iso = _isoformat(metric.resets_at)
            existing = self._state.get(key)

            if existing is None:
                self._state[key] = PeriodRecord(
                    provider=snapshot.provider,
                    label=metric.label,
                    resets_at=new_resets_iso,
                    started_at=_isoformat(observed_at),
                    last_seen_at=_isoformat(observed_at),
                    peak_pct=metric.percent_used,
                )
                continue

            old_resets = _parse_iso(existing.resets_at)
            rolled_over = (
                old_resets is not None
                and metric.resets_at >= old_resets + _ROLLOVER_THRESHOLD
            )
            if rolled_over:
                self._append_history(existing)
                closed.append(existing)
                self._state[key] = PeriodRecord(
                    provider=snapshot.provider,
                    label=metric.label,
                    resets_at=new_resets_iso,
                    started_at=_isoformat(observed_at),
                    last_seen_at=_isoformat(observed_at),
                    peak_pct=metric.percent_used,
                )
            else:
                # Same period (or jitter). Keep started_at; advance last_seen
                # and lift the peak. resets_at locks to the most recent reading
                # so steady drift doesn't slowly pull us toward a false rollover.
                existing.resets_at = new_resets_iso
                existing.last_seen_at = _isoformat(observed_at)
                if metric.percent_used > existing.peak_pct:
                    existing.peak_pct = metric.percent_used

        self._save_current()
        return closed

    def iter_history(self) -> Iterable[PeriodRecord]:
        if not self._history_path.exists():
            return
        with self._history_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield PeriodRecord(**json.loads(line))
                except (json.JSONDecodeError, TypeError):
                    continue

    # ---- internals ----

    def _load_current(self) -> dict[str, PeriodRecord]:
        if not self._current_path.exists():
            return {}
        try:
            data = json.loads(self._current_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        out: dict[str, PeriodRecord] = {}
        if not isinstance(data, dict):
            return out
        for key, raw in data.items():
            if not isinstance(raw, dict):
                continue
            try:
                record = PeriodRecord(**raw)
            except TypeError:
                continue
            out.setdefault(_canonical_record_key(record), record)
        return out

    def _save_current(self) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            payload = {key: asdict(rec) for key, rec in self._state.items()}
            self._current_path.write_text(
                json.dumps(payload, indent=2),
                encoding="utf-8",
            )
        except OSError:
            log.exception("failed to write current.json")

    def _append_history(self, record: PeriodRecord) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            with self._history_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(record)) + "\n")
            log.info(
                "history closed period provider=%s label=%s peak=%.1f%%",
                record.provider, record.label, record.peak_pct,
            )
        except OSError:
            log.exception("failed to append history.jsonl")
