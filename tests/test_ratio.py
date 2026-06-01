from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from aigauge.models import SnapshotStatus, UsageMetric, UsageSnapshot
from aigauge.ratio import (
    MAX_HISTORY,
    RatioStore,
    WeeklyRatioRecord,
    typical_sessions_per_week,
)


def _snap(
    provider: str,
    session_pct: float | None,
    weekly_pct: float | None,
    *,
    fetched_at: datetime,
    session_resets: datetime | None,
    weekly_resets: datetime | None,
    session_idle: bool = False,
    weekly_idle: bool = False,
    status: SnapshotStatus = SnapshotStatus.OK,
) -> UsageSnapshot:
    metrics: list[UsageMetric] = []
    metrics.append(
        UsageMetric(
            "Session",
            session_pct,
            None if session_idle else session_resets,
            "idle" if session_idle else None,
            window=timedelta(hours=5),
        )
    )
    metrics.append(
        UsageMetric(
            "Weekly",
            weekly_pct,
            None if weekly_idle else weekly_resets,
            "idle" if weekly_idle else None,
            window=timedelta(days=7),
        )
    )
    return UsageSnapshot(
        provider=provider, status=status, metrics=metrics, fetched_at=fetched_at
    )


@pytest.fixture
def store(tmp_path):
    return RatioStore(base_dir=tmp_path)


_WEEKLY_BASE = 5.0  # weekly already part-used this week, so readings stay in band
_SESSION_STEPS = (10.0, 30.0, 50.0, 70.0, 90.0)


def _feed_linear_session(store, *, provider="claude", base=None, weekly_per_session=10.0):
    """Drive one session window where weekly climbs `weekly_per_session` per full
    (100-point) session. All readings stay inside the countable [2, 99] band, so
    session climbs 10->90 (80 points) and weekly climbs proportionally. Returns
    the base time used."""
    base = base or datetime(2026, 5, 1, 10, 0, 0)
    s_reset = base + timedelta(hours=5)
    w_reset = base + timedelta(days=7)
    slope = weekly_per_session / 100.0
    for i, session_pct in enumerate(_SESSION_STEPS):
        store.record_snapshot(
            _snap(
                provider,
                session_pct=session_pct,
                weekly_pct=_WEEKLY_BASE + session_pct * slope,
                fetched_at=base + timedelta(minutes=10 * i),
                session_resets=s_reset,
                weekly_resets=w_reset,
            )
        )
    return base


def test_accumulates_within_session_gives_expected_ratio(store):
    _feed_linear_session(store, weekly_per_session=10.0)
    est = store.current_estimate("claude")
    assert est is not None
    assert est.confident
    # 100 session points consumed for 10 weekly points -> 10 sessions/week, 10% each.
    assert est.sessions_per_week == pytest.approx(10.0)
    assert est.weekly_pct_per_session == pytest.approx(10.0)
    assert est.sample_count == 4


def test_session_reset_interval_is_not_counted(store):
    base = _feed_linear_session(store, weekly_per_session=10.0)
    # After the linear session: last=(90, 14), sum_session=80, sum_weekly=8.
    # Session resets: pct drops to 10 and resets_at jumps +5h. Weekly keeps going.
    new_s_reset = base + timedelta(hours=10)
    w_reset = base + timedelta(days=7)
    store.record_snapshot(
        _snap(
            "claude",
            session_pct=10.0,
            weekly_pct=14.5,
            fetched_at=base + timedelta(hours=5, minutes=10),
            session_resets=new_s_reset,
            weekly_resets=w_reset,
        )
    )
    # Resume in the new session window: another 20 points for 2 weekly points.
    store.record_snapshot(
        _snap(
            "claude",
            session_pct=30.0,
            weekly_pct=16.5,
            fetched_at=base + timedelta(hours=5, minutes=20),
            session_resets=new_s_reset,
            weekly_resets=w_reset,
        )
    )
    est = store.current_estimate("claude")
    # The reset crossing (session 90->10, weekly 14->14.5) must be skipped, so the
    # rate stays 10 rather than being polluted by the +0.5 weekly with no session.
    assert est.sessions_per_week == pytest.approx(10.0)
    assert est.weekly_pct_per_session == pytest.approx(10.0)


