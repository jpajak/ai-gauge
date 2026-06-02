from __future__ import annotations

import json
import logging
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import app_data_dir
from .models import SnapshotStatus, UsageSnapshot

log = logging.getLogger("aigauge.ratio")

# Mirrors history._ROLLOVER_THRESHOLD: a resets_at reading must jump forward by
# at least this much to count as a real period rollover rather than minute-level
# jitter from the rendered countdown text. The smallest real period is the
# 5-hour session, so a 2-hour gap is safely below it yet far above any jitter.
_ROLLOVER_THRESHOLD = timedelta(hours=2)

# How many finalized weekly ratios to keep per provider (auto-pruned).
MAX_HISTORY = 26

# A current/last estimate is only shown once we have watched enough usage for
# the slope to be stable. Below these the ratio is reported as "calibrating".
MIN_SESSION_DELTA = 30.0  # total session-percent points consumed within sessions
MIN_WEEKLY_DELTA = 2.0  # total weekly-percent points accrued over the same span
MIN_SAMPLES = 3

# Only count readings where each meter is meaningfully mid-window. Providers
# report a low floor at the very start of a window (Codex shows ~1% with a fresh
# ~5h countdown before a session has really started) and pin the meter near the
# top once a limit is hit. Neither end moves in proportion to real consumption,
# so including them would skew the slope; we exclude both tails. Because a meter
# is monotonic within its window, the weekly floor only excludes the first ~2%
# of each week, which is negligible.
MIN_COUNTABLE_PCT = 2.0
MAX_COUNTABLE_PCT = 99.0

_SESSION_LABEL = "session"
_WEEKLY_LABEL = "weekly"


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
class WeeklyRatioRecord:
    """One finalized weekly period's session-to-weekly accumulation.

    The headline numbers (sessions/week, weekly% per session) are DERIVED from
    the raw sums via the helpers below so there is a single source of truth.
    """

    provider: str
    week_started_at: str
    week_ended_at: str
    weekly_resets_at: str
    sum_session_delta: float
    sum_weekly_delta: float
    sample_count: int


@dataclass
class RatioTrackerState:
    """Live, in-progress accumulation for a (provider) session/weekly pair."""

    provider: str
    last_session_pct: float | None = None
    last_weekly_pct: float | None = None
    last_session_resets_at: str | None = None
    last_weekly_resets_at: str | None = None
    bucket_started_at: str | None = None
    bucket_weekly_resets_at: str | None = None
    sum_session_delta: float = 0.0
    sum_weekly_delta: float = 0.0
    sample_count: int = 0
    bucket_last_seen_at: str | None = None


@dataclass
class RatioEstimate:
    """A display-ready ratio derived from a bucket or a finalized record."""

    sessions_per_week: float | None
    weekly_pct_per_session: float | None
    coverage_pct: float
    sample_count: int
    confident: bool
    source: str  # "current" | "history"
    session_delta: float = 0.0  # observed session-percent climb (calibration progress)


def sessions_per_week(sum_session_delta: float, sum_weekly_delta: float) -> float | None:
    """Full sessions before the weekly cap is reached (N = Σds / Σdw)."""
    if sum_weekly_delta <= 0:
        return None
    return sum_session_delta / sum_weekly_delta


def weekly_pct_per_session(
    sum_session_delta: float, sum_weekly_delta: float
) -> float | None:
    """Weekly percent consumed by one full session (R = 100 * Σdw / Σds)."""
    if sum_session_delta <= 0:
        return None
    return 100.0 * sum_weekly_delta / sum_session_delta


def is_confident(
    sum_session_delta: float, sum_weekly_delta: float, sample_count: int
) -> bool:
    return (
        sum_session_delta >= MIN_SESSION_DELTA
        and sum_weekly_delta >= MIN_WEEKLY_DELTA
        and sample_count >= MIN_SAMPLES
    )


def _estimate_from(
    sum_session_delta: float,
    sum_weekly_delta: float,
    sample_count: int,
    source: str,
) -> RatioEstimate:
    confident = is_confident(sum_session_delta, sum_weekly_delta, sample_count)
    return RatioEstimate(
        sessions_per_week=(
            sessions_per_week(sum_session_delta, sum_weekly_delta)
            if confident
            else None
        ),
        weekly_pct_per_session=(
            weekly_pct_per_session(sum_session_delta, sum_weekly_delta)
            if confident
            else None
        ),
        coverage_pct=sum_weekly_delta,
        sample_count=sample_count,
        confident=confident,
        source=source,
        session_delta=sum_session_delta,
    )


