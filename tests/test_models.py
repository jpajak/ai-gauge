from datetime import datetime

from usage_view.models import SnapshotStatus, UsageMetric, UsageSnapshot


def test_snapshot_defaults():
    snap = UsageSnapshot(provider="x", status=SnapshotStatus.OK)
    assert snap.metrics == []
    assert snap.error is None
    assert snap.raw == {}
    assert isinstance(snap.fetched_at, datetime)


def test_metric_optional_fields():
    m = UsageMetric(label="Session")
    assert m.percent_used is None
    assert m.resets_at is None
    assert m.reset_label is None
    assert m.note is None
