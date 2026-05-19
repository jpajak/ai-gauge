from datetime import datetime, timedelta
from types import SimpleNamespace

from aigauge.app import (
    App,
    _acquire_instance_lock,
    _adaptive_refresh_minutes,
    _enabled_providers,
    _preserve_error_metrics,
    _refresh_provider_order,
    _raw_summary,
)
from aigauge.config import BrowserAccount, Config
from aigauge.models import SnapshotStatus, UsageMetric, UsageSnapshot


class _Timer:
    def __init__(self):
        self.stopped = False
        self.started_ms: int | None = None
        self.active = False
        self.remaining_ms = 0

    def stop(self):
        self.stopped = True
        self.active = False

    def start(self, ms: int):
        self.started_ms = ms
        self.remaining_ms = ms
        self.active = True

    def isActive(self):
        return self.active

    def remainingTime(self):
        return self.remaining_ms


class _Widget:
    def __init__(self):
        self.loading_calls = []
        self.refreshing = []
        self.refresh_state_calls = []
        self.visible = True

    def set_refreshing(self, refreshing):
        self.refreshing.append(refreshing)

    def mark_loading(self, providers):
        self.loading_calls.append(providers)

    def set_refresh_state(self, *, active, minutes, next_at=None):
        self.refresh_state_calls.append(
            {"active": active, "minutes": minutes, "next_at": next_at}
        )

    def isVisible(self):
        return self.visible


class _Dialog:
    def __init__(self):
        self.calls = []

    def isMinimized(self):
        return False

    def show(self):
        self.calls.append("show")

    def showNormal(self):
        self.calls.append("showNormal")

    def raise_(self):
        self.calls.append("raise")

    def activateWindow(self):
        self.calls.append("activate")


def _refresh_app_stub() -> App:
    app = App.__new__(App)
    app._providers = {"claude": object(), "codex": object()}  # noqa: SLF001
    app._inflight = set()  # noqa: SLF001
    app._refresh_queue = []  # noqa: SLF001
    app._active_until = datetime.now() - timedelta(minutes=1)  # noqa: SLF001
    app._unchanged_cycles = 3  # noqa: SLF001
    app._timer = _Timer()  # noqa: SLF001
    app._current_refresh_manual = False  # noqa: SLF001
    app._cycle_signatures = {"old": ()}  # noqa: SLF001
    app._widget = _Widget()  # noqa: SLF001
    app._start_next_refresh = lambda: None  # noqa: SLF001
    app._config = SimpleNamespace()  # noqa: SLF001
    return app


def test_adaptive_refresh_uses_active_interval_when_active():
    assert _adaptive_refresh_minutes(
        active=True,
        active_minutes=5,
        unchanged_cycles=10,
        max_minutes=15,
    ) == 5


def test_adaptive_refresh_backs_off_when_unchanged():
    assert _adaptive_refresh_minutes(
        active=False,
        active_minutes=5,
        unchanged_cycles=0,
        max_minutes=60,
    ) == 5
    assert _adaptive_refresh_minutes(
        active=False,
        active_minutes=5,
        unchanged_cycles=1,
        max_minutes=60,
    ) == 10
    assert _adaptive_refresh_minutes(
        active=False,
        active_minutes=5,
        unchanged_cycles=3,
        max_minutes=60,
    ) == 40


def test_adaptive_refresh_caps_at_configured_max():
    assert _adaptive_refresh_minutes(
        active=False,
        active_minutes=5,
        unchanged_cycles=8,
        max_minutes=15,
    ) == 15


def test_adaptive_refresh_respects_short_user_interval():
    assert _adaptive_refresh_minutes(
        active=True,
        active_minutes=5,
        unchanged_cycles=0,
        max_minutes=2,
    ) == 2


def test_adaptive_refresh_uses_configured_active_rate():
    assert _adaptive_refresh_minutes(
        active=True,
        active_minutes=1,
        unchanged_cycles=0,
        max_minutes=60,
    ) == 1
    assert _adaptive_refresh_minutes(
        active=False,
        active_minutes=15,
        unchanged_cycles=1,
        max_minutes=120,
    ) == 30


def test_manual_refresh_marks_tiles_loading():
    app = _refresh_app_stub()

    app.refresh_now(manual=True)

    assert app._widget.loading_calls == [  # noqa: SLF001
        {"claude": "Claude", "codex": "Codex"}
    ]
    assert app._refresh_queue == ["claude", "codex"]  # noqa: SLF001
    assert app._unchanged_cycles == 0  # noqa: SLF001


def test_scheduled_refresh_keeps_existing_tiles_visible():
    app = _refresh_app_stub()

    app.refresh_now(manual=False)

    assert app._widget.loading_calls == []  # noqa: SLF001
    assert app._refresh_queue == ["claude", "codex"]  # noqa: SLF001
    assert app._unchanged_cycles == 3  # noqa: SLF001


def test_refresh_order_prioritizes_openrouter_without_reordering_tiles():
    providers = {
        "claude": object(),
        "codex": object(),
        "copilot": object(),
        "openrouter": object(),
    }

    assert _refresh_provider_order(providers) == [
        "openrouter",
        "claude",
        "codex",
        "copilot",
    ]


def test_enabled_providers_includes_enabled_browser_accounts():
    config = Config()
    config.browser_accounts.append(
        BrowserAccount(id="claude-team", kind="claude", name="Team", enabled=True)
    )
    config.providers.codex = False

    assert _enabled_providers(config) == (
        "claude",
        "claude-team",
        "copilot",
    )


