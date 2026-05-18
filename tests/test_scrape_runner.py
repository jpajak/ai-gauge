import logging
from typing import Any

import pytest

from aigauge.models import SnapshotStatus, UsageSnapshot
from aigauge.providers import _scrape_runner as runner_module
from aigauge.providers._scrape_runner import ScrapeRunner


class _FakeDoneSignal:
    def __init__(self):
        self._slot = None

    def connect(self, slot):
        self._slot = slot

    def emit(self, result, error):
        assert self._slot is not None, "no slot connected"
        self._slot(result, error)


class _FakeScraper:
    """Stand-in for HeadlessScraper that fires `done` synchronously on demand."""

    instances: list["_FakeScraper"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.done = _FakeDoneSignal()
        _FakeScraper.instances.append(self)


@pytest.fixture
def fake_scraper(monkeypatch):
    _FakeScraper.instances.clear()
    monkeypatch.setattr(runner_module, "HeadlessScraper", _FakeScraper)
    yield _FakeScraper
    _FakeScraper.instances.clear()


def _ok_snapshot() -> UsageSnapshot:
    return UsageSnapshot(provider="x", status=SnapshotStatus.OK)


def _err_snapshot(reason: str = "layout") -> UsageSnapshot:
    return UsageSnapshot(provider="x", status=SnapshotStatus.ERROR, error=reason)


def test_scrape_runner_passes_ok_snapshot_through(fake_scraper):
    received: list[UsageSnapshot] = []
    rn = ScrapeRunner(
        account_id="x",
        url="http://example",
        extractor_js="",
        build=lambda payload: _ok_snapshot(),
        log=logging.getLogger("test"),
        build_max_attempts=2,
    )
    rn.run(received.append)

    assert len(fake_scraper.instances) == 1
    fake_scraper.instances[0].done.emit({"any": "payload"}, "")

    assert len(received) == 1
    assert received[0].status == SnapshotStatus.OK
    # No retry should have been scheduled.
    assert len(fake_scraper.instances) == 1


def test_scrape_runner_retries_on_build_error(fake_scraper):
    received: list[UsageSnapshot] = []
    calls: list[dict[str, Any]] = []

    def _build(payload):
        calls.append(payload)
        # First call fails, second succeeds — mirrors the partial-render
        # scenario that motivated the runner.
        return _err_snapshot() if len(calls) == 1 else _ok_snapshot()

    rn = ScrapeRunner(
        account_id="x",
        url="http://example",
        extractor_js="",
        build=_build,
        log=logging.getLogger("test"),
        build_max_attempts=2,
    )
    rn.run(received.append)

    fake_scraper.instances[0].done.emit({"first": True}, "")
    assert received == [], "first ERROR should trigger retry, not deliver"
    assert len(fake_scraper.instances) == 2, "second scrape should have started"

    fake_scraper.instances[1].done.emit({"second": True}, "")
    assert len(received) == 1
    assert received[0].status == SnapshotStatus.OK


def test_scrape_runner_stops_retrying_after_limit(fake_scraper):
    received: list[UsageSnapshot] = []
    rn = ScrapeRunner(
        account_id="x",
        url="http://example",
        extractor_js="",
        build=lambda payload: _err_snapshot("layout"),
        log=logging.getLogger("test"),
        build_max_attempts=2,
    )
    rn.run(received.append)

    fake_scraper.instances[0].done.emit({}, "")
    fake_scraper.instances[1].done.emit({}, "")

    assert len(fake_scraper.instances) == 2, "should not retry past build_max_attempts"
    assert len(received) == 1
    assert received[0].status == SnapshotStatus.ERROR


def test_scrape_runner_surfaces_transport_error_without_build(fake_scraper):
    received: list[UsageSnapshot] = []
    build_calls = []
    rn = ScrapeRunner(
        account_id="x",
        url="http://example",
        extractor_js="",
        build=lambda payload: (build_calls.append(payload), _ok_snapshot())[1],
        log=logging.getLogger("test"),
        build_max_attempts=2,
    )
    rn.run(received.append)

    fake_scraper.instances[0].done.emit(None, "timeout")

    assert build_calls == [], "build should be skipped on transport error"
    assert len(received) == 1
    assert received[0].status == SnapshotStatus.ERROR
    assert received[0].error == "timeout"
