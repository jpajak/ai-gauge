from datetime import datetime, timedelta

import pytest
from PyQt6.QtCore import QPoint, QSize, Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from aigauge import __version__
from aigauge.config import (
    WINDOW_COLLAPSED_MIN_HEIGHT,
    WINDOW_COLLAPSED_MIN_WIDTH,
    WINDOW_MAX_WIDTH,
    WINDOW_MIN_WIDTH,
    BrowserAccount,
    ColorThresholds,
    Config,
)
from aigauge.models import SnapshotStatus, UsageMetric, UsageSnapshot
from aigauge.ratio import RatioEstimate
from aigauge.widget import (
    CORNER_SNAP_DISTANCE,
    MIN_GAUGE_WIDTH,
    CORNER_SNAP_INSET,
    CORNER_SNAP_RELEASE_DISTANCE,
    PANEL_BG,
    PANEL_BORDER,
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


def test_window_snaps_near_corner_and_anchor_survives_layout_changes(qtbot):
    geo = QApplication.primaryScreen().availableGeometry()
    config = Config()
    widget = UsageWidget(config)
    qtbot.addWidget(widget)
    widget.update_snapshot(_ok_snapshot("codex"), "Codex")
    widget._do_refit_height()  # noqa: SLF001

    widget.move(
        geo.right() - widget.width() + 1 - CORNER_SNAP_DISTANCE // 2,
        geo.bottom() - widget.height() + 1 - CORNER_SNAP_DISTANCE // 2,
    )
    widget._update_snap_anchor_from_position(geo.bottomRight())  # noqa: SLF001

    assert config.window.snap_corner == "bottom_right"
    assert widget.x() + widget.width() - 1 == geo.right() - CORNER_SNAP_INSET
    assert widget.y() + widget.height() - 1 == geo.bottom() - CORNER_SNAP_INSET

    widget.set_header_visible(False)
    assert widget.x() + widget.width() - 1 == geo.right() - CORNER_SNAP_INSET
    assert widget.y() + widget.height() - 1 == geo.bottom() - CORNER_SNAP_INSET

    widget.set_collapsed(True)
    assert widget.x() + widget.width() - 1 == geo.right() - CORNER_SNAP_INSET
    assert widget.y() + widget.height() - 1 == geo.bottom() - CORNER_SNAP_INSET


def test_moving_away_or_disabling_snap_releases_corner_anchor(qtbot):
    geo = QApplication.primaryScreen().availableGeometry()
    config = Config()
    config.window.snap_corner = "top_left"
    widget = UsageWidget(config)
    qtbot.addWidget(widget)

    assert widget.pos() == QPoint(
        geo.left() + CORNER_SNAP_INSET,
        geo.top() + CORNER_SNAP_INSET,
    )

    center = geo.center()
    widget.move(
        center.x() - widget.width() // 2,
        center.y() - widget.height() // 2,
    )
    widget._update_snap_anchor_from_position(center)  # noqa: SLF001
    assert config.window.snap_corner is None

    config.window.snap_corner = "top_left"
    widget._apply_snap_anchor()  # noqa: SLF001
    anchored_position = widget.pos()
    widget.set_snap_to_corners(False)

    assert config.window.snap_to_corners is False
    assert config.window.snap_corner is None
    assert widget.pos() == anchored_position


def test_drag_previews_corner_snap_and_detaches_when_moved_away(qtbot):
    geo = QApplication.primaryScreen().availableGeometry()
    config = Config()
    widget = UsageWidget(config)
    qtbot.addWidget(widget)

    max_x = geo.right() - widget.width() + 1
    max_y = geo.bottom() - widget.height() + 1
    inside_capture = QPoint(
        max_x - CORNER_SNAP_DISTANCE + 2,
        max_y - CORNER_SNAP_DISTANCE + 2,
    )

    widget._move_during_drag(inside_capture, geo.bottomRight())  # noqa: SLF001

    assert widget._drag_snap_corner == "bottom_right"  # noqa: SLF001
    assert config.window.snap_corner is None, "preview must not persist before release"
    assert widget.pos() == QPoint(
        max_x - CORNER_SNAP_INSET,
        max_y - CORNER_SNAP_INSET,
    )

    # Moving just outside the capture zone stays stable instead of flickering.
    inside_release = QPoint(
        max_x - CORNER_SNAP_DISTANCE - 2,
        max_y - CORNER_SNAP_DISTANCE - 2,
    )
    widget._move_during_drag(inside_release, geo.bottomRight())  # noqa: SLF001
    assert widget._drag_snap_corner == "bottom_right"  # noqa: SLF001
    assert widget.pos() != inside_release

    # Continuing away beyond the release zone immediately restores free movement.
    detached = QPoint(
        max_x - CORNER_SNAP_RELEASE_DISTANCE - 1,
        max_y - CORNER_SNAP_RELEASE_DISTANCE - 1,
    )
    widget._move_during_drag(detached, detached)  # noqa: SLF001
    assert widget._drag_snap_corner is None  # noqa: SLF001
    assert widget.pos() == detached


def test_releasing_live_snap_commits_corner_anchor(qtbot):
    geo = QApplication.primaryScreen().availableGeometry()
    config = Config()
    widget = UsageWidget(config)
    qtbot.addWidget(widget)

    drag_offset = QPoint(12, 10)
    max_x = geo.right() - widget.width() + 1
    max_y = geo.bottom() - widget.height() + 1
    raw_position = QPoint(
        max_x - CORNER_SNAP_DISTANCE // 2,
        max_y - CORNER_SNAP_DISTANCE // 2,
    )
    widget._drag_offset = drag_offset  # noqa: SLF001
    widget._move_during_drag(raw_position, raw_position + drag_offset)  # noqa: SLF001

    widget._finish_window_drag(raw_position + drag_offset)  # noqa: SLF001

    assert widget._drag_offset is None  # noqa: SLF001
    assert widget._drag_snap_corner is None  # noqa: SLF001
    assert config.window.snap_corner == "bottom_right"
    assert widget.x() + widget.width() - 1 == geo.right() - CORNER_SNAP_INSET
    assert widget.y() + widget.height() - 1 == geo.bottom() - CORNER_SNAP_INSET


def test_releasing_after_detach_clears_previous_anchor(qtbot):
    geo = QApplication.primaryScreen().availableGeometry()
    config = Config()
    config.window.snap_corner = "top_left"
    widget = UsageWidget(config)
    qtbot.addWidget(widget)

    drag_offset = QPoint(8, 8)
    free_position = QPoint(
        geo.center().x() - widget.width() // 2,
        geo.center().y() - widget.height() // 2,
    )
    widget._drag_offset = drag_offset  # noqa: SLF001
    widget._move_during_drag(free_position, free_position + drag_offset)  # noqa: SLF001

    widget._finish_window_drag(free_position + drag_offset)  # noqa: SLF001

    assert config.window.snap_corner is None
    assert widget.pos() == free_position


def test_title_bars_offer_distinct_view_and_hide_controls(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)

    assert not widget.title_icon.pixmap().isNull()
    assert not widget._collapsed_title_icon.pixmap().isNull()  # noqa: SLF001
    assert widget.title_icon.size() == QSize(24, 24)
    assert widget._collapsed_title_icon.size() == QSize(18, 18)  # noqa: SLF001
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


def test_header_visibility_is_independent_of_content_density(qtbot):
    config = Config()
    widget = UsageWidget(config)
    qtbot.addWidget(widget)
    widget.update_snapshot(_ok_snapshot("codex"), "Codex")
    widget._do_refit_height()  # noqa: SLF001
    height_with_header = widget.height()

    widget.set_header_visible(False)
    widget._do_refit_height()  # noqa: SLF001

    assert config.window.show_header is False
    assert widget._header_widget.isHidden()  # noqa: SLF001
    assert widget.height() < height_with_header
    assert not widget._tile_scroll.isHidden()  # noqa: SLF001

    widget.set_collapsed(True)
    assert not widget._collapsed_widget.isHidden()  # noqa: SLF001
    assert widget._collapsed_header_widget.isHidden()  # noqa: SLF001
    assert _collapsed_chip_texts(widget)

    widget.set_header_visible(True)
    assert config.window.show_header is True
    assert not widget._collapsed_header_widget.isHidden()  # noqa: SLF001
    assert widget._header_widget.isHidden()  # noqa: SLF001

    widget.set_collapsed(False)
    assert not widget._header_widget.isHidden()  # noqa: SLF001
    assert widget._collapsed_header_widget.isHidden()  # noqa: SLF001


def test_context_menu_topmost_toggle_persists(qtbot):
    config = Config()
    widget = UsageWidget(config)
    qtbot.addWidget(widget)

    widget.set_always_on_top(False)

    assert config.window.always_on_top is False


def _ordered_widget(qtbot, config: Config, providers: list[str]) -> UsageWidget:
    widget = UsageWidget(config)
    qtbot.addWidget(widget)
    for provider in providers:
        widget.update_snapshot(_ok_snapshot(provider), provider.title())
    return widget


def test_tiles_follow_configured_provider_order(qtbot):
    config = Config(
        provider_order=["copilot", "codex", "claude"],
        browser_accounts=[
            BrowserAccount(id="claude", kind="claude"),
            BrowserAccount(id="codex", kind="codex"),
            BrowserAccount(id="claude-team", kind="claude", name="Team"),
        ],
    )

    widget = _ordered_widget(
        qtbot, config, ["claude-team", "codex", "claude", "copilot"]
    )

    # Both Claude accounts stay together, in account-list order.
    assert _tile_order(widget) == ["copilot", "codex", "claude", "claude-team"]


def test_apply_provider_order_re_sorts_tiles_and_summary_chips(qtbot):
    config = Config()
    widget = _ordered_widget(qtbot, config, ["claude", "codex", "copilot"])
    widget.set_collapsed(True)
    assert _tile_order(widget) == ["claude", "codex", "copilot"]
    assert [t.split()[0] for t in _collapsed_chip_texts(widget)] == [
        "Claude",
        "Codex",
        "Copilot",
    ]

    config.provider_order = ["copilot", "claude"]
    widget.apply_provider_order()

    assert _tile_order(widget) == ["copilot", "claude", "codex"]
    assert [t.split()[0] for t in _collapsed_chip_texts(widget)] == [
        "Copilot",
        "Claude",
        "Codex",
    ]


def test_apply_provider_order_leaves_ordered_layout_untouched(qtbot, monkeypatch):
    widget = _ordered_widget(qtbot, Config(), ["claude", "codex", "copilot"])
    before = _tile_order(widget)
    calls = []
    layout = widget._tile_layout  # noqa: SLF001
    monkeypatch.setattr(layout, "removeWidget", lambda w: calls.append(("rm", w)))
    monkeypatch.setattr(
        layout, "insertWidget", lambda *a, **k: calls.append(("ins", a))
    )

    widget.apply_provider_order()

    assert calls == []
    assert _tile_order(widget) == before


def test_apply_provider_order_keeps_non_tile_items_in_place(qtbot):
    from PyQt6.QtWidgets import QLabel

    config = Config()
    widget = _ordered_widget(qtbot, config, ["claude", "codex", "copilot"])
    marker = QLabel("marker")
    widget._tile_layout.insertWidget(1, marker)  # noqa: SLF001

    config.provider_order = ["copilot", "codex", "claude"]
    widget.apply_provider_order()

    layout = widget._tile_layout  # noqa: SLF001
    items = [layout.itemAt(i).widget() for i in range(layout.count())]
    assert items[1] is marker
    assert [w.provider for w in items if w is not marker] == [
        "copilot",
        "codex",
        "claude",
    ]


def test_tile_sort_key_ranks_unknown_family_last(qtbot):
    widget = _ordered_widget(qtbot, Config(), [])

    assert widget._tile_sort_key("mystery")[0] > widget._tile_sort_key("antigravity")[0]  # noqa: SLF001


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


def test_claude_weekly_limit_hit_replaces_ratio_with_explicit_status(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    fetched = datetime(2026, 9, 19, 12, 0)
    widget.update_snapshot(
        UsageSnapshot(
            provider="claude",
            status=SnapshotStatus.OK,
            metrics=[
                UsageMetric(
                    "Session",
                    5.0,
                    note="Paused until your week resets",
                    window=timedelta(hours=5),
                ),
                UsageMetric(
                    "Weekly",
                    100.0,
                    fetched + timedelta(days=2),
                    window=timedelta(days=7),
                ),
                UsageMetric(
                    "Fable",
                    69.0,
                    fetched + timedelta(days=2),
                    window=timedelta(days=7),
                ),
            ],
            fetched_at=fetched,
        ),
        "Claude",
    )
    widget.set_ratio("claude", _estimate(True, 8.8), recent=[8.8])

    tile = widget._tiles["claude"]  # noqa: SLF001
    assert tile.status.text() == "limit hit"
    assert "#ef4444" in tile.status.styleSheet()
    assert "weekly plan limit is fully used" in tile.status.toolTip().lower()
    assert "paused until your week resets" in tile.status.toolTip().lower()
    assert tile.ratio_label.isHidden()


def test_independent_or_non_claude_limits_do_not_mark_provider_limit_hit(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    fetched = datetime(2026, 9, 19, 12, 0)
    for provider, metrics in (
        (
            "claude",
            [
                UsageMetric("Weekly", 69.0),
                UsageMetric("Fable", 100.0),
            ],
        ),
        ("codex", [UsageMetric("Weekly", 100.0)]),
    ):
        widget.update_snapshot(
            UsageSnapshot(
                provider=provider,
                status=SnapshotStatus.OK,
                metrics=metrics,
                fetched_at=fetched,
            ),
            provider.title(),
        )
        assert widget._tiles[provider].status.text() == ""  # noqa: SLF001


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




def test_opencode_go_auth_tile_uses_add_key_button(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)

    widget.update_snapshot(
        UsageSnapshot(
            provider="opencode_go",
            status=SnapshotStatus.AUTH_REQUIRED,
            error="Add an OpenCode Go API key in Settings.",
        ),
        "OpenCode",
    )

    tile = widget._tiles["opencode_go"]  # noqa: SLF001
    assert tile.action_btn.text() == "Add key"
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

    assert widget.status_label.text().endswith("next 4m")
    assert "5 min cadence" in widget.status_label.toolTip()


def test_expanded_refresh_state_includes_now_when_refresh_is_due(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)

    widget.set_refresh_state(
        active=False,
        minutes=60,
        next_at=datetime.now() - timedelta(seconds=1),
    )

    assert widget.status_label.text().endswith("next now")


def test_refresh_status_sits_on_the_footer_not_the_title_bar(qtbot):
    """The title bar keeps identity and controls; status moves to the footer."""
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)

    widget.set_refresh_state(True, 5, datetime.now() + timedelta(minutes=4))
    widget._last_fetch_at = datetime.now() - timedelta(minutes=1)  # noqa: SLF001
    widget._refresh_header_labels()  # noqa: SLF001

    assert widget.status_label.parent() is widget._resize_footer  # noqa: SLF001
    assert widget.status_label.text() == "Updated 1m ago · next 4m"
    assert widget.title_label.text() == f"AI Gauge {__version__}"

    header_texts = [
        widget._header_widget.layout().itemAt(i).widget()  # noqa: SLF001
        for i in range(widget._header_widget.layout().count())  # noqa: SLF001
    ]
    assert widget.status_label not in header_texts


def test_refresh_status_stays_visible_when_the_header_is_hidden(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    widget.update_snapshot(_ok_snapshot("codex"), "Codex")
    widget.set_refresh_state(True, 5, datetime.now() + timedelta(minutes=4))
    widget._refresh_header_labels()  # noqa: SLF001

    widget.set_header_visible(False)

    assert widget._header_widget.isVisibleTo(widget) is False  # noqa: SLF001
    assert widget._resize_footer.isVisibleTo(widget) is True  # noqa: SLF001
    assert widget.status_label.isVisibleTo(widget) is True
    assert "next 4m" in widget.status_label.text()


def test_refresh_status_shows_refreshing_then_returns_to_the_countdown(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    widget.set_refresh_state(True, 5, datetime.now() + timedelta(minutes=4))
    widget._last_fetch_at = datetime.now() - timedelta(minutes=1)  # noqa: SLF001
    widget._refresh_header_labels()  # noqa: SLF001

    widget.set_refreshing(True)
    assert widget.status_label.text() == "Refreshing…"

    widget.set_refreshing(False)
    widget._refresh_header_labels()  # noqa: SLF001
    assert widget.status_label.text() == "Updated 1m ago · next 4m"


def test_refresh_status_sheds_the_age_half_before_hiding(qtbot, monkeypatch):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    widget.set_refresh_state(True, 5, datetime.now() + timedelta(minutes=4))
    widget._last_fetch_at = datetime.now() - timedelta(minutes=1)  # noqa: SLF001
    widget._refresh_header_labels()  # noqa: SLF001
    assert widget.status_label.text() == "Updated 1m ago · next 4m"

    # Measured, not hardcoded: text widths differ across platforms.
    fits_full = widget._resize_footer.layout().minimumSize().width()  # noqa: SLF001
    monkeypatch.setattr(widget, "width", lambda: fits_full - 1)
    widget._apply_footer_status()  # noqa: SLF001
    assert widget.status_label.text() == "next 4m"

    fits_short = widget._resize_footer.layout().minimumSize().width()  # noqa: SLF001
    monkeypatch.setattr(widget, "width", lambda: fits_short - 1)
    widget._apply_footer_status()  # noqa: SLF001
    assert widget.status_label.isHidden()


def test_clicking_the_footer_status_refreshes_but_dragging_does_not(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    widget.set_refresh_state(True, 5, datetime.now() + timedelta(minutes=4))
    widget._last_fetch_at = datetime.now() - timedelta(minutes=1)  # noqa: SLF001
    widget._refresh_header_labels()  # noqa: SLF001
    widget.resize(340, widget.height())
    widget.show()
    widget._do_refit_height()  # noqa: SLF001

    requested = []
    widget.refresh_requested.connect(lambda: requested.append(True))

    origin = widget.status_label.mapToGlobal(
        widget.status_label.rect().center()
    )
    widget._press_global = origin  # noqa: SLF001
    assert widget._is_status_click(origin) is True  # noqa: SLF001
    # A drag that happens to start on the status text moves the window instead.
    assert widget._is_status_click(origin + QPoint(30, 0)) is False  # noqa: SLF001
    # So does a click that lands anywhere else on the panel.
    widget._press_global = widget.mapToGlobal(QPoint(4, 4))  # noqa: SLF001
    assert widget._is_status_click(widget._press_global) is False  # noqa: SLF001


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
    # The pill fits its own content instead of inheriting the panel's width,
    # and can be dragged narrower still.
    assert widget.width() == widget._preferred_collapsed_width()  # noqa: SLF001
    assert widget.width() < 340
    assert widget.minimumWidth() == widget._minimum_collapsed_width()  # noqa: SLF001
    assert widget.minimumWidth() < widget.width()
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
                UsageMetric("Balance $51.39 · Today $0.00 · Month $95.11")
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

    assert minimum_width > WINDOW_MIN_WIDTH
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

def test_resize_grip_stops_before_the_openrouter_row_would_stack(qtbot):
    """The tile layout only re-flows once the drag ends.

    The width floor left OpenRouter's summary row out, so a drag could pass
    the point where that row stacks its spend under its balance and then
    spring onto two lines the moment the mouse came up.
    """
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    widget.update_snapshot(_ok_snapshot("claude"), "Claude")
    widget.update_snapshot(
        UsageSnapshot(
            provider="openrouter",
            status=SnapshotStatus.OK,
            metrics=[
                UsageMetric("Balance $49.96 · Today $0.00 · Month $232.25")
            ],
        ),
        "OpenRouter",
    )
    widget.show()
    widget.resize(620, widget.height())
    widget._do_refit_height()  # noqa: SLF001
    QApplication.processEvents()
    row = widget._tiles["openrouter"]._rows[0]  # noqa: SLF001
    assert row._reset_stacked is False  # noqa: SLF001

    grip = widget._resize_grip  # noqa: SLF001
    start = grip.rect().center()
    QTest.mousePress(
        grip,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        start,
    )
    last_pos = start
    for delta in (-100, -200, -300, -400):
        last_pos = QPoint(start.x() + delta, start.y())
        QTest.mouseMove(grip, last_pos)
        QApplication.processEvents()
    during = (widget.width(), widget.height(), row._reset_stacked)  # noqa: SLF001

    QTest.mouseRelease(
        grip,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        last_pos,
    )
    QApplication.processEvents()

    # Releasing settles on exactly what the drag was already showing.
    assert (widget.width(), widget.height(), row._reset_stacked) == during  # noqa: SLF001
    assert row._reset_stacked is False  # noqa: SLF001
    assert widget.width() == widget.minimumWidth()


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


def test_dense_panel_narrows_past_the_old_280_floor(qtbot):
    """The gauge row's own minimums used to hold a dense panel at ~295px."""
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    widget.update_snapshot(_ok_snapshot("claude"), "Claude")
    widget.update_snapshot(_ok_snapshot("codex"), "Codex")
    widget.show()
    widget.resize(620, widget.height())
    widget._do_refit_height()  # noqa: SLF001
    QApplication.processEvents()

    widget.resize(100, widget.height())
    widget._do_refit_height()  # noqa: SLF001
    QApplication.processEvents()

    assert widget.width() == widget.minimumWidth() < 280
    row = widget._tiles["claude"]._rows[0]  # noqa: SLF001
    # The row is still one line: label, gauge, percent, reset.
    assert row.bar.y() < row.label.geometry().bottom()
    assert row.bar.geometry().right() < row.pct.x()
    assert row.reset.geometry().right() <= row.rect().right()
    assert row.bar.width() >= MIN_GAUGE_WIDTH


def test_expanded_header_survives_the_width_floor_intact(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    next_at = datetime.now() + timedelta(minutes=4)
    widget.set_refresh_state(True, 5, next_at)
    widget._last_fetch_at = datetime.now() - timedelta(minutes=1)  # noqa: SLF001
    widget._refresh_header_labels()  # noqa: SLF001

    widget.resize(620, widget.height())
    widget._apply_responsive_header()  # noqa: SLF001
    assert widget.title_label.text() == f"AI Gauge {__version__}"
    # The status text moved to the footer, so it no longer competes with the
    # controls for title-bar width.
    assert widget.status_label.text() == "Updated 1m ago · next 4m"

    # A provider-less panel bottoms out at the constant floor with the title
    # bar intact. How much of the version string survives there depends on the
    # platform's font metrics, so assert what is invariant: the header fits,
    # and nothing in it was dropped to make it fit.
    widget.resize(100, widget.height())
    widget._do_refit_height()  # noqa: SLF001
    widget._apply_responsive_header()  # noqa: SLF001
    assert widget.width() == WINDOW_MIN_WIDTH
    assert widget.title_label.text().startswith("AI")
    assert not widget.title_label.isHidden()
    assert not widget.refresh_btn.isHidden()
    assert not widget.settings_btn.isHidden()
    assert not widget.close_btn.isHidden()
    assert not widget.status_label.isHidden()
    assert widget._header_widget.layout().minimumSize().width() <= widget.width()  # noqa: SLF001


def test_expanded_header_compacts_progressively_when_it_cannot_fit(
    qtbot, monkeypatch
):
    """Safety net for a title bar narrower than its own contents.

    The widths are measured rather than hardcoded: the point at which each
    step is needed depends on the platform's font metrics.
    """
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    full_title = f"AI Gauge {__version__}"

    def title_at(width: int) -> str:
        monkeypatch.setattr(widget, "width", lambda: width)
        widget._apply_responsive_header()  # noqa: SLF001
        return widget.title_label.text()

    assert title_at(WINDOW_MAX_WIDTH) == full_title
    fits_full = widget._header_widget.layout().minimumSize().width()  # noqa: SLF001

    assert title_at(fits_full) == full_title
    # One pixel short of the full title, the version goes first.
    assert title_at(fits_full - 1) == "AI Gauge"
    # Far past every step, the name itself is abbreviated but the controls
    # are all still there.
    assert title_at(10) == "AI"
    assert not widget.refresh_btn.isHidden()
    assert not widget.settings_btn.isHidden()
    assert not widget.close_btn.isHidden()


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
    assert minimum_width > WINDOW_MIN_WIDTH

    # Asking for less than the content floor clamps back up to it.
    widget.resize(100, widget.height())
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
                    "Today $1.31 · Month $21.90",
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

    # Collapsing shrinks to the pill's fitted height straight away rather than
    # keeping the expanded height until the next refit.
    assert widget.height() == widget.minimumHeight() == widget.maximumHeight()
    assert widget.height() <= WINDOW_COLLAPSED_MIN_HEIGHT + 34


def test_panel_palette_never_leaves_light_corners(qtbot):
    """Regression for issue #7.

    Qt erases a top-level widget with the palette Window brush before
    paintEvent runs, and paintEvent only covers the rounded rect. With the
    default brush the corners kept the system theme's colour, which on a light
    Windows theme showed as grey notches around the dark panel.
    """
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)

    assert widget.palette().color(QPalette.ColorRole.Window) == QColor(PANEL_BG)


def test_rounded_corners_are_masked_out_of_the_window_shape(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    widget.resize(340, 120)
    widget.show()

    mask = widget.mask()
    assert not mask.isEmpty()
    # The corner pixel is outside the window; a point inside the radius is not.
    assert not mask.contains(QPoint(0, 0))
    assert mask.contains(QPoint(30, 30))


def test_square_corners_setting_skips_the_mask(qtbot):
    config = Config()
    config.window.square_corners = True
    widget = UsageWidget(config)
    qtbot.addWidget(widget)
    widget.resize(340, 120)
    widget.show()

    assert widget.mask().isEmpty()
    assert widget._corner_radius() == 0  # noqa: SLF001


def test_collapsed_pill_can_be_dragged_narrower_and_remembers_it(qtbot):
    config = Config()
    widget = UsageWidget(config)
    qtbot.addWidget(widget)
    widget.update_snapshot(_ok_snapshot("codex"), "Codex")
    widget.set_collapsed(True)

    floor = widget.minimumWidth()
    assert floor == WINDOW_COLLAPSED_MIN_WIDTH
    assert widget.width() > floor  # starts fitted to the full header

    widget.resize(floor + 20, widget.height())
    widget._on_resize_finished()  # noqa: SLF001

    # The drag survives the refit that follows it, and is persisted.
    assert widget.width() == floor + 20
    assert config.window.collapsed_width == floor + 20

    widget.set_collapsed(False)
    widget.set_collapsed(True)
    assert widget.width() == floor + 20


def test_collapsed_pill_cannot_be_dragged_below_its_floor(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    widget.update_snapshot(_ok_snapshot("codex"), "Codex")
    widget.set_collapsed(True)

    widget.resize(40, widget.height())
    widget._on_resize_finished()  # noqa: SLF001

    assert widget.width() == widget.minimumWidth() == WINDOW_COLLAPSED_MIN_WIDTH


def test_collapsed_header_sheds_detail_as_the_pill_narrows(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    widget.update_snapshot(_ok_snapshot("codex"), "Codex")
    widget.set_collapsed(True)

    assert __version__ in widget._collapsed_title.text()  # noqa: SLF001

    widget.resize(widget.minimumWidth(), widget.height())
    widget._on_resize_finished()  # noqa: SLF001

    # At the floor only the icon and the buttons remain.
    assert widget._collapsed_title.text() == ""  # noqa: SLF001


def test_compact_pill_height_follows_its_row_count(qtbot):
    """Regression: compact mode used to be floored at a two-row height.

    A pill wide enough to fit every chip on one row — or one with the header
    hidden — reserved space for a second row it never used.
    """
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    for provider, name in (
        ("claude", "Claude"),
        ("codex", "Codex"),
        ("copilot", "Copilot"),
    ):
        widget.update_snapshot(_ok_snapshot(provider), name)
    widget.set_collapsed(True)
    widget.set_header_visible(False)

    widget.resize(700, widget.height())
    widget._on_resize_finished()  # noqa: SLF001

    margins = widget._collapsed_widget.layout().contentsMargins()  # noqa: SLF001
    one_row = margins.top() + margins.bottom() + _SummaryChip().height()
    assert one_row == WINDOW_COLLAPSED_MIN_HEIGHT
    assert widget.height() == one_row

    # Narrowing wraps the chips, and the pill grows by exactly what that costs.
    widget.resize(widget.minimumWidth(), widget.height())
    widget._on_resize_finished()  # noqa: SLF001
    assert widget.height() > one_row


def test_compact_pill_reflows_its_chips_when_it_narrows(qtbot):
    """Hiding the header narrows the pill; the chips must wrap, not clip."""
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    widget.update_snapshot(_ok_snapshot("claude"), "Claude")
    widget.update_snapshot(_ok_snapshot("copilot"), "Copilot")
    widget.set_collapsed(True)

    widget.set_header_visible(False)

    layout = widget._collapsed_summary_layout  # noqa: SLF001
    rows = [
        layout.itemAt(i).widget()
        for i in range(layout.count())
        if layout.itemAt(i).widget() is not widget._collapsed_label  # noqa: SLF001
    ]
    chrome = widget._collapsed_chrome_width()  # noqa: SLF001
    for row in rows:
        chips = [
            row.layout().itemAt(i).widget()
            for i in range(row.layout().count())
            if row.layout().itemAt(i).widget() is not None
        ]
        assert chips
        assert max(c.x() + c.width() for c in chips) <= widget.width() - chrome


def test_footer_draws_a_divider_above_the_status_row(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    widget.show()
    widget._do_refit_height()  # noqa: SLF001

    image = widget._resize_footer.grab().toImage()  # noqa: SLF001

    assert image.pixelColor(image.width() // 2, 0).name() == PANEL_BORDER


def test_collapsed_pill_keeps_its_height_when_the_grip_appears(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    widget.update_snapshot(_ok_snapshot("codex"), "Codex")
    widget.set_collapsed(True)

    # The grip floats over the corner rather than taking a footer row, so
    # making the pill resizable must not make it taller.
    assert widget._grip_overlaid is True  # noqa: SLF001
    assert widget._resize_footer.isVisibleTo(widget) is False  # noqa: SLF001
    assert widget.height() == widget._collapsed_widget.sizeHint().height()  # noqa: SLF001

    widget.set_collapsed(False)
    assert widget._grip_overlaid is False  # noqa: SLF001
    assert widget._resize_footer.isVisibleTo(widget) is True  # noqa: SLF001


def test_openrouter_model_expand_resizes_immediately(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    widget.update_snapshot(
        UsageSnapshot(
            provider="openrouter",
            status=SnapshotStatus.OK,
            metrics=[
                UsageMetric("Balance $11.50 · Today $0.00", None),
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


def test_always_on_top_suspension_is_reference_counted(qtbot, monkeypatch):
    config = Config()
    config.window.always_on_top = True
    widget = UsageWidget(config)
    qtbot.addWidget(widget)

    native_changes = []
    monkeypatch.setattr(
        widget,
        "_set_native_topmost",
        lambda on: native_changes.append(on) or True,
    )

    widget.suspend_always_on_top()
    widget.suspend_always_on_top()
    assert native_changes == [False]

    widget.restore_always_on_top()
    assert native_changes == [False]

    widget.restore_always_on_top()
    assert native_changes == [False, True]


def test_always_on_top_suspension_falls_back_to_qt_flags(qtbot, monkeypatch):
    config = Config()
    config.window.always_on_top = True
    widget = UsageWidget(config)
    qtbot.addWidget(widget)
    monkeypatch.setattr(widget, "_set_native_topmost", lambda _on: False)

    widget.suspend_always_on_top()
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
        "Balance $11.50 · Today $0.00 · Month $0.00",
        None,
        None,
        note="OpenRouter summary.",
    )

    assert row.label.text() == "Balance $11.50"
    assert row.reset.text() == "Today $0.00 · Month $0.00"
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


def _minimax_unlimited_snapshot(provider: str = "minimax") -> UsageSnapshot:
    return UsageSnapshot(
        provider=provider,
        status=SnapshotStatus.OK,
        metrics=[
            UsageMetric(
                "5h",
                12.0,
                datetime.now() + timedelta(hours=3),
                window=timedelta(hours=5),
            ),
            UsageMetric("Weekly", None, reset_label="Unlimited"),
        ],
    )


def test_minimax_auth_tile_says_api_key_needed_with_add_key_button(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)

    widget.update_snapshot(
        UsageSnapshot(
            provider="minimax",
            status=SnapshotStatus.AUTH_REQUIRED,
            error="Add a MiniMax Coding Plan API key in Settings.",
        ),
        "MiniMax",
    )

    tile = widget._tiles["minimax"]  # noqa: SLF001
    assert tile.action_btn.text() == "Add key"
    assert not tile.action_btn.isHidden()
    assert tile.status.text() == "API key needed"


def test_minimax_unlimited_weekly_row_renders_in_normal_and_compact_tiles(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)

    widget.update_snapshot(_minimax_unlimited_snapshot(), "MiniMax")
    tile = widget._tiles["minimax"]  # noqa: SLF001
    assert tile._supports_compact_collapse()  # noqa: SLF001

    tile.set_expanded(True)
    assert [row.label.text() for row in tile._rows] == ["5h", "Weekly"]  # noqa: SLF001
    weekly = tile._rows[1]  # noqa: SLF001
    assert weekly.pct.isHidden()
    assert weekly.reset.text() == "Unlimited"

    tile.set_expanded(False)
    # Compact mode shows only readings that have a percentage.
    assert [item.code.text() for item in tile._compact_metrics] == ["5"]  # noqa: SLF001
    assert [item.pct.text() for item in tile._compact_metrics] == ["12%"]  # noqa: SLF001

    # A second refresh and the window's own compact view also go through.
    widget.update_snapshot(_minimax_unlimited_snapshot(), "MiniMax")
    widget.set_collapsed(True)
    widget.set_collapsed(False)