def record_estimate(record: WeeklyRatioRecord) -> RatioEstimate:
    return _estimate_from(
        record.sum_session_delta,
        record.sum_weekly_delta,
        record.sample_count,
        "history",
    )


def _merge_history_records(records: list[WeeklyRatioRecord]) -> WeeklyRatioRecord:
    first = records[0]

    def _min_iso(values: list[str]) -> str:
        parsed = [dt for dt in (_parse_iso(value) for value in values) if dt is not None]
        return _isoformat(min(parsed)) if parsed else values[0]

    def _max_iso(values: list[str]) -> str:
        parsed = [dt for dt in (_parse_iso(value) for value in values) if dt is not None]
        return _isoformat(max(parsed)) if parsed else values[-1]

    return WeeklyRatioRecord(
        provider=first.provider,
        week_started_at=_min_iso([record.week_started_at for record in records]),
        week_ended_at=_max_iso([record.week_ended_at for record in records]),
        weekly_resets_at=_max_iso([record.weekly_resets_at for record in records]),
        sum_session_delta=sum(record.sum_session_delta for record in records),
        sum_weekly_delta=sum(record.sum_weekly_delta for record in records),
        sample_count=sum(record.sample_count for record in records),
    )


def _normalize_history_records(
    records: list[WeeklyRatioRecord],
) -> list[WeeklyRatioRecord]:
    """Collapse old split records that represent the same weekly reset.

    Early builds could finalize multiple fragments for one Claude weekly period
    when the weekly percent dropped without the reset date moving. Reset times
    are parsed from rendered text and can jitter by seconds, so records within
    the rollover threshold belong to the same weekly bucket.
    """
    if len(records) < 2:
        return records

    def _reset_key(record: WeeklyRatioRecord) -> datetime:
        return _parse_iso(record.weekly_resets_at) or datetime.min

    normalized: list[WeeklyRatioRecord] = []
    for record in sorted(records, key=_reset_key):
        reset_at = _parse_iso(record.weekly_resets_at)
        if not normalized or reset_at is None:
            normalized.append(record)
            continue
        previous = normalized[-1]
        previous_reset = _parse_iso(previous.weekly_resets_at)
        if previous_reset is None or abs(reset_at - previous_reset) >= _ROLLOVER_THRESHOLD:
            normalized.append(record)
            continue

        group = [previous, record]
        confident = [
            item
            for item in group
            if is_confident(
                item.sum_session_delta,
                item.sum_weekly_delta,
                item.sample_count,
            )
        ]
        normalized[-1] = _merge_history_records(confident or group)
    return normalized


def typical_sessions_per_week(
    records: list[WeeklyRatioRecord], *, min_weeks: int = 2
) -> tuple[float, int] | None:
    """Median sessions/week across confident finalized weeks.

    Median rather than mean so one unusual week (e.g. a light week measured off a
    short burst) doesn't drag the long-run figure. Returns (median, weeks_used),
    or None until at least ``min_weeks`` confident weeks exist.
    """
    values: list[float] = []
    for record in records:
        if not is_confident(
            record.sum_session_delta, record.sum_weekly_delta, record.sample_count
        ):
            continue
        value = sessions_per_week(record.sum_session_delta, record.sum_weekly_delta)
        if value is not None:
            values.append(value)
    if len(values) < min_weeks:
        return None
    return statistics.median(values), len(values)


@dataclass
class _LiveMetric:
    pct: float
    resets_at: datetime


def _live_pair(snapshot: UsageSnapshot) -> tuple[_LiveMetric, _LiveMetric] | None:
    """Return (session, weekly) only when BOTH windows are actively counting.

    A metric is countable iff it has a percent, a reset time, is not flagged
    idle, and sits within [MIN_COUNTABLE_PCT, MAX_COUNTABLE_PCT]. Idle/unused
    windows set resets_at=None (see providers.idle and
    providers._common.idle_session_weekly_metrics); start-of-window floors and
    saturated tails fall outside the band. Any of these makes this return None
    and the snapshot becomes a no-op upstream (last_* stays frozen), so reset
    detection still works off the last in-band reading.
    """
    session: _LiveMetric | None = None
    weekly: _LiveMetric | None = None
    for metric in snapshot.metrics:
        label = metric.label.lower()
        if label not in (_SESSION_LABEL, _WEEKLY_LABEL):
            continue
        if (
            metric.percent_used is None
            or metric.resets_at is None
            or metric.reset_label == "idle"
            or not (MIN_COUNTABLE_PCT <= metric.percent_used <= MAX_COUNTABLE_PCT)
        ):
            continue
        live = _LiveMetric(pct=metric.percent_used, resets_at=metric.resets_at)
        if label == _SESSION_LABEL:
            session = live
        else:
            weekly = live
    if session is None or weekly is None:
        return None
    return session, weekly


