import logging
import shutil
from pathlib import Path

import pytest

from aigauge.config import BrowserAccount, Config
from aigauge.local_usage import service as service_module
from aigauge.local_usage.importer import ProviderImportResult
from aigauge.local_usage.service import (
    LocalUsageService,
    assigned_account,
    comparison_period_id,
    provider_for_account,
)

FIXTURES = Path(__file__).parent / "fixtures" / "local_usage"


@pytest.fixture
def tracked_config(tmp_path):
    claude = tmp_path / "logs" / "claude" / "projects"
    codex = tmp_path / "logs" / "codex"
    shutil.copytree(FIXTURES / "claude" / "projects", claude)
    shutil.copytree(FIXTURES / "codex" / "sessions", codex / "sessions")
    config = Config()
    config.local_usage.enabled = True
    config.local_usage.claude.account_id = "claude"
    config.local_usage.claude.log_root = str(claude)
    config.local_usage.codex.account_id = "codex"
    config.local_usage.codex.log_root = str(codex)
    return config


def test_assignment_requires_tracking_on_and_a_matching_account(tracked_config):
    assert assigned_account(tracked_config, "claude") == "claude"
    assert provider_for_account(tracked_config, "codex") == "codex"

    tracked_config.local_usage.enabled = False
    assert assigned_account(tracked_config, "claude") is None

    tracked_config.local_usage.enabled = True
    tracked_config.local_usage.claude.account_id = "codex"  # wrong kind
    assert assigned_account(tracked_config, "claude") is None


def test_removing_the_assigned_account_pauses_instead_of_reassigning(tracked_config):
    tracked_config.browser_accounts.append(BrowserAccount(id="claude-2", kind="claude"))
    tracked_config.browser_accounts = [
        a for a in tracked_config.browser_accounts if a.id != "claude"
    ]

    assert assigned_account(tracked_config, "claude") is None
    service = LocalUsageService(tracked_config)
    try:
        assert [s.provider for s in service.sources()] == ["codex"]
    finally:
        service.shutdown()


def test_renaming_or_reordering_accounts_keeps_the_assignment(tracked_config):
    tracked_config.browser_accounts.reverse()
    for account in tracked_config.browser_accounts:
        account.name = "Renamed"

    assert assigned_account(tracked_config, "claude") == "claude"


def test_reassigning_or_moving_folder_starts_new_comparison_period(tracked_config):
    before = comparison_period_id(tracked_config, "claude")
    tracked_config.local_usage.claude.log_root = "elsewhere"
    moved = comparison_period_id(tracked_config, "claude")
    tracked_config.local_usage.claude.account_id = "claude-2"

    assert len({before, moved, comparison_period_id(tracked_config, "claude")}) == 3


def test_run_sync_imports_assigned_providers(tracked_config, tmp_path):
    service = LocalUsageService(tracked_config, base_dir=tmp_path / "data")
    try:
        results = service.run_sync()
        assert {r.provider for r in results} == {"claude", "codex"}
        assert service.store.row_count("claude") == 4
        assert service.store.row_count("codex") > 0
    finally:
        service.shutdown()


def test_clear_data_deletes_only_the_database(tracked_config, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "history.jsonl").write_text("{}\n", encoding="utf-8")
    service = LocalUsageService(tracked_config, base_dir=data)
    service.run_sync()
    log_file = Path(tracked_config.local_usage.claude.log_root) / "c--proj" / "sess-a.jsonl"

    service.clear_data()

    assert not (data / "local_usage.sqlite").exists()
    assert (data / "history.jsonl").exists()
    assert log_file.exists()
    assert service.store.row_count("claude") == 0  # store reopens empty
    service.shutdown()


def test_start_from_now_then_import_history_clears_it(tracked_config, tmp_path, monkeypatch):
    service = LocalUsageService(tracked_config, base_dir=tmp_path / "data")
    requested = []
    monkeypatch.setattr(service, "request_import", lambda kind="incremental": requested.append(kind))
    try:
        service.start_from_now()
        assert tracked_config.local_usage.claude.start_from is not None

        service.import_history()

        assert tracked_config.local_usage.claude.start_from is None
        assert requested == [service_module.BACKFILL]
    finally:
        service.shutdown()


def test_background_import_emits_finished(tracked_config, tmp_path, qtbot):
    service = LocalUsageService(tracked_config, base_dir=tmp_path / "data")
    try:
        with qtbot.waitSignal(service.import_finished, timeout=10000) as signal:
            assert service.request_import()
        assert {r.provider for r in signal.args[0]} == {"claude", "codex"}
    finally:
        service.shutdown()


def test_request_import_does_nothing_without_tracked_providers(tmp_path):
    service = LocalUsageService(Config(), base_dir=tmp_path / "data")
    try:
        assert service.request_import() is False
        assert not service.is_running()
    finally:
        service.shutdown()


def _finished_records(caplog):
    return [r for r in caplog.records if "import finished" in r.getMessage()]


def test_import_that_parsed_nothing_is_logged_at_debug(tracked_config, tmp_path, caplog):
    service = LocalUsageService(tracked_config, base_dir=tmp_path / "data")
    try:
        with caplog.at_level(logging.DEBUG, logger=service_module.log.name):
            service.run_sync()  # imports the fixtures
            service.run_sync()  # nothing new
        first, second = _finished_records(caplog)
        assert first.levelno == logging.INFO
        assert "parsed=0" not in first.getMessage()
        assert second.levelno == logging.DEBUG
        assert "parsed=0" in second.getMessage()
    finally:
        service.shutdown()


def test_import_that_was_cancelled_or_errored_stays_at_info(
    tracked_config, tmp_path, caplog, monkeypatch
):
    service = LocalUsageService(tracked_config, base_dir=tmp_path / "data")
    try:
        for result in (
            ProviderImportResult("claude", cancelled=True),
            ProviderImportResult("claude", errors=1),
        ):
            monkeypatch.setattr(
                service_module.LogImporter, "run", lambda self, sources, r=result: [r]
            )
            caplog.clear()
            with caplog.at_level(logging.DEBUG, logger=service_module.log.name):
                service.run_sync()
            (record,) = _finished_records(caplog)
            assert record.levelno == logging.INFO
    finally:
        service.shutdown()
