from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from usage_view.history import HistoryStore, PeriodRecord
from usage_view.models import SnapshotStatus, UsageMetric, UsageSnapshot


def _snapshot(
    provider: str,
    metrics: list[UsageMetric],
    fetched_at: datetime,
    status: SnapshotStatus = SnapshotStatus.OK,
) -> UsageSnapshot:
    return UsageSnapshot(
        provider=provider,
        status=status,
        metrics=metrics,
        fetched_at=fetched_at,
    )


@pytest.fixture
def store(tmp_path):
    return HistoryStore(base_dir=tmp_path)


def test_first_snapshot_starts_period_without_writing_history(store, tmp_path):
    fetched = datetime(2026, 4, 27, 10, 0, 0)
    resets = fetched + timedelta(hours=5)
    closed = store.record_snapshot(
        _snapshot(
            "claude",
            [UsageMetric(label="Session", percent_used=12.0, resets_at=resets)],
            fetched,
        )
    )
    assert closed == []
    assert (tmp_path / "current.json").exists()
    assert not (tmp_path / "history.jsonl").exists()


def test_same_period_lifts_peak_no_rollover(store, tmp_path):
    base = datetime(2026, 4, 27, 10, 0, 0)
    resets = base + timedelta(hours=5)
    store.record_snapshot(
        _snapshot("claude", [UsageMetric("Session", 12.0, resets)], base)
    )
    closed = store.record_snapshot(
        _snapshot("claude", [UsageMetric("Session", 47.0, resets)], base + timedelta(minutes=10))
    )
    assert closed == []
    assert not (tmp_path / "history.jsonl").exists()


def test_jitter_in_resets_at_not_treated_as_rollover(store, tmp_path):
    base = datetime(2026, 4, 27, 10, 0, 0)
    resets = base + timedelta(hours=5)
    store.record_snapshot(
        _snapshot("claude", [UsageMetric("Session", 12.0, resets)], base)
    )
    # Reset text rounds to nearest minute — small forward jitter is normal.
    closed = store.record_snapshot(
        _snapshot(
            "claude",
            [UsageMetric("Session", 50.0, resets + timedelta(minutes=3))],
            base + timedelta(minutes=15),
        )
    )
    assert closed == []
    assert not (tmp_path / "history.jsonl").exists()


def test_rollover_finalizes_period_and_appends_history(store, tmp_path):
    base = datetime(2026, 4, 27, 10, 0, 0)
    resets1 = base + timedelta(hours=5)
    store.record_snapshot(
        _snapshot("claude", [UsageMetric("Session", 12.0, resets1)], base)
    )
    store.record_snapshot(
        _snapshot(
            "claude",
            [UsageMetric("Session", 87.0, resets1)],
            base + timedelta(hours=4),
        )
    )

    # New session starts; resets_at jumps by ~5h.
    resets2 = resets1 + timedelta(hours=5)
    closed = store.record_snapshot(
        _snapshot(
            "claude",
            [UsageMetric("Session", 3.0, resets2)],
            base + timedelta(hours=5, minutes=10),
        )
    )

    assert len(closed) == 1
    assert closed[0].provider == "claude"
    assert closed[0].label == "Session"
    assert closed[0].peak_pct == 87.0

    history_lines = [line for line in (tmp_path / "history.jsonl").read_text().splitlines() if line]
    assert len(history_lines) == 1
    records = list(store.iter_history())
    assert len(records) == 1
    assert records[0].peak_pct == 87.0


def test_error_snapshot_is_ignored(store, tmp_path):
    fetched = datetime(2026, 4, 27, 10, 0, 0)
    closed = store.record_snapshot(
        _snapshot(
            "claude",
            [],
            fetched,
            status=SnapshotStatus.ERROR,
        )
    )
    assert closed == []
    assert not (tmp_path / "current.json").exists()
    assert not (tmp_path / "history.jsonl").exists()


def test_metrics_without_resets_or_percent_are_skipped(store, tmp_path):
    fetched = datetime(2026, 4, 27, 10, 0, 0)
    store.record_snapshot(
        _snapshot(
            "claude",
            [
                UsageMetric("Idle Weekly", percent_used=0.0, resets_at=None),
                UsageMetric("No Pct", percent_used=None, resets_at=fetched + timedelta(days=7)),
            ],
            fetched,
        )
    )
    # Neither metric should produce in-flight state.
    state = store._state  # noqa: SLF001
    assert state == {}


def test_state_survives_restart(tmp_path):
    base = datetime(2026, 4, 27, 10, 0, 0)
    resets = base + timedelta(hours=5)
    first = HistoryStore(base_dir=tmp_path)
    first.record_snapshot(
        _snapshot("claude", [UsageMetric("Session", 25.0, resets)], base)
    )

    second = HistoryStore(base_dir=tmp_path)
    closed = second.record_snapshot(
        _snapshot(
            "claude",
            [UsageMetric("Session", 70.0, resets)],
            base + timedelta(hours=2),
        )
    )
    assert closed == []
    rec = second._state["claude::Session"]  # noqa: SLF001
    assert rec.peak_pct == 70.0


def test_per_provider_metrics_tracked_independently(store, tmp_path):
    base = datetime(2026, 4, 27, 10, 0, 0)
    claude_resets = base + timedelta(hours=5)
    codex_resets = base + timedelta(hours=5)
    store.record_snapshot(
        _snapshot(
            "claude",
            [UsageMetric("Session", 30.0, claude_resets)],
            base,
        )
    )
    store.record_snapshot(
        _snapshot(
            "codex",
            [UsageMetric("Session", 60.0, codex_resets)],
            base,
        )
    )
    # Roll claude over; codex must remain unaffected.
    closed = store.record_snapshot(
        _snapshot(
            "claude",
            [UsageMetric("Session", 5.0, claude_resets + timedelta(hours=5))],
            base + timedelta(hours=5, minutes=5),
        )
    )
    assert len(closed) == 1
    assert closed[0].provider == "claude"
    assert "codex::Session" in store._state  # noqa: SLF001


def test_load_migrates_old_codex_session_label(tmp_path):
    now = datetime(2026, 4, 27, 12, 0)
    (tmp_path / "current.json").write_text(
        json.dumps(
            {
                "codex::5 hour": {
                    "provider": "codex",
                    "label": "5 hour",
                    "resets_at": now.isoformat(),
                    "started_at": now.isoformat(),
                    "last_seen_at": now.isoformat(),
                    "peak_pct": 42.0,
                }
            }
        ),
        encoding="utf-8",
    )

    store = HistoryStore(base_dir=tmp_path)

    assert "codex::5 hour" not in store._state  # noqa: SLF001
    assert store._state["codex::Session"].label == "Session"  # noqa: SLF001


def test_peak_does_not_decrease_within_period(store):
    base = datetime(2026, 4, 27, 10, 0, 0)
    resets = base + timedelta(hours=5)
    store.record_snapshot(_snapshot("claude", [UsageMetric("Session", 60.0, resets)], base))
    store.record_snapshot(
        _snapshot(
            "claude",
            [UsageMetric("Session", 55.0, resets)],
            base + timedelta(minutes=10),
        )
    )
    rec: PeriodRecord = store._state["claude::Session"]  # noqa: SLF001
    assert rec.peak_pct == 60.0
