from aigauge.config import (
    BrowserAccount,
    ColorThresholds,
    Config,
    account_display_name,
    account_kind,
    app_data_dir,
    browser_account,
    browser_accounts,
    config_path,
    display_name_for_account,
    qt_scale_factor_env,
    webview_profile_dir,
)


def test_defaults():
    c = Config()
    assert c.active_refresh_interval_minutes == 5
    assert c.refresh_interval_minutes == 60
    assert c.providers.claude is True
    assert c.providers.codex is True
    assert c.browser_accounts[0].show_fable is True
    assert [a.id for a in c.browser_accounts] == [
        "claude",
        "codex",
        "opencode_go",
    ]
    assert [a.kind for a in c.browser_accounts] == [
        "claude",
        "codex",
        "opencode_go",
    ]
    assert c.browser_accounts[2].usage_url.startswith(
        "https://opencode.ai/workspace/"
    )
    assert c.providers.copilot is True
    assert c.providers.opencode_go is False
    assert c.start_at_login is False
    assert c.copilot.monthly_quota == 1500
    assert c.opencode_go.usage_url.startswith("https://opencode.ai/workspace/")
    assert c.collapsed_tiles == []
    assert c.window.always_on_top is True
    assert c.window.collapsed is False
    assert c.window.fade_when_inactive is False
    assert c.window.opacity == 0.8
    assert c.window.ui_scale == 1.0
    assert c.copilot.colors == ColorThresholds(
        green_max=59, yellow_max=79, orange_max=94
    )


def test_ui_scale_round_trips_and_maps_to_qt_factor():
    c = Config()
    # Default scale leaves Qt's own DPI handling untouched.
    assert qt_scale_factor_env(c) is None

    c.window.ui_scale = 1.5
    assert qt_scale_factor_env(c) == "1.5"
    c.window.ui_scale = 2.0
    assert qt_scale_factor_env(c) == "2"


def test_ui_scale_persists(tmp_path, monkeypatch):
    c = Config()
    c.window.ui_scale = 1.25
    c.save()
    assert Config.load().window.ui_scale == 1.25


def test_round_trip(tmp_path, monkeypatch):
    c = Config()
    c.active_refresh_interval_minutes = 2
    c.refresh_interval_minutes = 10
    c.start_at_login = True
    c.providers.codex = False
    c.browser_accounts[0].show_fable = False
    c.browser_accounts[1].enabled = False
    c.copilot.username = "octocat"
    c.copilot.billing_org = "my-org"
    c.copilot.monthly_quota = 1500
    c.window.x = 100
    c.window.y = 200
    c.providers.opencode_go = True
    browser_account(c, "opencode_go").usage_url = (
        "https://opencode.ai/workspace/test/go"
    )
    c.collapsed_tiles = ["claude"]
    c.save()

    loaded = Config.load()
    assert loaded.active_refresh_interval_minutes == 2
    assert loaded.refresh_interval_minutes == 10
    assert loaded.start_at_login is True
    assert loaded.providers.codex is False
    assert loaded.browser_accounts[1].enabled is False
    assert loaded.providers.claude is True
    assert loaded.browser_accounts[0].show_fable is False
    assert loaded.copilot.username == "octocat"
    assert loaded.copilot.billing_org == "my-org"
    assert loaded.copilot.monthly_quota == 1500
    assert loaded.window.x == 100
    assert loaded.window.y == 200
    assert loaded.providers.opencode_go is True
    assert (
        browser_account(loaded, "opencode_go").usage_url
        == "https://opencode.ai/workspace/test/go"
    )
    assert loaded.collapsed_tiles == ["claude"]

def test_load_missing_returns_defaults():
    c = Config.load()
    assert c.refresh_interval_minutes == 60


def test_paths_under_appdata(tmp_path):
    assert str(tmp_path) in str(app_data_dir())
    assert config_path() == app_data_dir() / "config.json"
    assert webview_profile_dir("claude") == app_data_dir() / "profiles" / "claude"


def test_load_corrupt_falls_back_to_defaults():
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text("{ not valid json", encoding="utf-8")
    c = Config.load()
    assert c.refresh_interval_minutes == 60


def test_load_migrates_old_refresh_interval_to_active_rate():
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(
        '{"refresh_interval_minutes": 5, "providers": {"claude": true, "codex": true, "copilot": true}}',
        encoding="utf-8",
    )
    c = Config.load()
    assert c.active_refresh_interval_minutes == 5
    assert c.refresh_interval_minutes == 60
    assert [(a.id, a.kind, a.enabled) for a in c.browser_accounts] == [
        ("claude", "claude", True),
        ("codex", "codex", True),
        ("opencode_go", "opencode_go", True),
    ]


