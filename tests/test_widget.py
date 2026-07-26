from datetime import datetime, timedelta

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from aigauge import __version__
from aigauge.config import BrowserAccount, ColorThresholds, Config
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


def test_title_bars_offer_distinct_view_and_hide_controls(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)

    assert widget.collapse_btn.text() == "▾"
    assert widget.collapse_btn.toolTip() == "Switch to compact view"
    assert widget.hide_btn.text() == "—"
    assert widget.hide_btn.toolTip() == "Hide to system tray"
    assert widget.close_btn.text() == "✕"
    assert widget.close_btn.toolTip() == "Quit AI Gauge"

    widget.set_collapsed(True)
    assert widget._expand_btn.text() == "▴"  # noqa: SLF001
    assert widget._collapsed_hide_btn.toolTip() == "Hide to system tray"  # noqa: SLF001
    assert widget._collapsed_close_btn.text() == "✕"  # noqa: SLF001
    assert widget._collapsed_close_btn.toolTip() == "Quit AI Gauge"  # noqa: SLF001

    with qtbot.waitSignal(widget.quit_requested):
        widget._collapsed_close_btn.click()  # noqa: SLF001

    widget.show()
    qtbot.waitUntil(widget.isVisible)
    widget._collapsed_hide_btn.click()  # noqa: SLF001
    assert widget.isHidden()


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
    config.browser_accounts.append(
        BrowserAccount(
            id="opencode_go-work",
            kind="opencode_go",
            name="Work",
            usage_url="https://opencode.ai/workspace/work/go",
        )
    )
    widget = UsageWidget(config)
    qtbot.addWidget(widget)

    widget.ensure_tile("codex-work", "Codex (Work)")
    widget.ensure_tile("claude-team", "Claude (Team)")
    widget.ensure_tile("codex", "Codex")
    widget.ensure_tile("claude", "Claude")
    widget.ensure_tile("opencode_go-work", "OpenCode (Work)")
    widget.ensure_tile("opencode_go", "OpenCode")
    widget.ensure_tile("copilot", "Copilot")

    assert _tile_order(widget) == [
        "claude",
        "claude-team",
        "codex",
        "codex-work",
        "opencode_go",
        "opencode_go-work",
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


def test_codex_ratio_label_hides_without_session_and_returns_with_it(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    fetched = datetime(2026, 7, 14, 12, 0)
    weekly_only = UsageSnapshot(
        provider="codex",
        status=SnapshotStatus.OK,
        metrics=[
            UsageMetric("Weekly", 10.0, fetched + timedelta(days=5)),
        ],
        fetched_at=fetched,
    )

    widget.update_snapshot(weekly_only, "Codex")
    widget.set_ratio("codex", _estimate(True, 6.7), recent=[6.7])

    label = widget._tiles["codex"].ratio_label  # noqa: SLF001
    assert label.isHidden()

    widget.update_snapshot(_ok_snapshot("codex"), "Codex")
    widget.set_ratio("codex", _estimate(True, 6.7), recent=[6.7])

    assert not label.isHidden()
    assert "6.7/wk" in label.text()


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




def test_opencode_go_auth_tile_uses_sign_in_button(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)

    widget.update_snapshot(
        UsageSnapshot(
            provider="opencode_go",
            status=SnapshotStatus.AUTH_REQUIRED,
            error="Not signed in.",
        ),
        "OpenCode",
    )

    tile = widget._tiles["opencode_go"]  # noqa: SLF001
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




def test_browser_tile_collapses_to_inline_metric_chips(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    reset_at = datetime.now() + timedelta(hours=2, minutes=30)

    widget.update_snapshot(
        UsageSnapshot(
            provider="claude",
            status=SnapshotStatus.OK,
            metrics=[
                UsageMetric("Session", 85.0, reset_at),
                UsageMetric("Weekly", 9.0, reset_at + timedelta(days=4)),
            ],
        ),
        "Claude",
    )

    tile = widget._tiles["claude"]  # noqa: SLF001
    assert not tile.expand_btn.isHidden()
    assert [row.label.text() for row in tile._rows] == ["Session", "Weekly"]  # noqa: SLF001

    tile.set_expanded(False)

    assert tile._rows == []  # noqa: SLF001
    assert not tile._compact_summary.isHidden()  # noqa: SLF001
    assert [item.code.text() for item in tile._compact_metrics] == ["S", "W"]  # noqa: SLF001
    assert [item.pct.text() for item in tile._compact_metrics] == ["85%", "9%"]  # noqa: SLF001
    assert tile._compact_metrics[0].bar.value() == 85  # noqa: SLF001


def test_collapsed_browser_tile_hides_ratio_label(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    widget.update_snapshot(_ok_snapshot("codex"), "Codex")
    tile = widget._tiles["codex"]  # noqa: SLF001

    widget.set_ratio("codex", _estimate(True, 6.7), [], _estimate(True, 6.7))
    assert not tile.ratio_label.isHidden()

    tile.set_expanded(False)
    widget.set_ratio("codex", _estimate(True, 6.7), [], _estimate(True, 6.7))

    assert tile.ratio_label.isHidden()


def test_ratio_label_returns_after_browser_tile_reexpanded(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    widget.update_snapshot(_ok_snapshot("claude"), "Claude")
    tile = widget._tiles["claude"]  # noqa: SLF001

    widget.set_ratio("claude", _estimate(True, 9.2), [9.2], _estimate(True, 9.2))
    assert "9.2/wk" in tile.ratio_label.text()

    tile.set_expanded(False)
    assert tile.ratio_label.isHidden()

    tile.set_expanded(True)
    assert not tile.ratio_label.isHidden()
    assert "9.2/wk" in tile.ratio_label.text()


def test_configured_collapsed_browser_tile_starts_compact(qtbot):
    config = Config()
    config.collapsed_tiles = ["claude"]
    widget = UsageWidget(config)
    qtbot.addWidget(widget)

    widget.update_snapshot(_ok_snapshot("claude"), "Claude")

    tile = widget._tiles["claude"]  # noqa: SLF001
    assert tile._rows == []  # noqa: SLF001
    assert not tile._compact_summary.isHidden()  # noqa: SLF001


def test_secondary_opencode_collapsed_tile_shows_rolling_and_monthly(qtbot):
    config = Config()
    config.browser_accounts.append(
        BrowserAccount(
            id="opencode_go-work",
            kind="opencode_go",
            name="Work",
            usage_url="https://opencode.ai/workspace/work/go",
        )
    )
    widget = UsageWidget(config)
    qtbot.addWidget(widget)
    reset_at = datetime.now() + timedelta(hours=4)

    widget.update_snapshot(
        UsageSnapshot(
            provider="opencode_go-work",
            status=SnapshotStatus.OK,
            metrics=[
                UsageMetric("Rolling", 13.0, reset_at),
                UsageMetric("Weekly", 14.0, reset_at + timedelta(days=3)),
                UsageMetric("Monthly", 7.0, reset_at + timedelta(days=30)),
            ],
        ),
        "OpenCode (Work)",
    )

    tile = widget._tiles["opencode_go-work"]  # noqa: SLF001
    tile.set_expanded(False)

    assert [item.code.text() for item in tile._compact_metrics] == ["R", "M"]  # noqa: SLF001
    assert [item.pct.text() for item in tile._compact_metrics] == ["13%", "7%"]  # noqa: SLF001


def test_expanded_refresh_state_includes_next_refresh_countdown(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)

    widget.set_refresh_state(
        active=True,
        minutes=5,
        next_at=datetime.now() + timedelta(minutes=3, seconds=5),
    )

    assert widget.cadence_label.text().endswith("4m")
    assert "5 min cadence" in widget.cadence_label.toolTip()


def test_expanded_refresh_state_includes_now_when_refresh_is_due(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)

    widget.set_refresh_state(
        active=False,
        minutes=60,
        next_at=datetime.now() - timedelta(seconds=1),
    )

    assert widget.cadence_label.text().endswith("now")


def test_widget_clamps_saved_width_and_ignores_saved_height(qtbot):
    config = Config()
    config.window.width = 5000
    config.window.height = 700
    config.window.manually_resized = True

    widget = UsageWidget(config)
    qtbot.addWidget(widget)
    widget._do_refit_height()  # noqa: SLF001

    assert widget.width() == 900
    assert widget.height() != 700
    assert widget.minimumHeight() == widget.maximumHeight() == widget.height()


def test_expanded_widget_resizes_width_only_and_persists_width(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    widget._do_refit_height()  # noqa: SLF001
    fitted_height = widget.height()

    widget.resize(520, fitted_height + 300)
    widget._save_expanded_width()  # noqa: SLF001

    assert widget.width() == 520
    assert widget.height() == fitted_height
    assert widget._config.window.width == 520  # noqa: SLF001
    assert widget._config.window.manually_resized is False  # noqa: SLF001

    widget.set_collapsed(True)
    assert widget.width() == 340
    widget.set_collapsed(False)
    widget._do_refit_height()  # noqa: SLF001
    assert widget.width() == 520
    assert widget.height() == fitted_height


def test_resize_grip_stops_before_standard_gauge_rows_would_wrap(qtbot):
    config = Config()
    config.window.width = 620
    widget = UsageWidget(config)
    qtbot.addWidget(widget)
    widget.update_snapshot(
        UsageSnapshot(
            provider="copilot",
            status=SnapshotStatus.OK,
            metrics=[
                UsageMetric(
                    "Monthly credits",
                    52.0,
                    reset_label="123d 12h",
                )
            ],
        ),
        "Copilot",
    )
    gauge_row = widget._tiles["copilot"]._rows[0]  # noqa: SLF001
    widget.update_snapshot(
        UsageSnapshot(
            provider="opencode_go-gmail",
            status=SnapshotStatus.OK,
            metrics=[
                UsageMetric("Rolling", 13.0, reset_label="2h 13m"),
                UsageMetric("Weekly", 5.0, reset_label="23h 58m"),
                UsageMetric("Monthly", 97.0, reset_label="30.9d"),
            ],
        ),
        "OpenCode (Gmail)",
    )
    compact_tile = widget._tiles["opencode_go-gmail"]  # noqa: SLF001
    compact_tile.set_expanded(False)
    widget.update_snapshot(
        UsageSnapshot(
            provider="openrouter",
            status=SnapshotStatus.OK,
            metrics=[
                UsageMetric(
                    "Balance $51.39 left · Spend today $0.00 / month $95.11"
                )
            ],
        ),
        "OpenRouter",
    )
    openrouter_row = widget._tiles["openrouter"]._rows[0]  # noqa: SLF001
    widget.set_refresh_state(
        active=True,
        minutes=5,
        next_at=datetime.now() + timedelta(minutes=4),
    )
    widget._last_fetch_at = datetime.now() - timedelta(minutes=1)  # noqa: SLF001
    widget._refresh_header_labels()  # noqa: SLF001
    widget.show()
    widget.resize(620, widget.height())
    widget._do_refit_height()  # noqa: SLF001
    QApplication.processEvents()
    wide_height = widget.height()
    minimum_width = widget.minimumWidth()

    assert minimum_width > 280
    assert compact_tile._compact_below_header is False  # noqa: SLF001
    assert openrouter_row._reset_stacked is False  # noqa: SLF001

    grip = widget._resize_grip  # noqa: SLF001
    start = grip.rect().center()
    QTest.mousePress(
        grip,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        start,
    )
    assert widget._resizing_with_grip is True  # noqa: SLF001
    last_pos = start
    for delta in (-120, -180, -220, -260, -320, -400):
        last_pos = QPoint(start.x() + delta, start.y())
        QTest.mouseMove(grip, last_pos)
        QApplication.processEvents()

    # The live minimum clamps the drag before the normal gauge can clip or
    # move onto a second line. Height and layout remain stable during drag.
    assert widget.width() == minimum_width
    assert widget.height() == wide_height
    assert gauge_row.bar.y() < gauge_row.label.geometry().bottom()
    assert gauge_row.bar.geometry().right() < gauge_row.pct.x()
    assert gauge_row.reset.geometry().right() <= gauge_row.rect().right()

    QTest.mouseRelease(
        grip,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        last_pos,
    )
    QApplication.processEvents()

    assert widget._resizing_with_grip is False  # noqa: SLF001
    assert widget.width() == minimum_width
    assert gauge_row.bar.y() < gauge_row.label.geometry().bottom()
    assert gauge_row.reset.geometry().right() <= gauge_row.rect().right()
    assert widget._header_widget.minimumSizeHint().width() <= widget.width()  # noqa: SLF001
    assert widget.refresh_btn.isVisible()
    assert widget.settings_btn.isVisible()
    assert widget.close_btn.isVisible()

    widget.resize(620, widget.height())
    widget._do_refit_height()  # noqa: SLF001
    QApplication.processEvents()
    assert compact_tile._compact_below_header is False  # noqa: SLF001
    assert openrouter_row._reset_stacked is False  # noqa: SLF001
    assert widget.title_label.text() == f"AI Gauge {__version__}"

def test_expanded_height_caps_and_scrolls_when_content_exceeds_screen(
    qtbot,
    monkeypatch,
):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    for index in range(20):
        provider = f"provider-{index}"
        widget.update_snapshot(
            UsageSnapshot(
                provider=provider,
                status=SnapshotStatus.OK,
                metrics=[UsageMetric("Usage", 25.0)],
            ),
            f"Provider {index}",
        )
    monkeypatch.setattr(widget, "_available_expanded_height", lambda: 180)
    widget.show()
    widget._do_refit_height()  # noqa: SLF001
    QApplication.processEvents()

    assert widget.height() == 180
    assert widget._tile_scroll.verticalScrollBar().maximum() > 0  # noqa: SLF001


def test_resize_grip_has_its_own_footer_below_provider_content(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    widget.ensure_tile("copilot", "Copilot")
    widget.update_snapshot(_ok_snapshot("copilot"), "Copilot")
    widget.show()
    widget._do_refit_height()  # noqa: SLF001

    assert widget._resize_footer.y() >= (  # noqa: SLF001
        widget._tile_scroll.y() + widget._tile_scroll.height()  # noqa: SLF001
    )


def test_expanded_header_compacts_progressively_at_280(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    next_at = datetime.now() + timedelta(minutes=4)
    widget.set_refresh_state(True, 5, next_at)
    widget._last_fetch_at = datetime.now() - timedelta(minutes=1)  # noqa: SLF001
    widget._refresh_header_labels()  # noqa: SLF001

    widget.resize(620, widget.height())
    widget._refresh_cadence_label()  # noqa: SLF001
    assert widget.title_label.text() == f"AI Gauge {__version__}"
    assert not widget.age_label.isHidden()
    assert "active next" in widget.cadence_label.text()

    widget.resize(360, widget.height())
    widget._refresh_cadence_label()  # noqa: SLF001
    assert widget.width() == 360
    assert widget.cadence_label.text().endswith("4m")
    assert widget._header_widget.minimumSizeHint().width() <= widget.width()  # noqa: SLF001

    widget.resize(280, widget.height())
    widget._refresh_cadence_label()  # noqa: SLF001
    assert widget.width() == 280
    assert not widget.title_label.isHidden()
    assert not widget.refresh_btn.isHidden()
    assert not widget.settings_btn.isHidden()
    assert not widget.close_btn.isHidden()
    assert widget._header_widget.minimumSizeHint().width() <= widget.width()  # noqa: SLF001

    widget.resize(200, widget.height())
    assert widget.width() == 280

    widget.resize(620, widget.height())
    widget._refresh_cadence_label()  # noqa: SLF001
    assert widget.title_label.text() == f"AI Gauge {__version__}"
    assert not widget.age_label.isHidden()
    assert "active next" in widget.cadence_label.text()


def test_metric_row_clamps_overage_fill_and_uses_account_threshold_color(qtbot):
    colors = ColorThresholds(
        green_max=30,
        yellow_max=60,
        orange_max=90,
        red_color="#123456",
    )
    row = _MetricRow(colors=colors)
    qtbot.addWidget(row)

    row.set_metric("Credits", 110.0, datetime.now() + timedelta(days=4))

    assert row.bar._bar.value() == 100  # noqa: SLF001
    assert "#123456" in row.bar._bar.styleSheet()  # noqa: SLF001
    assert row.pct.text() == "110%"


def test_metric_row_percent_and_reset_columns_fit_their_text(qtbot):
    row = _MetricRow()
    qtbot.addWidget(row)
    row.set_metric("Weekly", 8.0, datetime.now() + timedelta(days=4))

    assert row.pct.width() <= row.pct.fontMetrics().horizontalAdvance("8%") + 2
    assert row.reset.width() <= (
        row.reset.fontMetrics().horizontalAdvance(row.reset.text()) + 2
    )


def test_provider_gauges_share_normalized_columns_at_minimum_width(qtbot):
    config = Config()
    config.window.width = 620
    widget = UsageWidget(config)
    qtbot.addWidget(widget)
    widget.update_snapshot(
        UsageSnapshot(
            provider="opencode_go-work",
            status=SnapshotStatus.OK,
            metrics=[
                UsageMetric("Rolling", 26.0, reset_label="57m"),
                UsageMetric("Weekly", 100.0, reset_label="23h 06m"),
                UsageMetric("Monthly", 5.0, reset_label="30.9d"),
            ],
        ),
        "OpenCode (Work)",
    )
    widget.show()
    widget._do_refit_height()  # noqa: SLF001
    QApplication.processEvents()
    rows = widget._tiles["opencode_go-work"]._rows  # noqa: SLF001

    def assert_columns_aligned():
        assert len({row.label.width() for row in rows}) == 1
        assert len({row.bar.x() for row in rows}) == 1
        assert len({row.bar.width() for row in rows}) == 1
        assert len({row.pct.x() for row in rows}) == 1
        assert len({row.reset.x() for row in rows}) == 1
        assert len({row.reset.width() for row in rows}) == 1

    assert_columns_aligned()
    minimum_width = widget.minimumWidth()
    assert minimum_width > 280

    widget.resize(280, widget.height())
    widget._do_refit_height()  # noqa: SLF001
    QApplication.processEvents()
    assert widget.width() == minimum_width
    assert all(row.bar.y() < row.label.geometry().bottom() for row in rows)
    assert all(row.reset.geometry().right() <= row.rect().right() for row in rows)
    assert_columns_aligned()

    widget.resize(620, widget.height())
    widget._do_refit_height()  # noqa: SLF001
    QApplication.processEvents()
    assert_columns_aligned()


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
    assert widget.windowOpacity() == pytest.approx(0.45, abs=1 / 255)

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
    assert row.reset.width() >= (
        row.reset.fontMetrics().horizontalAdvance(row.reset.text()) + 4
    )
    assert row.label.minimumWidth() >= (
        row.label.fontMetrics().horizontalAdvance(row.label.text()) + 4
    )
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
