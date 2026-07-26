from aigauge.config import ColorThresholds, Config
from aigauge.gauge import (
    band_for_percent,
    highest_indicator,
    provider_max_percent,
)
from aigauge.models import SnapshotStatus, UsageMetric, UsageSnapshot


def _snapshot(provider: str, *percentages: float) -> UsageSnapshot:
    return UsageSnapshot(
        provider=provider,
        status=SnapshotStatus.OK,
        metrics=[
            UsageMetric(label=f"Metric {index}", percent_used=percent)
            for index, percent in enumerate(percentages)
        ],
    )


def test_default_bands_preserve_original_fractional_boundaries():
    colors = ColorThresholds()

    assert band_for_percent(59.9, colors) == "green"
    assert band_for_percent(60.0, colors) == "yellow"
    assert band_for_percent(79.9, colors) == "yellow"
    assert band_for_percent(80.0, colors) == "orange"
    assert band_for_percent(94.9, colors) == "orange"
    assert band_for_percent(95.0, colors) == "red"


def test_provider_indicator_uses_highest_metric():
    assert provider_max_percent(_snapshot("claude", 25.0, 82.0)) == 82.0


def test_single_tray_indicator_prioritizes_configured_severity_band():
    config = Config()
    codex = next(account for account in config.browser_accounts if account.id == "codex")
    codex.colors = ColorThresholds(
        green_max=10,
        yellow_max=20,
        orange_max=40,
        red_color="#123456",
    )
    snapshots = {
        "claude": _snapshot("claude", 90.0),
        "codex": _snapshot("codex", 50.0),
    }

    indicator = highest_indicator(config, snapshots, ("claude", "codex"))

    assert indicator is not None
    assert indicator.provider == "codex"
    assert indicator.band == "red"
    assert indicator.color == "#123456"