from aigauge.config import (
    BrowserAccount,
    Config,
    account_display_name,
    app_data_dir,
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
    assert [a.id for a in c.browser_accounts] == ["claude", "codex"]
    assert [a.kind for a in c.browser_accounts] == ["claude", "codex"]
    assert c.providers.copilot is True
    assert c.start_at_login is False
    assert c.copilot.monthly_quota == 1500
    assert c.window.always_on_top is True
    assert c.window.collapsed is False
    assert c.window.ui_scale == 1.0


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
    c.browser_accounts[1].enabled = False
    c.copilot.username = "octocat"
    c.copilot.billing_org = "my-org"
    c.copilot.monthly_quota = 1500
    c.window.x = 100
    c.window.y = 200
    c.save()

    loaded = Config.load()
    assert loaded.active_refresh_interval_minutes == 2
    assert loaded.refresh_interval_minutes == 10
    assert loaded.start_at_login is True
    assert loaded.providers.codex is False
    assert loaded.browser_accounts[1].enabled is False
    assert loaded.providers.claude is True
    assert loaded.copilot.username == "octocat"
    assert loaded.copilot.billing_org == "my-org"
    assert loaded.copilot.monthly_quota == 1500
    assert loaded.window.x == 100
    assert loaded.window.y == 200


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
    ]


def test_load_migrates_legacy_copilot_pro_request_quota_to_credits():
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(
        '{"copilot": {"monthly_quota": 300}}',
        encoding="utf-8",
    )

    c = Config.load()

    assert c.copilot.monthly_quota == 1500


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
    ]


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

    assert c.window.width == 340
    assert c.window.height == 80