def test_weekly_rollover_finalizes_record_and_resets_bucket(store, tmp_path):
    base = _feed_linear_session(store, weekly_per_session=10.0)
    # New week: weekly resets_at jumps +7d (and the session is fresh too). The
    # reading is in band so the rollover is detected and the prior week closes.
    next_base = base + timedelta(days=7, minutes=5)
    store.record_snapshot(
        _snap(
            "claude",
            session_pct=8.0,
            weekly_pct=3.0,
            fetched_at=next_base,
            session_resets=next_base + timedelta(hours=5),
            weekly_resets=next_base + timedelta(days=7),
        )
    )
    records = store.history("claude")
    assert len(records) == 1
    rec = records[0]
    assert rec.provider == "claude"
    assert rec.sum_session_delta == pytest.approx(80.0)
    assert rec.sum_weekly_delta == pytest.approx(8.0)
    assert rec.sample_count == 4
    assert (tmp_path / "ratios.json").exists()
    # The current (new) bucket started clean.
    cur = store.current_estimate("claude")
    assert cur.sample_count == 0
    assert not cur.confident


def test_weekly_mid_period_reset_keeps_bucket(store):
    base = datetime(2026, 5, 1, 10, 0, 0)
    s_reset = base + timedelta(hours=5)
    w_reset = base + timedelta(days=7)
    # Accumulate part of the week: 10->30->50, weekly 6->8->10.
    for i, (s, w) in enumerate(((10.0, 6.0), (30.0, 8.0), (50.0, 10.0))):
        store.record_snapshot(
            _snap(
                "claude",
                s,
                w,
                fetched_at=base + timedelta(minutes=10 * i),
                session_resets=s_reset,
                weekly_resets=w_reset,
            )
        )
    assert store._state["claude"].sum_session_delta == pytest.approx(40.0)  # noqa: SLF001

    # Claude zeroes the weekly counter mid-week but keeps the SAME end date.
    # The 0% reading is out of band (no-op); when it climbs back in band the
    # drop is detected with the reset time unchanged -> NOT a new week.
    store.record_snapshot(
        _snap(
            "claude",
            60.0,
            0.0,
            fetched_at=base + timedelta(minutes=40),
            session_resets=s_reset,
            weekly_resets=w_reset,
        )
    )
    store.record_snapshot(
        _snap(
            "claude",
            70.0,
            2.0,
            fetched_at=base + timedelta(minutes=50),
            session_resets=s_reset,
            weekly_resets=w_reset,
        )
    )
    # No spurious week was finalized, and the bucket kept its accumulation.
    assert store.history("claude") == []
    state = store._state["claude"]  # noqa: SLF001
    assert state.sum_session_delta == pytest.approx(40.0)
    assert state.sum_weekly_delta == pytest.approx(4.0)

    # Accumulation resumes within the same week after the reset.
    store.record_snapshot(
        _snap(
            "claude",
            90.0,
            4.0,
            fetched_at=base + timedelta(minutes=60),
            session_resets=s_reset,
            weekly_resets=w_reset,
        )
    )
    assert store.history("claude") == []
    est = store.current_estimate("claude")
    assert est.confident
    assert est.sessions_per_week == pytest.approx(10.0)
    assert est.sample_count == 3


def test_display_estimate_prefers_confident_current_then_history(store):
    base = datetime(2026, 5, 1, 10, 0)
    s_reset = base + timedelta(hours=5)
    w_reset = base + timedelta(days=7)
    # One in-band reading -> state exists but not enough data: calibrating.
    store.record_snapshot(
        _snap(
            "claude",
            10.0,
            6.0,
            fetched_at=base,
            session_resets=s_reset,
            weekly_resets=w_reset,
        )
    )
    early = store.display_estimate("claude")
    assert early is not None
    assert not early.confident
    assert early.source == "current"

    # Fill out a confident week on the same window.
    for i, session_pct in enumerate((30.0, 50.0, 70.0, 90.0), start=1):
        store.record_snapshot(
            _snap(
                "claude",
                session_pct,
                5.0 + session_pct * 0.1,
                fetched_at=base + timedelta(minutes=10 * i),
                session_resets=s_reset,
                weekly_resets=w_reset,
            )
        )
    confident = store.display_estimate("claude")
    assert confident.confident
    assert confident.source == "current"


def test_state_survives_restart(tmp_path):
    base = datetime(2026, 5, 1, 10, 0, 0)
    s_reset = base + timedelta(hours=5)
    w_reset = base + timedelta(days=7)
    steps = (10.0, 30.0, 50.0, 70.0, 90.0)
    first = RatioStore(base_dir=tmp_path)
    for i in range(3):  # 10,30,50
        first.record_snapshot(
            _snap(
                "claude",
                steps[i],
                5.0 + steps[i] * 0.1,
                fetched_at=base + timedelta(minutes=10 * i),
                session_resets=s_reset,
                weekly_resets=w_reset,
            )
        )

    second = RatioStore(base_dir=tmp_path)
    for i in range(3, 5):  # 70,90 continue the same window
        second.record_snapshot(
            _snap(
                "claude",
                steps[i],
                5.0 + steps[i] * 0.1,
                fetched_at=base + timedelta(minutes=10 * i),
                session_resets=s_reset,
                weekly_resets=w_reset,
            )
        )
    est = second.current_estimate("claude")
    assert est.confident
    assert est.sessions_per_week == pytest.approx(10.0)
    assert est.sample_count == 4


