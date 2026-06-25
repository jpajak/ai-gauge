from datetime import datetime, timedelta

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from aigauge.config import BrowserAccount, Config
from aigauge.models import SnapshotStatus, UsageMetric, UsageSnapshot
from aigauge.ratio import RatioEstimate
from aigauge.widget import (
    UsageWidget,
    _format_ratio_inline,
    _MetricRow,
    _SummaryChip,
)


def _ok_snapshot(provider: str) -> UsageSnapshot:
    fetched = datetime(2026, 4, 27, 12, 0)
    return UsageSnapshot(
        provider=provider,
        status=SnapshotStatus.OK,
        metrics=[
            UsageMetric("Session", 40.0, fetched + timedelta(hours=2)),
            UsageMetric("Weekly", 20.0, fetched + timedelta(days=5)),
        ],
        fetched_at=fetched,
    )


def _estimate(confident: bool, n: float | None, source: str = "current") -> RatioEstimate:
    return RatioEstimate(
        sessions_per_week=n if confident else None,
        weekly_pct_per_session=(100.0 / n) if (confident and n) else None,
        coverage_pct=40.0,
        sample_count=12,
        confident=confident,
        source=source,
    )


def _tile_order(widget: UsageWidget) -> list[str]:
    return [
        widget._tile_layout.itemAt(i).widget().provider
        for i in range(widget._tile_layout.count())
    ]


def _collapsed_chip_texts(widget: UsageWidget) -> list[str]:
    texts = []
    stack = [widget._collapsed_summary_layout]  # noqa: SLF001
    while stack:
        layout = stack.pop(0)
        for i in range(layout.count()):
            item = layout.itemAt(i)
            child = item.widget()
            if child is not None and child is not widget._collapsed_label:  # noqa: SLF001
                if hasattr(child, "text"):
                    texts.append(child.text())
                if child.layout() is not None:
                    stack.append(child.layout())
    return texts


def test_offscreen_saved_position_is_clamped_on_screen(qtbot):
    """A position saved at a lower display scale can land off the (smaller)
    logical desktop at 175%/200%; the widget must reappear fully on-screen."""
    geo = QApplication.primaryScreen().availableGeometry()
    config = Config()
    # Far past the bottom-right corner, as a high-DPI logical shrink would do
    # to coordinates captured at 100%.
    config.window.x = geo.right() + 5000
    config.window.y = geo.bottom() + 5000

    widget = UsageWidget(config)
    qtbot.addWidget(widget)

    assert widget.x() >= geo.left()
    assert widget.y() >= geo.top()
    assert widget.x() + widget.width() <= geo.right() + 1
    assert widget.y() + widget.height() <= geo.bottom() + 1


def test_reenabled_provider_returns_to_canonical_order(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)

    widget.ensure_tile("claude", "Claude")
    widget.ensure_tile("codex", "Codex")
    widget.ensure_tile("copilot", "Copilot")
    widget.remove_tile("codex")
    widget.ensure_tile("codex", "Codex")

    assert _tile_order(widget) == ["claude", "codex", "copilot"]


def test_browser_account_tiles_group_by_provider_kind(qtbot):
    config = Config()
    config.browser_accounts.append(
        BrowserAccount(id="claude-team", kind="claude", name="Team")
    )
    config.browser_accounts.append(
        BrowserAccount(id="codex-work", kind="codex", name="Work")
    )
    widget = UsageWidget(config)
    qtbot.addWidget(widget)

    widget.ensure_tile("codex-work", "Codex (Work)")
    widget.ensure_tile("claude-team", "Claude (Team)")
    widget.ensure_tile("codex", "Codex")
    widget.ensure_tile("claude", "Claude")
    widget.ensure_tile("copilot", "Copilot")

    assert _tile_order(widget) == [
        "claude",
        "claude-team",
        "codex",
        "codex-work",
        "copilot",
    ]


def test_format_ratio_inline_states():
    assert _format_ratio_inline(None) is None
    assert _format_ratio_inline(_estimate(True, 9.24)) == "~9.2/wk"
    assert _format_ratio_inline(_estimate(False, None)) == "burn ~?"