class RatioStore:
    """Accumulates the session-to-weekly exchange rate from usage snapshots.

    State layout under app_data_dir():
        ratio_state.json — overwritten each snapshot; {provider: RatioTrackerState}
        ratios.json      — finalized weekly records; {provider: [WeeklyRatioRecord]}

    The estimate is built by summing within-session percent increments: while a
    session is counting, Δweekly/Δsession is the constant session_budget /
    weekly_budget, so Σdw / Σds over a week gives a stable rate even though the
    page only reports integer percents.
    """

    def __init__(self, base_dir: Path | None = None):
        self._dir = base_dir or app_data_dir()
        self._state_path = self._dir / "ratio_state.json"
        self._history_path = self._dir / "ratios.json"
        self._state: dict[str, RatioTrackerState] = self._load_state()
        self._history, history_dirty = self._load_history()
        if history_dirty:
            self._save_history()

    # ---- public API ----

    def record_snapshot(self, snapshot: UsageSnapshot) -> None:
        """Fold one snapshot into the live accumulation. No-op when idle."""
        if snapshot.status != SnapshotStatus.OK:
            return
        pair = _live_pair(snapshot)
        if pair is None:
            # Idle gate: either window isn't counting. Do nothing and, crucially,
            # leave last_* frozen so a stale pre-idle reading is never paired
            # against a post-idle reading.
            return
        session, weekly = pair
        observed_at = snapshot.fetched_at
        state = self._state.get(snapshot.provider)

        if state is None:
            self._state[snapshot.provider] = self._fresh_state(
                snapshot.provider, session, weekly, observed_at
            )
            self._save_state()
            return

        last_session = state.last_session_pct
        last_weekly = state.last_weekly_pct
        last_session_resets = _parse_iso(state.last_session_resets_at)
        last_weekly_resets = _parse_iso(state.last_weekly_resets_at)

        # A true new week is signalled by the weekly reset time jumping forward.
        # A weekly percent that drops while the reset time stays put is NOT a new
        # week: Claude occasionally zeroes the weekly counter mid-period (same end
        # date). Treat that as a discontinuity (skip the crossing interval) rather
        # than a rollover, so we neither write a spurious partial-week record nor
        # discard the week's accumulation so far.
        weekly_period_rolled = self._resets_jumped(
            last_weekly_resets, weekly.resets_at
        )
        session_reset = self._resets_jumped(
            last_session_resets, session.resets_at
        ) or (last_session is not None and session.pct < last_session)
        weekly_mid_reset = (
            not weekly_period_rolled
            and last_weekly is not None
            and weekly.pct < last_weekly
        )

        if weekly_period_rolled:
            self._finalize_bucket(state, snapshot.provider)
            self._start_bucket(state, weekly, observed_at)
        elif session_reset or weekly_mid_reset:
            pass  # discontinuity within the same week: skip this interval's delta
        elif last_session is not None and last_weekly is not None:
            ds = session.pct - last_session
            dw = weekly.pct - last_weekly
            if ds > 0 and dw >= 0:
                state.sum_session_delta += ds
                state.sum_weekly_delta += dw
                state.sample_count += 1
                if state.bucket_started_at is None:
                    state.bucket_started_at = _isoformat(observed_at)
                    state.bucket_weekly_resets_at = _isoformat(weekly.resets_at)

        # Advance last_* on every live reading so the next interval is measured
        # from here (session resets and weekly rollovers skip the crossing
        # interval above, then resume cleanly from this point).
        state.last_session_pct = session.pct
        state.last_weekly_pct = weekly.pct
        state.last_session_resets_at = _isoformat(session.resets_at)
        state.last_weekly_resets_at = _isoformat(weekly.resets_at)
        state.bucket_last_seen_at = _isoformat(observed_at)
        if state.bucket_weekly_resets_at is None:
            state.bucket_weekly_resets_at = _isoformat(weekly.resets_at)
        self._save_state()

    def current_estimate(self, provider: str) -> RatioEstimate | None:
        state = self._state.get(provider)
        if state is None:
            return None
        return _estimate_from(
            state.sum_session_delta,
            state.sum_weekly_delta,
            state.sample_count,
            "current",
        )

    def history(self, provider: str) -> list[WeeklyRatioRecord]:
        return list(self._history.get(provider, []))

    def display_estimate(self, provider: str) -> RatioEstimate | None:
        """Resolve what the inline header should show for this provider.

        Prefer a confident in-progress week ("this week so far"); otherwise fall
        back to the most recent finalized week ("last week"); otherwise return
        the (calibrating) current estimate so the caller can show a placeholder.
        """
        current = self.current_estimate(provider)
        if current is not None and current.confident:
            return current
        recent = self._history.get(provider)
        if recent:
            return record_estimate(recent[-1])
        return current

    # ---- internals ----

    def _fresh_state(
        self,
        provider: str,
        session: _LiveMetric,
        weekly: _LiveMetric,
        observed_at: datetime,
    ) -> RatioTrackerState:
        return RatioTrackerState(
            provider=provider,
            last_session_pct=session.pct,
            last_weekly_pct=weekly.pct,
            last_session_resets_at=_isoformat(session.resets_at),
            last_weekly_resets_at=_isoformat(weekly.resets_at),
            bucket_started_at=None,
            bucket_weekly_resets_at=_isoformat(weekly.resets_at),
            bucket_last_seen_at=_isoformat(observed_at),
        )

    def _resets_jumped(
        self, old_resets: datetime | None, new_resets: datetime
    ) -> bool:
        """True when the reset time moved forward enough to be a new period.

        Used as the sole signal for a weekly rollover (a same-date percent drop
        is a mid-period reset, handled separately) and as one of the signals for
        a session reset.
        """
        return (
            old_resets is not None
            and new_resets >= old_resets + _ROLLOVER_THRESHOLD
        )

    def _start_bucket(
        self, state: RatioTrackerState, weekly: _LiveMetric, observed_at: datetime
    ) -> None:
        state.sum_session_delta = 0.0
        state.sum_weekly_delta = 0.0
        state.sample_count = 0
        state.bucket_started_at = None
        state.bucket_weekly_resets_at = _isoformat(weekly.resets_at)
        state.bucket_last_seen_at = _isoformat(observed_at)

    def _finalize_bucket(self, state: RatioTrackerState, provider: str) -> None:
        if state.sum_session_delta <= 0 or state.bucket_started_at is None:
            return
        record = WeeklyRatioRecord(
            provider=provider,
            week_started_at=state.bucket_started_at,
            week_ended_at=state.bucket_last_seen_at or state.bucket_started_at,
            weekly_resets_at=state.bucket_weekly_resets_at
            or state.last_weekly_resets_at
            or state.bucket_started_at,
            sum_session_delta=state.sum_session_delta,
            sum_weekly_delta=state.sum_weekly_delta,
            sample_count=state.sample_count,
        )
        bucket = self._history.setdefault(provider, [])
        bucket.append(record)
        del bucket[:-MAX_HISTORY]
        self._save_history()
        log.info(
            "ratio closed week provider=%s sessions_per_week=%s weekly_pct_per_session=%s samples=%s",
            provider,
            _fmt(sessions_per_week(record.sum_session_delta, record.sum_weekly_delta)),
            _fmt(
                weekly_pct_per_session(
                    record.sum_session_delta, record.sum_weekly_delta
                )
            ),
            record.sample_count,
        )

    def _load_state(self) -> dict[str, RatioTrackerState]:
        data = _read_json(self._state_path)
        out: dict[str, RatioTrackerState] = {}
        if not isinstance(data, dict):
            return out
        for provider, raw in data.items():
            if not isinstance(raw, dict):
                continue
            try:
                out[provider] = RatioTrackerState(**raw)
            except TypeError:
                continue
        return out

    def _load_history(self) -> tuple[dict[str, list[WeeklyRatioRecord]], bool]:
        data = _read_json(self._history_path)
        out: dict[str, list[WeeklyRatioRecord]] = {}
        if not isinstance(data, dict):
            return out, False
        dirty = False
        for provider, raw_list in data.items():
            if not isinstance(raw_list, list):
                continue
            records: list[WeeklyRatioRecord] = []
            for raw in raw_list:
                if not isinstance(raw, dict):
                    continue
                try:
                    records.append(WeeklyRatioRecord(**raw))
                except TypeError:
                    continue
            normalized = _normalize_history_records(records)
            if normalized != records:
                dirty = True
            out[provider] = normalized[-MAX_HISTORY:]
        return out, dirty

    def _save_state(self) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            payload = {p: asdict(rec) for p, rec in self._state.items()}
            self._state_path.write_text(
                json.dumps(payload, indent=2), encoding="utf-8"
            )
        except OSError:
            log.exception("failed to write ratio_state.json")

    def _save_history(self) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            payload = {
                p: [asdict(rec) for rec in recs]
                for p, recs in self._history.items()
            }
            self._history_path.write_text(
                json.dumps(payload, indent=2), encoding="utf-8"
            )
        except OSError:
            log.exception("failed to write ratios.json")


def _read_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"
