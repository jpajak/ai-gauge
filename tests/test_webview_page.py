import logging
from types import SimpleNamespace

from aigauge.webview.page import (
    _NOISY_CONSOLE_FRAGMENTS,
    _python_level_for,
    _safe_source_id,
)


def test_safe_source_id_drops_query_and_fragment():
    source = "https://accounts.google.com/v3/signin?login_hint=person@example.com#frag"

    assert _safe_source_id(source) == "https://accounts.google.com/v3/signin"


def test_python_level_for_demotes_info_to_debug():
    assert _python_level_for(SimpleNamespace(name="InfoMessageLevel")) == logging.DEBUG
    assert _python_level_for(SimpleNamespace(name="WarningMessageLevel")) == logging.INFO
    assert _python_level_for(SimpleNamespace(name="ErrorMessageLevel")) == logging.WARNING


def test_isolated_segment_and_datadog_messages_are_noise():
    # claude.ai's analytics iframe and Datadog RUM produce ~25 lines per page
    # load; they must be in the suppress list so the file log stays readable.
    samples = (
        "[IsolatedSegment] Message received from parent [object Object]",
        "[IsolatedSegment] Analytics loaded successfully",
        "[O11Y] [DatadogRUM] Initialized [object Object]",
    )
    for sample in samples:
        assert any(fragment in sample for fragment in _NOISY_CONSOLE_FRAGMENTS), sample