def test_ratio_label_shows_when_ok_and_confident(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    widget.update_snapshot(_ok_snapshot("claude"), "Claude")
    widget.set_ratio("claude", _estimate(True, 9.2), recent=[10.0, 9.5, 9.2])

    label = widget._tiles["claude"].ratio_label  # noqa: SLF001
    assert not label.isHidden()
    assert "9.2/wk" in label.text()
    assert "sessions/week" in label.toolTip()


def test_ratio_label_calibrating_placeholder(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    widget.update_snapshot(_ok_snapshot("codex"), "Codex")
    calibrating = RatioEstimate(
        sessions_per_week=None,
        weekly_pct_per_session=None,
        coverage_pct=1.2,
        sample_count=2,
        confident=False,
        source="current",
        session_delta=12.0,
    )
    widget.set_ratio("codex", calibrating, recent=[])

    label = widget._tiles["codex"].ratio_label  # noqa: SLF001
    assert not label.isHidden()
    assert "burn ~?" in label.text()
    tip = label.toolTip().lower()
    assert "calibrating" in tip
    # Calibration progress is visible so the wait is not a mystery.
    assert "12/30" in label.toolTip()
    assert "2/3" in label.toolTip()


def test_ratio_label_carry_over_dimmed_with_progress(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    widget.update_snapshot(_ok_snapshot("claude"), "Claude")
    last_week = _estimate(True, 9.2, source="history")
    this_week = RatioEstimate(
        sessions_per_week=None,
        weekly_pct_per_session=None,
        coverage_pct=1.0,
        sample_count=1,
        confident=False,
        source="current",
        session_delta=6.0,
    )
    widget.set_ratio("claude", last_week, recent=[9.5, 9.2], live=this_week)

    label = widget._tiles["claude"].ratio_label  # noqa: SLF001
    assert not label.isHidden()
    assert "9.2/wk°" in label.text()  # carry-over marker
    assert "#6b7280" in label.text()  # dimmed color
    tip = label.toolTip()
    assert "Last week" in tip
    assert "This week calibrating" in tip
    assert "6/30" in tip


def test_ratio_label_hidden_when_not_ok(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    widget.update_snapshot(
        UsageSnapshot(
            provider="claude",
            status=SnapshotStatus.AUTH_REQUIRED,
            error="Not signed in.",
            fetched_at=datetime(2026, 4, 27, 12, 0),
        ),
        "Claude",
    )
    # Even a confident estimate must not show on a non-OK tile.
    widget.set_ratio("claude", _estimate(True, 9.2), recent=[9.2])

    label = widget._tiles["claude"].ratio_label  # noqa: SLF001
    assert label.isHidden()


def test_ratio_history_signal_emitted_on_link_click(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    widget.update_snapshot(_ok_snapshot("claude"), "Claude")
    widget.set_ratio("claude", _estimate(True, 9.2), recent=[9.2])

    seen: list[str] = []
    widget.ratio_history_requested.connect(seen.append)
    widget._tiles["claude"].ratio_label.linkActivated.emit("ratio-history")  # noqa: SLF001
    assert seen == ["claude"]


def test_mark_loading_preserves_existing_data_and_dims_tile(qtbot):
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
    # Prior data stays on screen — only the visual "refreshing" flag flips.
    assert len(tile._rows) == 1  # noqa: SLF001
    assert tile._rows[0].pct.text() == "47%"  # noqa: SLF001
    assert tile._refreshing is True  # noqa: SLF001
    # And the cached snapshot is intact so collapsed chips keep their values.
    assert widget._snapshots["codex"] is not None  # noqa: SLF001


def test_mark_loading_shows_skeleton_when_no_prior_data(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)

    widget.mark_loading({"claude": "Claude", "codex": "Codex", "copilot": "Copilot"})
    widget._do_refit_height()  # noqa: SLF001

    for provider in ("claude", "codex", "copilot"):
        tile = widget._tiles[provider]  # noqa: SLF001
        assert tile._refreshing is True  # noqa: SLF001
        assert len(tile._rows) == 1  # noqa: SLF001
        # Indeterminate range == busy mode (animated stripe).
        assert tile._rows[0].bar.maximum() == 0  # noqa: SLF001

    assert widget._header_widget.height() <= widget._header_widget.sizeHint().height() + 2  # noqa: SLF001
    assert widget._tile_container.height() <= widget._tile_container.sizeHint().height() + 2  # noqa: SLF001


def test_update_snapshot_clears_refreshing_flag(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)

    widget.mark_loading({"codex": "Codex"})
    assert widget._tiles["codex"]._refreshing is True  # noqa: SLF001

    widget.update_snapshot(
        UsageSnapshot(
            provider="codex",
            status=SnapshotStatus.OK,
            metrics=[UsageMetric("Session", 50.0, None)],
        ),
        "Codex",
    )

    assert widget._tiles["codex"]._refreshing is False  # noqa: SLF001
    # The previously-skeleton row is now a real metric row.
    assert widget._tiles["codex"]._rows[0].bar.maximum() == 100  # noqa: SLF001


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


def test_secondary_browser_account_auth_tile_uses_sign_in_button(qtbot):
    config = Config()
    config.browser_accounts.append(
        BrowserAccount(id="codex-work", kind="codex", name="Work")
    )
    widget = UsageWidget(config)
    qtbot.addWidget(widget)

    widget.update_snapshot(
        UsageSnapshot(
            provider="codex-work",
            status=SnapshotStatus.AUTH_REQUIRED,
            error="Not signed in.",
        ),
        "Codex (Work)",
    )

    tile = widget._tiles["codex-work"]  # noqa: SLF001
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


def test_collapsed_mode_shows_session_summary(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)

    widget.update_snapshot(
        UsageSnapshot(
            provider="claude",
            status=SnapshotStatus.OK,
            metrics=[
                UsageMetric("Session", 50.0, None),
                UsageMetric("Weekly", 12.0, None),
            ],
        ),
        "Claude",
    )
    widget.update_snapshot(
        UsageSnapshot(
            provider="codex",
            status=SnapshotStatus.OK,
            metrics=[
                UsageMetric("Session", 0.0, None),
                UsageMetric("Weekly", 15.0, None),
            ],
        ),
        "Codex",
    )
    widget.update_snapshot(
        UsageSnapshot(
            provider="copilot",
            status=SnapshotStatus.OK,
            metrics=[UsageMetric("Premium (1434/1500)", 96.0, None)],
        ),
        "Copilot",
    )

    widget.set_collapsed(True)

    assert not widget._collapsed_widget.isHidden()  # noqa: SLF001
    assert widget._tile_container.isHidden()  # noqa: SLF001
    assert _collapsed_chip_texts(widget) == ["Claude 50%", "Codex 0%", "Copilot 96%"]
    assert all("Weekly" not in text for text in _collapsed_chip_texts(widget))


def test_error_snapshot_can_show_stale_metrics(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)

    widget.update_snapshot(
        UsageSnapshot(
            provider="claude",
            status=SnapshotStatus.ERROR,
            error="extractor retry limit exceeded",
            metrics=[
                UsageMetric("Session", 50.0, None),
                UsageMetric("Weekly", 12.0, None),
            ],
        ),
        "Claude",
    )

    tile = widget._tiles["claude"]  # noqa: SLF001
    assert "error · stale" in tile.status.text()
    assert [row.label.text() for row in tile._rows] == ["Session", "Weekly"]  # noqa: SLF001
    assert tile._rows[0].pct.text() == "50%"  # noqa: SLF001

    widget.set_collapsed(True)

    assert _collapsed_chip_texts(widget) == ["Claude 50% stale"]


def test_collapsed_mode_shows_openrouter_balance(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)

    widget.update_snapshot(
        UsageSnapshot(
            provider="openrouter",
            status=SnapshotStatus.OK,
            metrics=[UsageMetric("Balance $11.50 left · Today $1.31", None)],
        ),
        "OpenRouter",
    )

    widget.set_collapsed(True)

    assert _collapsed_chip_texts(widget) == ["OpenRouter $11.50"]


def test_collapsed_mode_shows_openrouter_today_without_balance(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)

    widget.update_snapshot(
        UsageSnapshot(
            provider="openrouter",
            status=SnapshotStatus.OK,
            metrics=[
                UsageMetric(
                    "Spend today $1.31 / month $21.90",
                    None,
                )
            ],
        ),
        "OpenRouter",
    )

    widget.set_collapsed(True)

    assert _collapsed_chip_texts(widget) == ["OpenRouter today $1.31"]


def test_collapsed_mode_resizes_immediately(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    widget.resize(340, 260)

    widget.set_collapsed(True)

    assert widget.height() == 58

def test_openrouter_model_expand_resizes_immediately(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    widget.update_snapshot(
        UsageSnapshot(
            provider="openrouter",
            status=SnapshotStatus.OK,
            metrics=[
                UsageMetric("Balance $11.50 left · Spend today $0.00", None),
                UsageMetric("Models: last 30 completed UTC days", None, tag="models"),
                UsageMetric("claude-sonnet-4", 42.0, tag="models"),
                UsageMetric("gpt-4.1", 21.0, tag="models"),
                UsageMetric("gemini-pro", 18.0, tag="models"),
            ],
        ),
        "OpenRouter",
    )
    widget._do_refit_height()  # noqa: SLF001
    collapsed_height = widget.height()

    widget._tiles["openrouter"].set_expanded(True)  # noqa: SLF001
    qtbot.waitUntil(lambda: widget.height() > collapsed_height)
    expanded_height = widget.height()

    widget._tiles["openrouter"].set_expanded(False)  # noqa: SLF001
    qtbot.waitUntil(lambda: widget.height() < expanded_height)


def test_collapsed_mode_wraps_all_account_chips_without_overflow(qtbot):
    config = Config()
    for i in range(2, 6):
        config.browser_accounts.append(
            BrowserAccount(id=f"codex-{i}", kind="codex", name=f"Account {i}")
        )
    widget = UsageWidget(config)
    qtbot.addWidget(widget)

    for account in config.browser_accounts:
        widget.update_snapshot(
            UsageSnapshot(
                provider=account.id,
                status=SnapshotStatus.OK,
                metrics=[UsageMetric("Session", 25.0, None)],
            ),
            f"Codex ({account.name})" if account.name else "Codex",
        )

    widget.set_collapsed(True)
    widget._do_refit_height()  # noqa: SLF001

    texts = _collapsed_chip_texts(widget)
    assert "+4" not in texts
    assert len(texts) == len(config.browser_accounts)
    assert "Account 2 25%" in texts
    assert all("Codex (Account" not in text for text in texts)
    assert widget.height() > 58


def test_collapsed_mode_persists_and_expands(qtbot):
    config = Config()
    widget = UsageWidget(config)
    qtbot.addWidget(widget)

    widget.set_collapsed(True)
    assert config.window.collapsed is True

    widget.set_collapsed(False)
    assert config.window.collapsed is False
    assert widget._collapsed_widget.isHidden()  # noqa: SLF001
    assert not widget._tile_container.isHidden()  # noqa: SLF001


def test_always_on_top_suspension_is_reference_counted(qtbot):
    config = Config()
    config.window.always_on_top = True
    widget = UsageWidget(config)
    qtbot.addWidget(widget)

    assert widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint

    widget.suspend_always_on_top()
    widget.suspend_always_on_top()
    assert not widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint

    widget.restore_always_on_top()
    assert not widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint

    widget.restore_always_on_top()
    assert widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint

def test_widget_is_solid_when_fade_disabled(qtbot):
    config = Config()
    config.window.fade_when_inactive = False
    config.window.opacity = 0.4
    widget = UsageWidget(config)
    qtbot.addWidget(widget)

    assert widget._target_window_opacity() == 1.0  # noqa: SLF001
    assert widget.windowOpacity() == 1.0


def test_widget_fades_when_inactive_and_restores_on_hover(qtbot):
    config = Config()
    config.window.fade_when_inactive = True
    config.window.opacity = 0.45
    widget = UsageWidget(config)
    qtbot.addWidget(widget)

    assert widget._target_window_opacity() == 0.45  # noqa: SLF001
    assert widget.windowOpacity() == 0.45

    widget._mouse_inside = True  # noqa: SLF001
    widget._apply_window_opacity()  # noqa: SLF001

    assert widget._target_window_opacity() == 1.0  # noqa: SLF001
    assert widget.windowOpacity() == 1.0

def test_metric_row_sets_pace_from_window(qtbot):
    row = _MetricRow()
    qtbot.addWidget(row)

    row.set_metric(
        "Session",
        47.0,
        datetime.now() + timedelta(hours=1),
        window=timedelta(hours=5),
    )

    assert row.bar._pace_pct == pytest.approx(80, abs=1)  # noqa: SLF001
    assert "Time elapsed:" in row.bar.toolTip()


def test_metric_row_renders_note_only_metric_without_empty_gauge(qtbot):
    row = _MetricRow()
    qtbot.addWidget(row)

    row.set_metric("Models: none", None, None, note="No activity.")

    assert row.label.text() == "Models: none"
    assert row.bar.isHidden()
    assert row.pct.isHidden()
    assert row.reset.isHidden()


def test_metric_row_right_aligns_split_note_metric(qtbot):
    row = _MetricRow()
    qtbot.addWidget(row)

    row.set_metric(
        "Balance $11.50 left · Spend today $0.00 / month $0.00",
        None,
        None,
        note="OpenRouter summary.",
    )

    assert row.label.text() == "Balance $11.50 left"
    assert row.reset.text() == "Spend today $0.00 / month $0.00"
    assert row.reset.width() > 92
    assert row.reset.toolTip() == ""
    assert "#d1d5db" in row.reset.styleSheet()
    assert row.bar.isHidden()
    assert row.pct.isHidden()
    assert not row.reset.isHidden()


def test_metric_row_keeps_timeline_bar_without_missing_percent(qtbot):
    row = _MetricRow()
    qtbot.addWidget(row)

    row.set_metric(
        "Today ($0.00/$5.00)",
        None,
        datetime.now() + timedelta(hours=8),
        window=timedelta(days=1),
    )

    assert not row.bar.isHidden()
    assert row.pct.isHidden()
    assert not row.reset.isHidden()


def test_summary_chip_stores_pace(qtbot):
    chip = _SummaryChip()
    qtbot.addWidget(chip)

    chip.set_state("Claude 37%", 37.0, "ok", pace=37)

    assert chip._pace_pct == 37  # noqa: SLF001