def test_history_pruned_to_max(store):
    base = datetime(2026, 1, 1, 10, 0, 0)
    # Each week: an in-band reading that (from week 1 on) rolls the prior week
    # over, then a second in-band reading that accumulates. Plenty of weeks so
    # the kept history must prune down to MAX_HISTORY.
    for w in range(MAX_HISTORY + 6):
        week_base = base + timedelta(days=7 * w)
        s_reset = week_base + timedelta(hours=5)
        w_reset = week_base + timedelta(days=7)
        store.record_snapshot(
            _snap(
                "claude",
                20.0,
                5.0,
                fetched_at=week_base,
                session_resets=s_reset,
                weekly_resets=w_reset,
            )
        )
        store.record_snapshot(
            _snap(
                "claude",
                60.0,
                9.0,
                fetched_at=week_base + timedelta(minutes=10),
                session_resets=s_reset,
                weekly_resets=w_reset,
            )
        )
    assert len(store.history("claude")) == MAX_HISTORY


def test_divide_by_zero_guarded(store):
    base = datetime(2026, 5, 1, 10, 0, 0)
    s_reset = base + timedelta(hours=5)
    w_reset = base + timedelta(days=7)
    # Session climbs but weekly stays pinned at its (in-band) floor: no weekly
    # movement, so the slope has a zero denominator.
    for i, session_pct in enumerate((10.0, 30.0, 50.0, 70.0)):
        store.record_snapshot(
            _snap(
                "claude",
                session_pct,
                2.0,
                fetched_at=base + timedelta(minutes=10 * i),
                session_resets=s_reset,
                weekly_resets=w_reset,
            )
        )
    est = store.current_estimate("claude")
    # sum_weekly_delta == 0 -> N undefined, and not confident (below weekly floor).
    assert est.sessions_per_week is None
    assert not est.confident


def test_codex_floor_reading_not_counted(store):
    base = datetime(2026, 5, 1, 10, 0, 0)
    s_reset = base + timedelta(hours=5)
    w_reset = base + timedelta(days=7)
    # Codex shows ~1% with a fresh countdown before the session really starts.
    store.record_snapshot(
        _snap(
            "codex",
            1.0,
            1.0,
            fetched_at=base,
            session_resets=s_reset,
            weekly_resets=w_reset,
        )
    )
    # Out-of-band floor: no state is created, so nothing anchors on the floor.
    assert store.current_estimate("codex") is None

    # Real usage begins; the first in-band reading anchors the segment.
    store.record_snapshot(
        _snap(
            "codex",
            10.0,
            6.0,
            fetched_at=base + timedelta(minutes=10),
            session_resets=s_reset,
            weekly_resets=w_reset,
        )
    )
    state = store._state["codex"]  # noqa: SLF001
    assert state.last_session_pct == 10.0
    assert state.sum_session_delta == 0.0  # anchored only, no delta yet


def test_saturated_reading_excluded(store):
    base = datetime(2026, 5, 1, 10, 0, 0)
    s_reset = base + timedelta(hours=5)
    w_reset = base + timedelta(days=7)
    for session_pct, weekly_pct in ((10.0, 6.0), (30.0, 8.0)):
        store.record_snapshot(
            _snap(
                "claude",
                session_pct,
                weekly_pct,
                fetched_at=base + timedelta(minutes=10 * session_pct),
                session_resets=s_reset,
                weekly_resets=w_reset,
            )
        )
    state = store._state["claude"]  # noqa: SLF001
    assert state.sum_session_delta == pytest.approx(20.0)

    # Session pinned at 100% (capped): excluded, so it neither adds a sample nor
    # advances last_*; the weekly jump there is never attributed to session use.
    store.record_snapshot(
        _snap(
            "claude",
            100.0,
            12.0,
            fetched_at=base + timedelta(hours=1),
            session_resets=s_reset,
            weekly_resets=w_reset,
        )
    )
    state = store._state["claude"]  # noqa: SLF001
    assert state.sum_session_delta == pytest.approx(20.0)
    assert state.sum_weekly_delta == pytest.approx(2.0)
    assert state.last_session_pct == 30.0  # frozen at the last in-band reading


