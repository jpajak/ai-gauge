from datetime import datetime, timedelta

from aigauge.providers.idle import idle_reset_state


def test_idle_reset_state_hides_unused_far_future_reset():
    resets_at = datetime.now() + timedelta(days=2)
    reset, label, note = idle_reset_state(
        percent=0,
        resets_at=resets_at,
        window=timedelta(hours=5),
    )
    assert reset is None
    assert label == "idle"
    assert note is not None


def test_idle_reset_state_keeps_active_reset_inside_window():
    resets_at = datetime.now() + timedelta(hours=4)
    reset, label, note = idle_reset_state(
        percent=0,
        resets_at=resets_at,
        window=timedelta(hours=5),
    )
    assert reset == resets_at
    assert label is None
    assert note is None


def test_idle_reset_state_keeps_nonzero_usage():
    resets_at = datetime.now() + timedelta(days=2)
    reset, label, note = idle_reset_state(
        percent=10,
        resets_at=resets_at,
        window=timedelta(hours=5),
    )
    assert reset == resets_at
    assert label is None
    assert note is None


def test_idle_reset_state_marks_unused_missing_reset_as_idle():
    reset, label, note = idle_reset_state(
        percent=0,
        resets_at=None,
        window=timedelta(days=7),
    )
    assert reset is None
    assert label == "idle"
    assert note is not None
