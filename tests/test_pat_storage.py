import keyring

from aigauge import config


def test_set_github_pat_uses_credential_manager_only(monkeypatch):
    keyring_calls = []
    secret_calls = []
    monkeypatch.setattr(
        config.keyring,
        "set_password",
        lambda service, account, value: keyring_calls.append((service, account, value)),
    )
    monkeypatch.setattr(
        "aigauge.secret_storage.save_secret",
        lambda name, value: secret_calls.append((name, value)),
    )

    config.set_github_pat("ghp_test")

    assert keyring_calls == [(config.KEYRING_SERVICE, config.KEYRING_GITHUB_PAT, "ghp_test")]
    assert secret_calls == [(config.KEYRING_GITHUB_PAT, None)]


def test_get_github_pat_migrates_legacy_secret_to_credential_manager(monkeypatch):
    keyring_store = {"value": None}
    secret_calls = []
    monkeypatch.setattr(config.keyring, "get_password", lambda _service, _account: None)

    def set_password(_service, _account, value):
        keyring_store["value"] = value

    monkeypatch.setattr(config.keyring, "set_password", set_password)
    monkeypatch.setattr("aigauge.secret_storage.load_secret", lambda _name: "legacy")
    monkeypatch.setattr(
        "aigauge.secret_storage.save_secret",
        lambda name, value: secret_calls.append((name, value)),
    )

    assert config.get_github_pat() == "legacy"
    assert keyring_store["value"] == "legacy"
    assert secret_calls == [(config.KEYRING_GITHUB_PAT, None)]


def test_get_github_pat_can_still_read_legacy_secret_if_keyring_unavailable(monkeypatch):
    monkeypatch.setattr(config.keyring, "get_password", lambda _service, _account: None)

    def raise_keyring_error(_service, _account, _value):
        raise keyring.errors.KeyringError("no backend")

    monkeypatch.setattr(config.keyring, "set_password", raise_keyring_error)
    monkeypatch.setattr("aigauge.secret_storage.load_secret", lambda _name: "legacy")

    assert config.get_github_pat() == "legacy"
