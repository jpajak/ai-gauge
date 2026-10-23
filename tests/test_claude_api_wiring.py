"""Claude API credit tile: how the provider is wired into the app.

The provider's own builder is covered by test_claude_api.py; the registry,
config and domain checks live beside their siblings in test_provider_registry,
test_config, test_external_login and test_login_window. This file holds the
cross-cutting checks: the usage cache and MCP guard, Settings, the tile, the
menu bar and the snapshot pipeline, all fed by the real builder's output.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from PyQt6.QtWidgets import QPushButton

from aigauge import settings_dialog
from aigauge.app import App
from aigauge.config import BrowserAccount, Config
from aigauge.mcp_server import check_usage_guard, recommend_ai_account
from aigauge.menubar import status_items
from aigauge.models import SnapshotStatus, UsageMetric, UsageSnapshot
from aigauge.providers.claude_api import _build_snapshot
from aigauge.settings_dialog import SettingsDialog
from aigauge.usage_cache import read_usage_cache, write_usage_cache
from aigauge.widget import UsageWidget

NOW = datetime(2026, 10, 23, 12, 0)


def _credit_snapshot(account_id: str = "claude_api") -> UsageSnapshot:
    """What the provider returns for one org with $87.60 of $100 left."""
    tranche = {
        "program_id": "subscription_api_credit",
        "currency": "USD",
        "granted_amount_minor_units": 10000,
        "remaining_amount_minor_units": 8760,
        "granted_at": "2026-10-08T13:47:24Z",
        "expires_at": "2026-11-05T12:00:00Z",
        "granted_exponent": 2,
        "remaining_exponent": 2,
        "scope_token": 0,
    }
    payload = {
        "orgs_status": 200,
        "api_orgs": 1,
        "credits": [
            {
                "status": 200,
                "error": None,
                "credit": {
                    "amount": 8760 + 1234,
                    "amount_without_scoped_credits": 1234,
                    "currency": "USD",
                    "promo_tranches": [tranche],
                },
            }
        ],
    }
    snapshot = _build_snapshot(payload, account_id, NOW)
    assert snapshot.status == SnapshotStatus.OK
    return snapshot


def _enabled_config(**policies: int) -> Config:
    config = Config(mcp_enabled=True)
    config.providers.claude_api = True
    config.mcp_pause_policies = dict(policies)
    return config


def _patch_mcp(monkeypatch, config: Config, cache: dict) -> None:
    monkeypatch.setattr("aigauge.mcp_server.Config.load", lambda: config)
    monkeypatch.setattr("aigauge.mcp_server.read_usage_cache", lambda: cache)


# ----- Provider output -------------------------------------------------------


def test_credit_bar_is_not_guard_eligible_and_text_lines_have_no_percentage():
    snapshot = _credit_snapshot()

    bars = [m for m in snapshot.metrics if m.percent_used is not None]
    assert [m.label for m in bars] == ["Credit"]
    assert bars[0].guard_eligible is False
    # Every other row is a text line: no percentage, so never eligible.
    assert [m.percent_used for m in snapshot.metrics[1:]] == [None] * (
        len(snapshot.metrics) - 1
    )
    assert len(snapshot.metrics) > 1


# ----- Usage cache and MCP guard ---------------------------------------------


def test_usage_cache_writes_the_credit_bar_as_not_guard_eligible(tmp_path, monkeypatch):
    monkeypatch.setattr("aigauge.usage_cache.app_data_dir", lambda: tmp_path)

    write_usage_cache({"claude_api": _credit_snapshot()})
    metrics = read_usage_cache()["accounts"]["claude_api"]["metrics"]

    credit = next(m for m in metrics if m["label"] == "Credit")
    assert credit["guard_eligible"] is False
    assert credit["percent_used"] is not None
    # Text lines carry no percentage, which the guard skips on its own.
    assert not any(
        m["guard_eligible"] for m in metrics if m["percent_used"] is not None
    )


def test_claude_api_account_is_configured_only_when_enabled():
    from aigauge import mcp_server

    config = Config(mcp_enabled=True)

    assert "claude_api" not in mcp_server._configured_account_ids(config)  # noqa: SLF001
    config.providers.claude_api = True
    assert "claude_api" in mcp_server._configured_account_ids(config)  # noqa: SLF001


def test_hand_set_pause_policy_for_claude_api_blocks_with_no_eligible_metric(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("aigauge.usage_cache.app_data_dir", lambda: tmp_path)
    snapshot = _credit_snapshot()
    snapshot.fetched_at = datetime.now()
    write_usage_cache({"claude_api": snapshot})
    config = _enabled_config(claude_api=90)
    _patch_mcp(monkeypatch, config, read_usage_cache())

    result = check_usage_guard("claude_api")

    assert result["allowed"] is False
    assert result["reason_code"] == "no_eligible_metric"
    assert recommend_ai_account()["account"] is None


def test_claude_api_is_never_recommended_beside_an_eligible_account(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("aigauge.usage_cache.app_data_dir", lambda: tmp_path)
    claude = UsageSnapshot(
        provider="claude",
        status=SnapshotStatus.OK,
        metrics=[UsageMetric("Weekly", 20.0)],
        fetched_at=datetime.now(),
    )
    credit = _credit_snapshot()
    credit.fetched_at = datetime.now()
    write_usage_cache({"claude": claude, "claude_api": credit})
    config = _enabled_config(claude=80, claude_api=90)
    _patch_mcp(monkeypatch, config, read_usage_cache())

    result = recommend_ai_account()

    assert result["account"]["account_id"] == "claude"
    # The Claude account's own guard is unchanged.
    assert check_usage_guard("claude")["allowed"] is True


# ----- Settings --------------------------------------------------------------


def test_settings_shows_no_pause_row_for_claude_api_but_keeps_claude_s(
    qtbot, monkeypatch
):
    monkeypatch.setattr(settings_dialog, "set_start_at_login", lambda enabled: None)
    config = _enabled_config(claude=70, claude_api=80)
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)

    assert "claude" in dialog.mcp_policy_controls
    assert "claude_api" not in dialog.mcp_policy_controls

    dialog.apply_to(config)

    assert config.mcp_pause_policies == {"claude": 70}


def test_settings_has_a_claude_api_page_with_sign_in_for_the_account(qtbot):
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)

    page = dialog.page_widget("claude_api")
    assert page.isAncestorOf(dialog.claude_api_cb)
    assert dialog.claude_api_cb.isChecked() is False
    assert dialog.claude_api_cb.text() == "Enable Claude API"
    signin = page.findChild(QPushButton, "claude_api_signin_btn")
    assert signin is not None
    assert page.findChild(QPushButton, "claude_api_paste_cookie_btn") is None
    assert page.findChild(QPushButton, "claude_api_open_usage_btn") is not None

    with qtbot.waitSignal(dialog.sign_in_clicked) as signal:
        signin.click()
    assert signal.args == ["claude_api"]


def test_settings_adds_a_second_claude_api_account_with_its_own_sign_in(qtbot):
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)

    dialog._add_browser_account("claude_api")  # noqa: SLF001

    rows = [r for r in dialog._browser_account_rows if r.kind == "claude_api"]  # noqa: SLF001
    assert [r.account_id for r in rows][0] == "claude_api"
    assert len(rows) == 2
    assert rows[1].account_id.startswith("claude_api-")
    page = dialog.page_widget("claude_api")
    assert page.findChild(QPushButton, f"{rows[1].account_id}_signin_btn") is not None


# ----- Tile ------------------------------------------------------------------


def test_signed_out_claude_api_tile_shows_the_sign_in_button(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)

    widget.update_snapshot(
        UsageSnapshot(
            provider="claude_api",
            status=SnapshotStatus.AUTH_REQUIRED,
            error="Not signed in.",
        ),
        "Claude API",
    )

    tile = widget._tiles["claude_api"]  # noqa: SLF001
    assert tile.action_btn.text() == "Sign in"
    assert not tile.action_btn.isHidden()
    with qtbot.waitSignal(widget.sign_in_requested) as signal:
        tile.action_btn.click()
    assert signal.args == ["claude_api"]


def test_ok_claude_api_tile_draws_the_credit_bar_and_its_text_line(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)

    widget.update_snapshot(_credit_snapshot(), "Claude API")

    tile = widget._tiles["claude_api"]  # noqa: SLF001
    tile.set_expanded(True)
    rows = tile._rows  # noqa: SLF001
    assert [row.label.text() for row in rows][0] == "Credit"
    assert rows[0].pct.text() == "12%"
    assert not rows[0].pct.isHidden()
    text_lines = [row for row in rows[1:]]
    assert text_lines, "the Left line is drawn"
    assert text_lines[0].label.text().startswith("Left $87.60")
    assert text_lines[0].pct.isHidden()


def test_claude_tile_beside_claude_api_is_unchanged(qtbot):
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    fetched = datetime(2026, 4, 27, 12, 0)
    claude = UsageSnapshot(
        provider="claude",
        status=SnapshotStatus.OK,
        metrics=[
            UsageMetric("Session", 40.0, fetched + timedelta(hours=2)),
            UsageMetric("Weekly", 20.0, fetched + timedelta(days=5)),
        ],
        fetched_at=fetched,
    )
    widget.update_snapshot(claude, "Claude")
    before = [
        (row.label.text(), row.pct.text())
        for row in widget._tiles["claude"]._rows  # noqa: SLF001
    ]

    widget.update_snapshot(_credit_snapshot(), "Claude API")

    tile = widget._tiles["claude"]  # noqa: SLF001
    assert [(row.label.text(), row.pct.text()) for row in tile._rows] == before  # noqa: SLF001
    assert before == [("Session", "40%"), ("Weekly", "20%")]
    assert tile._supports_compact_collapse()  # noqa: SLF001


def test_claude_api_tiles_follow_claude_in_the_default_order(qtbot):
    config = Config()
    widget = UsageWidget(config)
    qtbot.addWidget(widget)
    for provider in ("codex", "claude_api", "claude"):
        widget.update_snapshot(_credit_snapshot(provider), provider)

    layout_ids = [
        widget._tile_layout.itemAt(i).widget().provider  # noqa: SLF001
        for i in range(widget._tile_layout.count())  # noqa: SLF001
    ]
    assert layout_ids == ["claude", "claude_api", "codex"]


# ----- Menu bar, app wiring and the snapshot pipeline -------------------------


def test_claude_api_is_labelled_ca_in_the_menu_bar():
    items = status_items(
        {"claude_api": _credit_snapshot()}, ("claude_api", "claude_api-work")
    )

    assert [label for label, _text, _color in items] == ["CA", "CA"]
    assert items[0][1] == "12%"
    assert items[1][1] == "..."


def test_details_menu_lists_claude_and_codex_but_not_claude_api():
    config = Config()
    config.browser_accounts.append(BrowserAccount(id="claude_api-x", kind="claude_api"))
    from PyQt6.QtWidgets import QMenu

    stub = SimpleNamespace(
        _config=config,
        _details_menu=QMenu(),
        open_ratio_history=lambda *args, **kwargs: None,
    )

    App._populate_details_menu(stub)  # noqa: SLF001

    assert [a.text() for a in stub._details_menu.actions()] == ["Claude", "Codex"]  # noqa: SLF001


def test_build_providers_creates_a_claude_api_provider_only_when_enabled():
    from aigauge.providers.claude_api import ClaudeApiProvider

    config = Config(
        browser_accounts=[
            BrowserAccount(id="claude_api", kind="claude_api"),
            BrowserAccount(id="claude_api-work", kind="claude_api", name="Work"),
        ]
    )
    config.providers.claude = False
    config.providers.codex = False
    config.providers.copilot = False
    ensured = []
    app = App.__new__(App)
    app._config = config  # noqa: SLF001
    app._providers = {}  # noqa: SLF001
    app._snapshots = {}  # noqa: SLF001
    app._widget = SimpleNamespace(  # noqa: SLF001
        _tiles={},
        ensure_tile=lambda account_id, name: ensured.append((account_id, name)),
        remove_tile=lambda account_id: None,
        apply_provider_order=lambda: None,
    )
    app._sync_usage_cache = lambda: None  # noqa: SLF001

    app._build_providers()  # noqa: SLF001
    assert app._providers == {}  # noqa: SLF001
    assert ensured == []

    config.providers.claude_api = True
    app._build_providers()  # noqa: SLF001

    assert list(app._providers) == ["claude_api", "claude_api-work"]  # noqa: SLF001
    assert all(
        isinstance(p, ClaudeApiProvider) for p in app._providers.values()  # noqa: SLF001
    )
    assert ensured == [
        ("claude_api", "Claude API"),
        ("claude_api-work", "Claude API (Work)"),
    ]


def test_claude_api_snapshot_goes_through_the_snapshot_pipeline(
    qtbot, tmp_path, monkeypatch, caplog
):
    from aigauge.history import HistoryStore
    from aigauge.ratio import RatioStore

    monkeypatch.setattr("aigauge.usage_cache.app_data_dir", lambda: tmp_path)
    config = Config(mcp_enabled=True)
    config.providers.claude_api = True
    widget = UsageWidget(config)
    qtbot.addWidget(widget)
    app = App.__new__(App)
    app._config = config  # noqa: SLF001
    app._cleared_sessions = set()  # noqa: SLF001
    app._snapshots = {}  # noqa: SLF001
    app._cycle_signatures = {}  # noqa: SLF001
    app._inflight = {"claude_api"}  # noqa: SLF001
    app._history = HistoryStore(base_dir=tmp_path)  # noqa: SLF001
    app._ratio = RatioStore(base_dir=tmp_path)  # noqa: SLF001
    app._local_usage = None  # noqa: SLF001
    app._widget = widget  # noqa: SLF001
    app._refresh_queue = []  # noqa: SLF001
    app._cycle_changed = lambda: False  # noqa: SLF001
    app._current_refresh_manual = True  # noqa: SLF001
    app._last_cycle_signatures = None  # noqa: SLF001
    app._update_tray = lambda: None  # noqa: SLF001
    app._schedule_next_refresh = lambda: None  # noqa: SLF001
    snapshot = _credit_snapshot()
    snapshot.fetched_at = datetime.now()

    with caplog.at_level("ERROR"):
        app._on_snapshot(snapshot)  # noqa: SLF001

    assert [r.getMessage() for r in caplog.records] == []
    assert app._snapshots["claude_api"] is snapshot  # noqa: SLF001
    assert "claude_api" in widget._tiles  # noqa: SLF001
    cached = read_usage_cache()["accounts"]["claude_api"]["metrics"]
    assert cached[0]["label"] == "Credit"
    assert cached[0]["guard_eligible"] is False
    assert app._ratio.history("claude_api") == []  # noqa: SLF001
