import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QFileDialog

from aigauge import app as app_module
from aigauge import config as config_module
from aigauge import settings_dialog as settings_module
from aigauge.app import App, _enabled_providers, _preserve_error_metrics
from aigauge.config import ColorThresholds, Config
from aigauge.gauge import thresholds_for_provider
from aigauge.mcp_server import _configured_account_ids
from aigauge.menubar import PROVIDER_LABELS
from aigauge.models import SnapshotStatus, UsageMetric, UsageSnapshot
from aigauge.providers import antigravity
from aigauge.settings_dialog import SettingsDialog
from aigauge.widget import PROVIDER_ORDER, UsageWidget, _short_error_reason


def test_antigravity_config_defaults_and_legacy_json(tmp_path, monkeypatch):
    config = Config()
    assert config.providers.antigravity is False
    assert config.antigravity.colors == ColorThresholds()
    assert config.antigravity.cli_path is None
    assert config.antigravity.show_gemini is True
    assert config.antigravity.show_third_party is True
    config_file = tmp_path / "config.json"
    config_file.write_text('{"providers": {"claude": false}}', encoding="utf-8")
    monkeypatch.setattr(config_module, "config_path", lambda: config_file)
    legacy = Config.load()
    assert legacy.providers.antigravity is False
    assert legacy.antigravity.cli_path is None


def test_antigravity_threshold_label_and_order():
    config = Config()
    colors = ColorThresholds(green_color="#123456")
    config.antigravity.colors = colors
    assert thresholds_for_provider(config, "antigravity") == colors
    assert PROVIDER_LABELS["antigravity"] == "Ag"
    assert PROVIDER_ORDER.index("antigravity") == PROVIDER_ORDER.index("openrouter") + 1


def test_mcp_account_ids_follow_antigravity_toggle():
    config = Config()
    config.providers.antigravity = True
    assert "antigravity" in _configured_account_ids(config)
    config.providers.antigravity = False
    assert "antigravity" not in _configured_account_ids(config)


def test_app_builds_antigravity_provider_with_config_and_tile(monkeypatch):
    config = Config()
    config.providers.claude = False
    config.providers.codex = False
    config.providers.copilot = False
    config.providers.antigravity = True
    config.antigravity.cli_path = "C:/tools/agy.exe"
    config.antigravity.show_gemini = False
    config.antigravity.show_third_party = True
    created = []
    ensured = []

    def fake_provider(**kwargs):
        created.append(kwargs)
        return object()

    monkeypatch.setattr(
        "aigauge.providers.antigravity.AntigravityProvider", fake_provider
    )
    app = App.__new__(App)
    app._config = config
    app._providers = {}
    app._snapshots = {}
    app._widget = SimpleNamespace(
        _tiles={},
        ensure_tile=lambda account_id, label: ensured.append((account_id, label)),
        remove_tile=lambda account_id: None,
        apply_provider_order=lambda: None,
    )

    assert "antigravity" in _enabled_providers(config)
    app._build_providers()

    assert created == [{
        "cli_path": "C:/tools/agy.exe",
        "show_gemini": False,
        "show_third_party": True,
    }]
    assert "antigravity" in app._providers
    assert ensured == [("antigravity", "Antigravity")]


def test_settings_dialog_shows_and_saves_antigravity_options(qtbot, monkeypatch):
    monkeypatch.setattr("aigauge.settings_dialog.resolve_cli", lambda _: "agy-test")
    monkeypatch.setattr("aigauge.settings_dialog.set_start_at_login", lambda _: None)
    config = Config()
    monkeypatch.setattr(Config, "save", lambda self: None)
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)

    assert dialog.antigravity_cli_path_edit.text() == ""
    assert dialog.antigravity_cli_path_edit.placeholderText() == (
        "Auto-detect (agy-test)"
    )
    assert dialog.antigravity_cli_path_status.text() == (
        "Using auto-detected: agy-test"
    )
    dialog.antigravity_show_gemini_cb.setChecked(False)
    dialog.apply_to(config)

    assert config.antigravity.show_gemini is False
    assert config.antigravity.cli_path is None
    dialog.show_provider("antigravity")
    assert dialog.current_page_key() == "antigravity"
    # The page sits after OpenRouter and before the TOOLS caption.
    keys = dialog.page_keys()
    index = keys.index("antigravity")
    assert keys[index - 1] == "openrouter"
    assert keys[index + 1] == "mcp"