def test_widget_activation_raises_open_settings_dialog():
    app = App.__new__(App)
    dialog = _Dialog()
    app._settings_dialog = dialog  # noqa: SLF001

    app._on_widget_activated()  # noqa: SLF001

    assert dialog.calls == ["show", "raise", "activate"]


def test_raw_summary_includes_sanitized_payload_details():
    summary = _raw_summary(
        {
            "session": None,
            "weekly": {
                "raw": "x" * 400,
                "percent": None,
            },
            "items": [{"a": 1}, {"b": 2}, {"c": 3}, {"d": 4}, {"e": 5}, {"f": 6}],
        }
    )

    assert '"session": null' in summary
    assert '"percent": null' in summary
    assert "xxx" in summary
    assert "more" in summary
    assert len(summary) < 700


def test_error_snapshot_preserves_previous_metrics():
    previous = UsageSnapshot(
        provider="claude",
        status=SnapshotStatus.OK,
        metrics=[UsageMetric("Session", 42.0, None)],
    )
    current = UsageSnapshot(
        provider="claude",
        status=SnapshotStatus.ERROR,
        error="extractor retry limit exceeded",
    )

    merged = _preserve_error_metrics(current, previous)

    assert merged.status == SnapshotStatus.ERROR
    assert merged.error == "extractor retry limit exceeded"
    assert [(m.label, m.percent_used) for m in merged.metrics] == [("Session", 42.0)]


def test_repeated_error_snapshot_keeps_stale_metrics():
    previous = UsageSnapshot(
        provider="claude",
        status=SnapshotStatus.ERROR,
        error="previous failure",
        metrics=[UsageMetric("Session", 42.0, None)],
    )
    current = UsageSnapshot(
        provider="claude",
        status=SnapshotStatus.ERROR,
        error="extractor retry limit exceeded",
    )

    merged = _preserve_error_metrics(current, previous)

    assert [(m.label, m.percent_used) for m in merged.metrics] == [("Session", 42.0)]


def test_lifecycle_context_includes_refresh_state():
    app = App.__new__(App)
    app._started_at = datetime.now() - timedelta(seconds=90)  # noqa: SLF001
    app._ui_mode = "floating_widget"  # noqa: SLF001
    app._widget = _Widget()  # noqa: SLF001
    app._config = SimpleNamespace(  # noqa: SLF001
        providers=SimpleNamespace(
            claude=True, codex=False, copilot=True, openrouter=False
        )
    )
    app._inflight = {"claude"}  # noqa: SLF001
    app._refresh_queue = ["copilot"]  # noqa: SLF001
    app._unchanged_cycles = 2  # noqa: SLF001
    app._timer = _Timer()  # noqa: SLF001
    app._timer.start(125_000)  # noqa: SLF001

    context = app._lifecycle_context()  # noqa: SLF001

    assert context["uptime_s"] >= 89
    assert context["ui_mode"] == "floating_widget"
    assert context["widget_visible"] is True
    assert context["providers"] == "claude,copilot"
    assert context["inflight"] == "claude"
    assert context["queue"] == "copilot"
    assert context["next_refresh_s"] == 125
    assert context["unchanged_cycles"] == 2


def test_instance_lock_prevents_second_running_copy(tmp_path, monkeypatch):
    monkeypatch.setattr("aigauge.app.app_data_dir", lambda: tmp_path)

    first = _acquire_instance_lock()
    assert first is not None
    try:
        assert _acquire_instance_lock() is None
    finally:
        first.unlock()


def _schedule_app_stub() -> App:
    app = App.__new__(App)
    app._inflight = set()  # noqa: SLF001
    app._refresh_queue = []  # noqa: SLF001
    app._active_until = datetime.now() - timedelta(minutes=1)  # noqa: SLF001
    app._unchanged_cycles = 5  # noqa: SLF001
    app._timer = _Timer()  # noqa: SLF001
    app._widget = _Widget()  # noqa: SLF001
    app._snapshots = {}  # noqa: SLF001
    app._config = SimpleNamespace(
        active_refresh_interval_minutes=5,
        refresh_interval_minutes=60,
    )
    return app


def test_schedule_pulls_refresh_forward_to_known_reset():
    app = _schedule_app_stub()
    soon = datetime.now() + timedelta(minutes=10)
    app._snapshots = {  # noqa: SLF001
        "claude": UsageSnapshot(
            provider="claude",
            status=SnapshotStatus.OK,
            metrics=[
                UsageMetric(label="Session", percent_used=80.0, resets_at=soon),
            ],
        ),
    }

    app._schedule_next_refresh()  # noqa: SLF001

    # Default backoff would be way longer; reset+grace is ~11 minutes.
    assert app._timer.started_ms is not None  # noqa: SLF001
    scheduled_minutes = app._timer.started_ms / 60_000  # noqa: SLF001
    assert 9 <= scheduled_minutes <= 13


def test_schedule_ignores_unused_metric_resets():
    app = _schedule_app_stub()
    soon = datetime.now() + timedelta(minutes=10)
    app._snapshots = {  # noqa: SLF001
        # 0% used — resetting changes nothing visible.
        "claude": UsageSnapshot(
            provider="claude",
            status=SnapshotStatus.OK,
            metrics=[
                UsageMetric(label="Session", percent_used=0.0, resets_at=soon),
            ],
        ),
    }

    app._schedule_next_refresh()  # noqa: SLF001

    assert app._timer.started_ms is not None  # noqa: SLF001
    scheduled_minutes = app._timer.started_ms / 60_000  # noqa: SLF001
    # Falls back to adaptive backoff (5 min × 2^5 = 160, capped at 60).
    assert scheduled_minutes >= 30