def test_idle_snapshot_is_noop_and_freezes_last(store):
    base = datetime(2026, 5, 1, 10, 0, 0)
    s_reset = base + timedelta(hours=5)
    w_reset = base + timedelta(days=7)
    # Three climbing readings: 10, 30, 50 (all in band).
    steps = (10.0, 30.0, 50.0, 70.0, 90.0)
    for i in range(3):
        store.record_snapshot(
            _snap(
                "claude",
                steps[i],
                5.0 + steps[i] * 0.1,
                fetched_at=base + timedelta(minutes=10 * i),
                session_resets=s_reset,
                weekly_resets=w_reset,
            )
        )
    before = store._state["claude"]  # noqa: SLF001
    frozen_session = before.last_session_pct
    frozen_sum = before.sum_session_delta
    frozen_samples = before.sample_count

    # An idle session reading must not advance last_* nor accumulate.
    store.record_snapshot(
        _snap(
            "claude",
            0.0,
            50.0,
            fetched_at=base + timedelta(minutes=35),
            session_resets=s_reset,
            weekly_resets=w_reset,
            session_idle=True,
        )
    )
    after = store._state["claude"]  # noqa: SLF001
    assert after.last_session_pct == frozen_session
    assert after.sum_session_delta == frozen_sum
    assert after.sample_count == frozen_samples

    # Resuming the same session window keeps the rate clean (ds measured from 50,
    # not from the idle 0, and the idle weekly spike is never counted).
    for i in range(3, 5):  # 70, 90
        store.record_snapshot(
            _snap(
                "claude",
                steps[i],
                5.0 + steps[i] * 0.1,
                fetched_at=base + timedelta(minutes=10 * i),
                session_resets=s_reset,
                weekly_resets=w_reset,
            )
        )
    est = store.current_estimate("claude")
    assert est.confident
    assert est.sessions_per_week == pytest.approx(10.0)
    assert est.sample_count == 4  # the idle reading did not add a sample


def test_fully_idle_account_creates_no_state(store, tmp_path):
    base = datetime(2026, 5, 1, 10, 0, 0)
    for i in range(3):
        store.record_snapshot(
            _snap(
                "claude",
                0.0,
                0.0,
                fetched_at=base + timedelta(minutes=10 * i),
                session_resets=None,
                weekly_resets=None,
                session_idle=True,
                weekly_idle=True,
            )
        )
    assert store.current_estimate("claude") is None
    assert store.history("claude") == []
    assert not (tmp_path / "ratios.json").exists()


def test_providers_tracked_independently(store):
    base = datetime(2026, 5, 1, 10, 0, 0)
    _feed_linear_session(store, provider="claude", base=base, weekly_per_session=10.0)
    _feed_linear_session(store, provider="codex", base=base, weekly_per_session=3.0)
    claude = store.current_estimate("claude")
    codex = store.current_estimate("codex")
    assert claude.sessions_per_week == pytest.approx(10.0)
    # 100 session points for 3 weekly points -> ~33 sessions/week.
    assert codex.sessions_per_week == pytest.approx(100.0 / 3.0)


def _record(n_session: float, n_weekly: float, samples: int = 8) -> WeeklyRatioRecord:
    return WeeklyRatioRecord(
        provider="claude",
        week_started_at="2026-05-01T10:00:00",
        week_ended_at="2026-05-06T10:00:00",
        weekly_resets_at="2026-05-08T10:00:00",
        sum_session_delta=n_session,
        sum_weekly_delta=n_weekly,
        sample_count=samples,
    )


def test_typical_is_median_of_confident_weeks():
    # N values 10, 8, 12 (each 80 session pts) -> median 10 over 3 weeks.
    records = [_record(80.0, 8.0), _record(80.0, 10.0), _record(80.0, 80.0 / 12.0)]
    result = typical_sessions_per_week(records)
    assert result is not None
    median, weeks = result
    assert weeks == 3
    assert median == pytest.approx(10.0)


def test_typical_needs_min_weeks_and_skips_low_confidence():
    # One confident week is below the min; a low-confidence week is ignored.
    assert typical_sessions_per_week([_record(80.0, 8.0)]) is None
    low = _record(5.0, 0.5, samples=1)  # below confidence floors
    assert typical_sessions_per_week([_record(80.0, 8.0), low]) is None


def test_error_snapshot_ignored(store, tmp_path):
    store.record_snapshot(
        _snap(
            "claude",
            None,
            None,
            fetched_at=datetime(2026, 5, 1, 10, 0),
            session_resets=None,
            weekly_resets=None,
            status=SnapshotStatus.ERROR,
        )
    )
    assert store.current_estimate("claude") is None
    assert not (tmp_path / "ratio_state.json").exists()