def _dialog(qtbot, monkeypatch, config=None, autodetected=None):
    lookups = []

    def fake_resolve(path):
        lookups.append(path)
        return autodetected

    monkeypatch.setattr("aigauge.settings_dialog.resolve_cli", fake_resolve)
    monkeypatch.setattr("aigauge.settings_dialog.set_start_at_login", lambda _: None)
    monkeypatch.setattr(Config, "save", lambda self: None)
    dialog = SettingsDialog(config or Config())
    qtbot.addWidget(dialog)
    dialog.agy_lookups = lookups
    return dialog


def _count_file_checks(monkeypatch) -> list[str]:
    checked: list[str] = []

    class CountingPath(type(Path())):
        def is_file(self, *args, **kwargs):
            checked.append(str(self))
            return super().is_file(*args, **kwargs)

    monkeypatch.setattr(settings_module, "Path", CountingPath)
    return checked


def test_settings_path_field_loads_saves_normalized_path_and_empty_as_none(
    qtbot, monkeypatch, tmp_path
):
    path = tmp_path / "agy.exe"
    config = Config()
    config.antigravity.cli_path = f'  "{path}"  '
    dialog = _dialog(qtbot, monkeypatch, config)
    assert dialog.antigravity_cli_path_edit.text() == f'  "{path}"  '
    dialog.apply_to(config)
    assert config.antigravity.cli_path == str(path)
    dialog.antigravity_cli_path_edit.clear()
    dialog.apply_to(config)
    assert config.antigravity.cli_path is None


@pytest.mark.parametrize(
    ("configured", "detected", "exists", "expected"),
    [
        ("agy.exe", None, True, "Using: {path}"),
        ("agy.exe", None, False, "Not found: {path}"),
        ("notepad.exe", None, True, "File is not named agy: {path}"),
        ("", "auto", True, "Using auto-detected: {path}"),
        (
            "", None, False,
            "agy CLI not found. Install the Antigravity CLI or set its path.",
        ),
    ],
)
def test_settings_path_status_when_the_dialog_opens(
    qtbot, monkeypatch, tmp_path, configured, detected, exists, expected
):
    custom = tmp_path / (configured or "unused")
    auto = tmp_path / "auto" / "agy.exe"
    if exists and configured:
        custom.touch()
    config = Config()
    config.antigravity.cli_path = str(custom) if configured else None
    dialog = _dialog(
        qtbot, monkeypatch, config, autodetected=str(auto) if detected else None
    )
    path = custom if configured else auto
    assert dialog.antigravity_cli_path_status.text() == expected.format(path=path)


def test_settings_checks_the_path_when_editing_ends_not_per_keystroke(
    qtbot, monkeypatch, tmp_path
):
    target = tmp_path / "agy.exe"
    target.touch()
    dialog = _dialog(qtbot, monkeypatch, autodetected="agy-auto")
    assert dialog.agy_lookups == [None]  # once, when the dialog opened
    checked = _count_file_checks(monkeypatch)
    edit = dialog.antigravity_cli_path_edit

    qtbot.keyClicks(edit, str(target))

    assert edit.text() == str(target)
    assert checked == []
    assert dialog.agy_lookups == [None]
    assert dialog.antigravity_cli_path_status.text() == (
        "The path is checked when you leave the field."
    )

    edit.editingFinished.emit()

    assert checked == [str(target)]
    assert dialog.antigravity_cli_path_status.text() == f"Using: {target}"

    edit.clear()  # programmatic, like the clear in apply tests: no check
    edit.editingFinished.emit()

    assert checked == [str(target)]
    assert dialog.agy_lookups == [None]
    assert dialog.antigravity_cli_path_status.text() == "Using auto-detected: agy-auto"


@pytest.mark.parametrize(
    ("name", "expected"),
    [("agy.exe", "Using: {path}"), ("notepad.exe", "File is not named agy: {path}")],
)
def test_settings_browse_fills_and_checks_the_path(
    qtbot, monkeypatch, tmp_path, name, expected
):
    dialog = _dialog(qtbot, monkeypatch)
    selected = tmp_path / name
    selected.touch()
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda *args: (selected.as_posix(), "")
    )
    checked = _count_file_checks(monkeypatch)

    dialog.antigravity_cli_browse_button.click()

    assert dialog.antigravity_cli_path_edit.text() == str(selected)
    assert checked == [str(selected)]
    assert dialog.antigravity_cli_path_status.text() == expected.format(path=selected)

    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: ("", ""))
    dialog.antigravity_cli_browse_button.click()

    assert dialog.antigravity_cli_path_edit.text() == str(selected)
    assert checked == [str(selected)]


