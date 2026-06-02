from PyQt6.QtWidgets import QLabel

from aigauge.ratio import WeeklyRatioRecord
from aigauge.ratio_dialog import RatioHistoryDialog, _period_label


def _record(
    *,
    session_delta: float,
    weekly_delta: float,
    samples: int,
) -> WeeklyRatioRecord:
    return WeeklyRatioRecord(
        provider="claude",
        week_started_at="2026-06-01T00:08:37",
        week_ended_at="2026-06-01T13:22:21",
        weekly_resets_at="2026-06-01T17:59:37",
        sum_session_delta=session_delta,
        sum_weekly_delta=weekly_delta,
        sample_count=samples,
    )


def test_period_label_uses_date_not_positional_week_count():
    assert _period_label(_record(session_delta=78, weekly_delta=7, samples=12)) == "Jun 01"


def test_period_label_marks_low_confidence_records_partial():
    assert (
        _period_label(_record(session_delta=27, weekly_delta=1, samples=7))
        == "Jun 01 partial"
    )


def test_history_grid_hides_low_confidence_ratio_values(qtbot):
    dialog = RatioHistoryDialog(
        "claude",
        "Claude",
        [_record(session_delta=27, weekly_delta=1, samples=7)],
        current_estimate=None,
    )
    qtbot.addWidget(dialog)

    labels = [label.text() for label in dialog.findChildren(QLabel)]
    assert "Jun 01 partial" in labels
    assert "27.0" not in labels
    assert "n/a" in labels
