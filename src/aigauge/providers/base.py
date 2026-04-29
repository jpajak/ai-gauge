from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from ..models import UsageSnapshot


class Provider(ABC):
    """Base class for a usage data source.

    Implementations may either return a snapshot synchronously (for plain HTTP
    providers like Copilot) or invoke the on_done callback asynchronously after
    a QWebEngineView load (Claude/Codex). Use ProviderSignals to bridge to the
    Qt main thread.
    """

    name: str = ""
    display_name: str = ""

    @abstractmethod
    def refresh(self, on_done: Callable[[UsageSnapshot], None]) -> None:
        """Trigger a refresh; deliver the result via on_done.

        on_done must be safe to call from a worker thread; the caller marshals
        it onto the GUI thread.
        """
        raise NotImplementedError


def _make_signals():
    """Construct ProviderSignals lazily so tests don't need PyQt6 installed."""
    from PyQt6.QtCore import QObject, pyqtSignal

    class ProviderSignals(QObject):
        snapshot_ready = pyqtSignal(object)

    return ProviderSignals()


class _LazySignals:
    def __call__(self):
        return _make_signals()


ProviderSignals = _LazySignals()
