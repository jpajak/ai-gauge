from usage_view.startup import _startup_command


def test_startup_command_uses_module_launcher():
    command = _startup_command()
    assert "-m usage_view" in command
    assert command.startswith('"')
