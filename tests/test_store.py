import stat

import keyring
import keyring.errors
import pytest

from opencode_swap import macos_keychain
from opencode_swap.models import Platform
from opencode_swap.store import SecretStore

SECRET_VALUE = b"secret-value"


class FakeBackend:
    """In-memory stand-in for either the macOS Keychain or python-keyring,
    with a controllable failure mode and a call counter (for sticky-pin
    assertions)."""

    def __init__(self, error_cls):
        self.data: dict[tuple[str, str], str] = {}
        self.fail = False
        self.calls = 0
        self._error_cls = error_cls

    def get_password(self, service, account):
        self.calls += 1
        if self.fail:
            raise self._error_cls("boom")
        return self.data.get((service, account))

    def set_password(self, service, account, value):
        self.calls += 1
        if self.fail:
            raise self._error_cls("boom")
        self.data[(service, account)] = value

    def delete_password(self, service, account):
        self.calls += 1
        if self.fail:
            raise self._error_cls("boom")
        self.data.pop((service, account), None)


@pytest.fixture
def mac_backend(monkeypatch):
    fake = FakeBackend(macos_keychain.KeychainError)
    monkeypatch.setattr(macos_keychain, "get_password", fake.get_password)
    monkeypatch.setattr(macos_keychain, "set_password", fake.set_password)
    monkeypatch.setattr(macos_keychain, "delete_password", fake.delete_password)
    return fake


@pytest.fixture
def linux_backend(monkeypatch):
    fake = FakeBackend(keyring.errors.KeyringError)
    monkeypatch.setattr(keyring, "get_password", fake.get_password)
    monkeypatch.setattr(keyring, "set_password", fake.set_password)
    monkeypatch.setattr(keyring, "delete_password", fake.delete_password)
    return fake


def test_macos_backend_roundtrip(tmp_path, mac_backend):
    store = SecretStore(tmp_path, platform=Platform.MACOS)
    assert store.backend_name == "keychain"
    store.put("openai:work", "secret-value")
    assert store.get("openai:work") == "secret-value"
    store.delete("openai:work")
    assert store.get("openai:work") is None


def test_macos_fallback_on_write_failure(tmp_path, mac_backend):
    store = SecretStore(tmp_path, platform=Platform.MACOS)
    mac_backend.fail = True
    store.put("openai:work", "secret-value")
    assert store.backend_name == "file"
    assert store.get("openai:work") == "secret-value"

    fallback_file = next(tmp_path.glob("*.enc"))
    assert stat.S_IMODE(fallback_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert SECRET_VALUE not in fallback_file.read_bytes()  # base64, not raw


def test_sticky_pin_never_retries_os_backend(tmp_path, mac_backend):
    store = SecretStore(tmp_path, platform=Platform.MACOS)
    mac_backend.fail = True
    store.put("openai:work", "v1")
    calls_after_failure = mac_backend.calls

    mac_backend.fail = False  # backend "recovers" mid-run
    store.put("openai:other", "v2")
    store.get("openai:work")
    store.delete("openai:work")

    assert mac_backend.calls == calls_after_failure  # never touched again
    assert store.get("openai:other") == "v2"


def test_file_wins_on_read_over_stale_os_value(tmp_path, mac_backend):
    store = SecretStore(tmp_path, platform=Platform.MACOS)
    mac_backend.data[("opencode-swap", "openai:work")] = "stale-os-value"
    store._put_file("openai:work", "fresher-file-value")
    assert store.get("openai:work") == "fresher-file-value"


def test_reconcile_deletes_stale_file_after_os_write(tmp_path, mac_backend):
    store = SecretStore(tmp_path, platform=Platform.MACOS)
    store._put_file("openai:work", "stale-file-value")
    store.put("openai:work", "new-os-value")  # OS backend healthy
    assert not store._file_path("openai:work").exists()
    assert store.get("openai:work") == "new-os-value"


def test_linux_backend_roundtrip(tmp_path, linux_backend):
    store = SecretStore(tmp_path, platform=Platform.LINUX)
    assert store.backend_name == "keyring"
    store.put("openai:work", "secret-value")
    assert store.get("openai:work") == "secret-value"
    store.delete("openai:work")
    assert store.get("openai:work") is None


def test_linux_fallback_on_keyring_error(tmp_path, linux_backend):
    store = SecretStore(tmp_path, platform=Platform.LINUX)
    linux_backend.fail = True
    store.put("openai:work", "secret-value")
    assert store.backend_name == "file"
    assert store.get("openai:work") == "secret-value"


def test_linux_delete_missing_is_noop(tmp_path, linux_backend):
    store = SecretStore(tmp_path, platform=Platform.LINUX)
    store.delete("openai:nonexistent")  # must not raise


def test_unknown_platform_uses_file_backend_only(tmp_path):
    store = SecretStore(tmp_path, platform=Platform.UNKNOWN)
    assert store.backend_name == "file"
    store.put("openai:work", "secret-value")
    assert store.get("openai:work") == "secret-value"
