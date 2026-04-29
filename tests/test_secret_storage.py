import sys

import pytest

from aigauge.secret_storage import load_secret, save_secret


def test_round_trip_short():
    save_secret("test-short", "hello")
    assert load_secret("test-short") == "hello"


def test_round_trip_large():
    """The whole point of this module: handle values >2.5KB that keyring can't."""
    big = "x" * 20_000  # 20KB — comfortably exceeds Credential Manager's limit
    save_secret("test-large", big)
    assert load_secret("test-large") == big


def test_overwrite():
    save_secret("test-overwrite", "first")
    save_secret("test-overwrite", "second")
    assert load_secret("test-overwrite") == "second"


def test_delete():
    save_secret("test-delete", "value")
    save_secret("test-delete", None)
    assert load_secret("test-delete") is None


def test_load_missing_returns_none():
    assert load_secret("never-set-this-key") is None


def test_multiple_secrets_independent():
    save_secret("a", "alpha")
    save_secret("b", "beta")
    assert load_secret("a") == "alpha"
    assert load_secret("b") == "beta"
    save_secret("a", None)
    assert load_secret("a") is None
    assert load_secret("b") == "beta"
