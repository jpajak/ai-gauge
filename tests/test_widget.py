from datetime import datetime, timedelta

from usage_view.config import Config
from usage_view.models import SnapshotStatus, UsageMetric, UsageSnapshot
from usage_view.widget import UsageWidget


def _tile_order(widget: UsageWidget) -> list[str]:
    return [
        widget._tile_layout.itemAt(i).widget().provider
        for i in range(widget._tile_layout.count())
    ]


def test_reenabled_provider_returns_to_canonical_order(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)

    widget.ensure_tile("claude", "Claude")
    widget.ensure_tile("codex", "Codex")
    widget.ensure_tile("copilot", "Copilot")
    widget.remove_tile("codex")
    widget.ensure_tile("codex", "Codex")

    assert _tile_order(widget) == ["claude", "codex", "copilot"]


def test_mark_loading_invalidates_existing_tile_data(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    fetched = datetime(2026, 4, 27, 12, 0)

    widget.update_snapshot(
        UsageSnapshot(
            provider="codex",
            status=SnapshotStatus.OK,
            metrics=[
                UsageMetric("Session", 47.0, fetched + timedelta(hours=2)),
            ],
            fetched_at=fetched,
        ),
        "Codex",
    )
    assert len(widget._tiles["codex"]._rows) == 1  # noqa: SLF001

    widget.mark_loading({"codex": "Codex"})

    tile = widget._tiles["codex"]  # noqa: SLF001
    assert tile.status.text().startswith("loading")
    assert tile.status.toolTip() == ""
    assert tile._rows == []  # noqa: SLF001


def test_auth_required_tile_uses_sign_in_button(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)

    widget.update_snapshot(
        UsageSnapshot(
            provider="claude",
            status=SnapshotStatus.AUTH_REQUIRED,
            error="Not signed in.",
        ),
        "Claude",
    )

    tile = widget._tiles["claude"]  # noqa: SLF001
    assert tile.action_btn.text() == "Sign in"
    assert not tile.action_btn.isHidden()


def test_sign_in_button_emits_sign_in_signal(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)

    widget.update_snapshot(
        UsageSnapshot(
            provider="codex",
            status=SnapshotStatus.AUTH_REQUIRED,
            error="Not signed in.",
        ),
        "Codex",
    )

    with qtbot.waitSignal(widget.sign_in_requested) as signal:
        widget._tiles["codex"].action_btn.click()  # noqa: SLF001

    assert signal.args == ["codex"]


def test_refresh_state_shows_next_refresh_countdown(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)

    widget.set_refresh_state(
        active=True,
        minutes=5,
        next_at=datetime.now() + timedelta(minutes=3, seconds=5),
    )

    assert widget.cadence_label.text() == "· active next 4m"
    assert "5 min cadence" in widget.cadence_label.toolTip()


def test_refresh_state_shows_now_when_next_refresh_is_due(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)

    widget.set_refresh_state(
        active=False,
        minutes=60,
        next_at=datetime.now() - timedelta(seconds=1),
    )

    assert widget.cadence_label.text() == "· idle next now"


def test_widget_uses_fixed_width_despite_extreme_saved_size(qtbot):
    config = Config()
    config.window.width = 5000
    config.window.height = 2

    widget = UsageWidget(config)
    qtbot.addWidget(widget)

    assert widget.width() == 340
    assert widget.height() >= 80


def test_refit_restores_fixed_width_after_dpi_resize_glitch(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    widget.resize(5000, 120)

    widget._do_refit_height()  # noqa: SLF001

    assert widget.width() == 340
