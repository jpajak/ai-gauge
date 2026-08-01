from types import SimpleNamespace

from aigauge.app import App
from aigauge.icons import app_icon, app_icon_path


def test_application_icon_assets_are_packaged(qtbot):
    for extension in ("png", "ico", "icns"):
        path = app_icon_path(extension)
        assert path.is_file()
        assert path.stat().st_size > 0

    icon = app_icon()
    assert not icon.isNull()
    assert not icon.pixmap(32, 32).isNull()


def test_floating_mode_tray_uses_application_logo(qtbot):
    fake_app = SimpleNamespace(_ui_mode="floating_widget")

    icon = App._render_tray_icon(fake_app)

    assert not icon.isNull()
    assert not icon.pixmap(32, 32).isNull()