def test_settings_hint_calls_the_name_check_a_guard_not_verification(
    qtbot, monkeypatch
):
    from PyQt6.QtWidgets import QLabel

    dialog = _dialog(qtbot, monkeypatch)
    hints = [
        label.text()
        for label in dialog.findChildren(QLabel)
        if "agy --print /quota" in label.text()
    ]
    assert len(hints) == 1
    assert "catches a mistyped or wrong file" in hints[0]
    assert "does not check what the program is" in hints[0]


def test_disabled_antigravity_builds_no_provider_and_never_touches_agy(monkeypatch):
    config = Config()
    assert config.providers.antigravity is False
    monkeypatch.setattr(
        antigravity.subprocess, "Popen", lambda *a, **k: pytest.fail("spawned")
    )
    monkeypatch.setattr(
        antigravity.subprocess, "run", lambda *a, **k: pytest.fail("spawned")
    )
    monkeypatch.setattr(
        antigravity.shutil, "which", lambda *a, **k: pytest.fail("looked up agy")
    )
    monkeypatch.setattr(
        "aigauge.providers.antigravity.AntigravityProvider",
        lambda **kwargs: pytest.fail("provider built while disabled"),
    )
    for target in (
        "aigauge.providers.claude.ClaudeProvider",
        "aigauge.providers.codex.CodexProvider",
        "aigauge.providers.opencode_go.OpenCodeGoProvider",
        "aigauge.providers.copilot.CopilotProvider",
        "aigauge.providers.openrouter.OpenRouterProvider",
    ):
        monkeypatch.setattr(target, lambda *a, **k: object())
    app = App.__new__(App)
    app._config = config
    app._providers = {}
    app._snapshots = {}
    app._widget = SimpleNamespace(
        _tiles={},
        ensure_tile=lambda *a: None,
        remove_tile=lambda *a: None,
        apply_provider_order=lambda: None,
    )
    app._sync_usage_cache = lambda: None

    app._build_providers()

    assert "antigravity" not in app._providers
    assert "antigravity" not in _enabled_providers(config)
    assert "antigravity" not in _configured_account_ids(config)


def _good_snapshot() -> UsageSnapshot:
    return UsageSnapshot(
        provider="antigravity",
        status=SnapshotStatus.OK,
        metrics=[UsageMetric("Gemini weekly", 42.0, None)],
    )


def _refresh_once(monkeypatch, provider) -> UsageSnapshot:
    monkeypatch.setattr(provider, "_run_async", lambda work, on_done: on_done(work()))
    snapshots = []
    provider.refresh(snapshots.append)
    return snapshots[0]


@pytest.mark.parametrize("file_name", [None, "notepad.exe"])
def test_mis_set_cli_path_does_not_keep_showing_stale_numbers(
    qtbot, monkeypatch, tmp_path, file_name
):
    # None: the configured file does not exist. Otherwise: it exists under
    # another name.
    path = tmp_path / (file_name or "agy.exe")
    if file_name:
        path.touch()
    monkeypatch.setattr(
        antigravity.subprocess, "Popen", lambda *a, **k: pytest.fail("must not run")
    )
    error = _refresh_once(
        monkeypatch, antigravity.AntigravityProvider(cli_path=str(path))
    )

    merged = _preserve_error_metrics(error, _good_snapshot())

    assert merged.status is SnapshotStatus.ERROR
    assert merged.metrics == []
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    tile = widget.ensure_tile("antigravity", "Antigravity")
    widget.update_snapshot(_good_snapshot(), "Antigravity")
    widget.update_snapshot(merged, "Antigravity")
    assert tile._rows == []  # noqa: SLF001
    assert "stale" not in tile.status.text()
    assert str(path) in tile.status.toolTip()


def test_transient_antigravity_errors_still_keep_the_last_numbers(monkeypatch):
    provider = antigravity.AntigravityProvider()
    monkeypatch.setattr(antigravity, "resolve_cli", lambda _: None)
    not_installed = _refresh_once(monkeypatch, provider)
    assert not_installed.error == "agy CLI not found"

    for error in (
        not_installed,
        UsageSnapshot(
            provider="antigravity",
            status=SnapshotStatus.ERROR,
            error=antigravity._TIMEOUT_ERROR,
        ),
    ):
        merged = _preserve_error_metrics(error, _good_snapshot())
        assert [m.label for m in merged.metrics] == ["Gemini weekly"]


