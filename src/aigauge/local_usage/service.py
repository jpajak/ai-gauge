"""Runs local usage imports in the background for the app.

Created only while local usage tracking is on. Imports run in one worker
thread, one at a time; a request that arrives while one is running is folded
into a single follow-up run. The gauge and quota refreshes never wait on it.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from ..config import Config, app_data_dir
from ..history import PeriodRecord
from . import summaries
from .assignment import (
    assigned_account,
    comparison_period_id,
    provider_config,
    provider_for_account,
)
from .importer import ImportSource, LogImporter, ProviderImportResult, provider_roots
from .store import CLAUDE, CODEX, DB_FILENAME, PROVIDERS, UsageStore, from_db_time

log = logging.getLogger("aigauge.local_usage.service")

INCREMENTAL = "incremental"
BACKFILL = "backfill"
# The details dialog triggers an import when it opens, at most this often.
DIALOG_IMPORT_MIN_INTERVAL_S = 60.0
_PROGRESS_EMIT_INTERVAL_S = 0.1


def database_path(base_dir: Path | None = None) -> Path:
    return (base_dir or app_data_dir()) / DB_FILENAME


class LocalUsageService(QObject):
    progress_changed = pyqtSignal(int, int)  # bytes done, bytes total
    import_started = pyqtSignal(str)  # INCREMENTAL or BACKFILL
    import_finished = pyqtSignal(object)  # list[ProviderImportResult]

    def __init__(self, config: Config, base_dir: Path | None = None, parent=None):
        super().__init__(parent)
        self._config = config
        self._path = database_path(base_dir)
        self._store: UsageStore | None = UsageStore(self._path)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._pending: str | None = None
        self._running_kind: str | None = None
        self._progress = (0, 0)
        self._last_progress_emit = 0.0
        self._last_import_started: float | None = None
        self._last_results: list[ProviderImportResult] = []
        # Claude periods from AI Gauge's own readings, copied from the UI
        # thread and consumed by the worker when it updates window summaries.
        self._claude_current: dict[str, list[PeriodRecord]] = {}
        self._claude_closed: list[PeriodRecord] = []
        # Returns closed periods from history.jsonl, for Claude backfill.
        self.history_records: Callable[[], list[PeriodRecord]] | None = None
        # Called in the worker after each import, before import_finished.
        self.after_import: list[Callable[[UsageStore], None]] = [self._update_summaries]

    # ---- state ----

    @property
    def config(self) -> Config:
        return self._config

    @property
    def store(self) -> UsageStore:
        if self._store is None:
            self._store = UsageStore(self._path)
        return self._store

    @property
    def database_path(self) -> Path:
        return self._path

    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def running_kind(self) -> str | None:
        return self._running_kind if self.is_running() else None

    def progress(self) -> tuple[int, int]:
        return self._progress

    def last_results(self) -> list[ProviderImportResult]:
        return list(self._last_results)

    def assigned_account(self, provider: str) -> str | None:
        return assigned_account(self._config, provider)

    def provider_for_account(self, account_id: str) -> str | None:
        return provider_for_account(self._config, account_id)

    def sources(self) -> list[ImportSource]:
        out = []
        for provider in PROVIDERS:
            if self.assigned_account(provider) is None:
                continue
            settings = provider_config(self._config, provider)
            out.append(
                ImportSource(
                    provider,
                    provider_roots(provider, settings.log_root),
                    from_db_time(settings.start_from),
                )
            )
        return out

    def note_claude_periods(
        self,
        account_id: str,
        current: list[PeriodRecord],
        closed: list[PeriodRecord],
    ) -> None:
        """Record the latest Claude readings for the next summary update."""
        with self._lock:
            self._claude_current[account_id] = [replace(r) for r in current]
            self._claude_closed.extend(replace(r) for r in closed)

    # ---- requests ----

    def request_import(self, kind: str = INCREMENTAL) -> bool:
        """Start an import, or queue one behind the running import."""
        if not self.sources():
            return False
        with self._lock:
            if self.is_running():
                if self._pending != BACKFILL:
                    self._pending = kind
                return True
            self._start_locked(kind)
        return True

    def maybe_import(self, min_interval_s: float = DIALOG_IMPORT_MIN_INTERVAL_S) -> bool:
        last = self._last_import_started
        if last is not None and time.monotonic() - last < min_interval_s:
            return False
        return self.request_import(INCREMENTAL)

    def import_history(self, providers: tuple[str, ...] = PROVIDERS) -> bool:
        """Import everything the logs still hold, including before "Start from now"."""
        for provider in providers:
            provider_config(self._config, provider).start_from = None
        try:
            self._config.save()
        except OSError:
            log.exception("failed to save config after clearing start_from")
        self.cancel(wait=True)
        for provider in providers:
            self.store.reset_progress(provider)
        return self.request_import(BACKFILL)

    def start_from_now(self, providers: tuple[str, ...] = PROVIDERS) -> None:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for provider in providers:
            settings = provider_config(self._config, provider)
            if settings.start_from is None:
                settings.start_from = stamp
        try:
            self._config.save()
        except OSError:
            log.exception("failed to save config after setting start_from")

    def cancel(self, wait: bool = False) -> None:
        with self._lock:
            self._pending = None
            thread = self._thread
        if thread is not None and thread.is_alive():
            self._cancel.set()
            if wait:
                thread.join()

    def shutdown(self) -> None:
        self.cancel(wait=True)
        if self._store is not None:
            self._store.close()
            self._store = None

    def clear_data(self) -> None:
        """Delete the local usage database. CLI logs and other files are untouched."""
        self.shutdown()
        for suffix in ("", "-wal", "-shm"):
            path = self._path.with_name(self._path.name + suffix)
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        self._last_results = []
        self._progress = (0, 0)

    def run_sync(self, kind: str = INCREMENTAL) -> list[ProviderImportResult]:
        """Run one import in the calling thread (tests and diagnostics)."""
        self._run(kind)
        return self._last_results

    # ---- worker ----

    def _start_locked(self, kind: str) -> None:
        self._cancel.clear()
        self._running_kind = kind
        self._last_import_started = time.monotonic()
        self._thread = threading.Thread(
            target=self._worker, args=(kind,), name="local-usage-import", daemon=True
        )
        self._thread.start()
        self.import_started.emit(kind)

    def _worker(self, kind: str) -> None:
        self._run(kind)
        with self._lock:
            follow_up = self._pending
            self._pending = None
            if follow_up is not None and not self._cancel.is_set():
                # Start the follow-up from this thread; is_running() stays true
                # for the caller because the new thread is set under the lock.
                self._start_locked(follow_up)
            else:
                self._running_kind = None

    def _update_summaries(self, store: UsageStore) -> None:
        account = assigned_account(self._config, CLAUDE)
        with self._lock:
            current = list(self._claude_current.get(account, [])) if account else []
            closed = [r for r in self._claude_closed if r.provider == account]
            self._claude_closed = []
        history = []
        if account is not None and self.history_records is not None:
            try:
                history = self.history_records()
            except Exception:  # noqa: BLE001
                log.exception("could not read usage history for backfill")
        summaries.update_all(
            store,
            self._config,
            claude_current=current,
            claude_closed=closed,
            history=history,
        )

    def _on_progress(self, done: int, total: int) -> None:
        self._progress = (done, total)
        now = time.monotonic()
        if done >= total or now - self._last_progress_emit >= _PROGRESS_EMIT_INTERVAL_S:
            self._last_progress_emit = now
            self.progress_changed.emit(done, total)

    def _run(self, kind: str) -> None:
        store = self.store
        results: list[ProviderImportResult] = []
        failed = False
        try:
            importer = LogImporter(store, cancel=self._cancel, progress=self._on_progress)
            results = importer.run(self.sources())
            for hook in list(self.after_import):
                try:
                    hook(store)
                except Exception:  # noqa: BLE001
                    log.exception("local usage after-import hook failed")
            # Summaries first: they copy the usage of windows about to be pruned.
            store.prune()
        except Exception:  # noqa: BLE001
            failed = True
            log.exception("local usage %s import failed", kind)
        finally:
            if threading.current_thread() is not threading.main_thread():
                store.close_thread_connection()
        self._last_results = results
        # An import that found nothing new is the common case on every check:
        # keep it out of the INFO log. Parsed rows, a cancel, an error or a
        # failed run stay visible.
        quiet = not failed and all(
            r.parsed == 0 and not r.cancelled and not r.errors for r in results
        )
        log.log(
            logging.DEBUG if quiet else logging.INFO,
            "local usage %s import finished %s",
            kind,
            ", ".join(
                f"{r.provider}: files={r.files_read}/{r.files_seen} bytes={r.bytes_read} "
                f"parsed={r.parsed} cancelled={r.cancelled}"
                for r in results
            ),
        )
        self.import_finished.emit(results)


__all__ = [
    "BACKFILL",
    "CLAUDE",
    "CODEX",
    "INCREMENTAL",
    "LocalUsageService",
    "assigned_account",
    "comparison_period_id",
    "provider_config",
    "provider_for_account",
]