def test_load_migrates_legacy_copilot_pro_request_quota_to_credits():
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(
        '{"copilot": {"monthly_quota": 300}}',
        encoding="utf-8",
    )

    c = Config.load()

    assert c.copilot.monthly_quota == 1500
    assert c.opencode_go.usage_url.startswith("https://opencode.ai/workspace/")
    assert c.collapsed_tiles == []

def test_load_migrates_legacy_provider_toggles_to_browser_accounts():
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(
        '{"providers": {"claude": false, "codex": true, "copilot": false}}',
        encoding="utf-8",
    )

    c = Config.load()

    assert [(a.id, a.kind, a.enabled) for a in c.browser_accounts] == [
        ("claude", "claude", False),
        ("codex", "codex", True),
        ("opencode_go", "opencode_go", True),
    ]


def test_upgraded_config_without_fable_key_shows_the_limit():
    """Configs written before the setting existed opt in to the gauge."""
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(
        '{"providers": {"claude": true},'
        ' "browser_accounts_version": 2,'
        ' "browser_accounts": ['
        '{"id": "claude", "kind": "claude"},'
        '{"id": "claude-work", "kind": "claude"}]}',
        encoding="utf-8",
    )

    c = Config.load()

    assert [a.show_fable for a in c.browser_accounts] == [True, True]


def test_explicitly_disabled_fable_account_stays_disabled():
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(
        '{"providers": {"claude": true},'
        ' "browser_accounts_version": 2,'
        ' "browser_accounts": ['
        '{"id": "claude", "kind": "claude", "show_fable": false},'
        '{"id": "claude-work", "kind": "claude"}]}',
        encoding="utf-8",
    )

    c = Config.load()

    assert [a.show_fable for a in c.browser_accounts] == [False, True]


def test_load_adds_opencode_without_restoring_removed_claude_or_codex():
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text('{"browser_accounts": []}', encoding="utf-8")

    c = Config.load()

    assert [(a.id, a.kind) for a in c.browser_accounts] == [
        ("opencode_go", "opencode_go")
    ]


def test_load_preserves_explicitly_removed_accounts_after_v2_migration():
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(
        '{"browser_accounts": [], "browser_accounts_version": 2}',
        encoding="utf-8",
    )

    c = Config.load()

    assert c.browser_accounts == []


def test_load_migrates_single_opencode_settings_to_account():
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(
        '{"browser_accounts": [], "opencode_go": {'
        '"usage_url": "https://opencode.ai/workspace/legacy/go", '
        '"colors": {"green_max": 20, "yellow_max": 50, "orange_max": 80}}}',
        encoding="utf-8",
    )

    c = Config.load()
    account = browser_account(c, "opencode_go")

    assert account is not None
    assert account.usage_url == "https://opencode.ai/workspace/legacy/go"
    assert account.colors.green_max == 20
    assert c.browser_accounts_version == 2


def test_browser_account_display_names():
    account = BrowserAccount(id="codex-work", kind="codex", name="Work")

    assert account_display_name(account) == "Codex (Work)"


def test_display_name_for_configured_account():
    c = Config()
    c.browser_accounts.append(
        BrowserAccount(id="claude-team", kind="claude", name="Team")
    )

    assert display_name_for_account(c, "claude-team") == "Claude (Team)"
    assert [a.id for a in browser_accounts(c, kind="claude")] == [
        "claude",
        "claude-team",
    ]


def test_secondary_opencode_account_resolves_kind_and_display_name():
    c = Config()
    c.browser_accounts.append(
        BrowserAccount(
            id="opencode_go-work",
            kind="opencode_go",
            name="Work",
            usage_url="https://opencode.ai/workspace/work/go",
        )
    )

    assert account_kind(c, "opencode_go-work") == "opencode_go"
    assert display_name_for_account(c, "opencode_go-work") == "OpenCode (Work)"


def test_load_migrates_start_with_windows_to_start_at_login():
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(
        '{"start_with_windows": true}',
        encoding="utf-8",
    )
    c = Config.load()
    assert c.start_at_login is True


def test_load_clamps_saved_window_size():
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(
        '{"window": {"width": 5000, "height": 2}}',
        encoding="utf-8",
    )

    c = Config.load()

    assert c.window.width == 900
    assert c.window.height == 80