@pytest.mark.parametrize(
    "error",
    [
        "agy CLI not found",
        # The quoted path must not be read as an "api" or "auth" failure.
        "agy CLI not found at C:/tools/api/auth/agy.exe",
    ],
)
def test_agy_not_found_has_a_tile_label(qtbot, error):
    assert _short_error_reason(error) == "error \u00b7 agy not found"
    widget = UsageWidget(Config())
    qtbot.addWidget(widget)
    tile = widget.ensure_tile("antigravity", "Antigravity")
    widget.update_snapshot(
        UsageSnapshot(provider="antigravity", status=SnapshotStatus.ERROR, error=error),
        "Antigravity",
    )
    assert "error \u00b7 agy not found" in tile.status.text()
    assert tile.status.toolTip().startswith(error)


FIXTURE = Path(__file__).parent / "fixtures" / "antigravity" / "quota_ok.json"


def _fixture_snapshot(*, weekly_only: bool = False, groups: int = 2) -> UsageSnapshot:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data = payload["command"]["data"]
    data["groups"] = data["groups"][:groups]
    if weekly_only:
        for group in data["groups"]:
            group["buckets"] = [b for b in group["buckets"] if b["window"] != "5h"]
    return antigravity._build_snapshot(payload)


def _antigravity_widget(qtbot, snapshot: UsageSnapshot) -> UsageWidget:
    config = Config()
    config.providers.claude = config.providers.codex = False
    config.providers.copilot = False
    config.providers.antigravity = True
    widget = UsageWidget(config)
    qtbot.addWidget(widget)
    widget.ensure_tile("antigravity", "Antigravity")
    widget.update_snapshot(snapshot, "Antigravity")
    widget.show()
    widget._do_refit_height()  # noqa: SLF001
    return widget


def _chip_texts(widget: UsageWidget) -> list[str]:
    layout = widget._collapsed_summary_layout  # noqa: SLF001
    texts = []
    for index in range(layout.count()):
        row = layout.itemAt(index).widget()
        if row is None or row.layout() is None:
            continue
        for position in range(row.layout().count()):
            chip = row.layout().itemAt(position).widget()
            if chip is not None:
                texts.append(chip.text())
    return texts


@pytest.mark.parametrize(
    ("weekly_only", "groups", "rows", "chip"),
    [
        (
            False, 2,
            [
                ("Gemini 5h", "15%"),
                ("Gemini weekly", "13%"),
                ("Claude+GPT 5h", "0%"),
                ("Claude+GPT weekly", "89%"),
            ],
            "Antigravity 15%",
        ),
        # A free account: a weekly quota and no 5-hour window.
        (
            True, 2,
            [("Gemini weekly", "13%"), ("Claude+GPT weekly", "89%")],
            "Antigravity 13%",
        ),
        (True, 1, [("Gemini weekly", "13%")], "Antigravity 13%"),
    ],
)
def test_tile_renders_every_reply_shape_like_any_other_provider(
    qtbot, weekly_only, groups, rows, chip
):
    widget = _antigravity_widget(
        qtbot, _fixture_snapshot(weekly_only=weekly_only, groups=groups)
    )
    tile = widget._tiles["antigravity"]  # noqa: SLF001

    assert tile.status.text() == ""
    assert [(row.label.text(), row.pct.text()) for row in tile._rows] == rows  # noqa: SLF001
    # Plain rows, as for Copilot: no compact form and nothing to expand.
    assert tile._compact_summary.isHidden()  # noqa: SLF001
    assert tile.expand_btn.isHidden()
    previous_bottom = -1
    for row in tile._rows:  # noqa: SLF001
        assert not row.bar.isHidden()
        geometry = row.geometry()
        assert geometry.top() > previous_bottom
        assert geometry.left() >= 0 and geometry.right() < tile.width()
        assert row.label.width() >= row.label.fontMetrics().horizontalAdvance(
            row.label.text()
        )
        previous_bottom = geometry.bottom()
    assert tile.geometry().bottom() < widget.height()

    # The collapsed pill shows one chip per provider, from the first metric.
    widget.set_collapsed(True)
    assert _chip_texts(widget) == [chip]
