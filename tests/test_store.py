import base64
import stat

import pytest

from opencode_swap import macos_keychain
from opencode_swap.exceptions import SecretStoreError
from opencode_swap.models import Platform
from opencode_swap.store import RecordLocation, SecretStore

SECRET_VALUE = b"secret-value"


class FakeBackend:
    """In-memory macOS Keychain stand-in with failure and call controls."""

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
    with pytest.raises(SecretStoreError):
        store.delete("openai:work")

    assert mac_backend.calls == calls_after_failure  # never touched again
    assert store.get("openai:other") == "v2"


def test_delete_os_failure_keeps_credential_and_surfaces_error(tmp_path, mac_backend):
    store = SecretStore(tmp_path, platform=Platform.MACOS)
    store.put("openai:work", "secret-value")
    mac_backend.fail = True

    with pytest.raises(SecretStoreError, match="could not confirm"):
        store.delete("openai:work")

    # The Keychain item now holds the v3 data key, not the raw credential.
    # This instance is sticky-pinned to the file backend after the failure
    # (see test_sticky_pin_never_retries_os_backend) and won't touch the
    # Keychain again itself, but nothing was destroyed: a fresh SecretStore
    # -- e.g. the next CLI invocation -- can still recover the record.
    mac_backend.fail = False
    fresh_store = SecretStore(tmp_path, platform=Platform.MACOS)
    assert fresh_store.get("openai:work") == "secret-value"


def test_confirmed_get_refuses_absence_when_os_backend_fails(tmp_path, mac_backend):
    store = SecretStore(tmp_path, platform=Platform.MACOS)
    mac_backend.fail = True

    with pytest.raises(SecretStoreError, match=r"cannot confirm|could not confirm"):
        store.get_confirmed("openai:work")

    assert not store._file_path("openai:work").exists()


def test_file_directory_is_private_before_secret_publication(tmp_path, monkeypatch):
    store = SecretStore(tmp_path / "secrets", platform=Platform.UNKNOWN)

    def check_private_directory(*args, **kwargs):
        assert stat.S_IMODE(store._dir.stat().st_mode) == 0o700

    monkeypatch.setattr("opencode_swap.store.atomic_write_bytes", check_private_directory)
    store.put("openai:work", "secret-value")


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


def test_linux_uses_private_file_backend(tmp_path):
    store = SecretStore(tmp_path, platform=Platform.LINUX)
    assert store.backend_name == "file"
    store.put("openai:work", "secret-value")
    assert store.get("openai:work") == "secret-value"
    secret_file = next(tmp_path.glob("*.enc"))
    assert stat.S_IMODE(secret_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert SECRET_VALUE not in secret_file.read_bytes()
    store.delete("openai:work")
    assert store.get("openai:work") is None


def test_linux_delete_missing_is_noop(tmp_path):
    store = SecretStore(tmp_path, platform=Platform.LINUX)
    store.delete("openai:nonexistent")  # must not raise


def test_file_delete_wraps_os_error_as_secret_store_error(tmp_path, monkeypatch):
    """A raw OSError (EACCES/EIO) from the filesystem delete must surface as
    a typed SecretStoreError, not escape uncaught -- callers that suppress
    only SecretStoreError/OpenCodeSwapError around a delete-rollback must be
    able to catch this."""
    store = SecretStore(tmp_path, platform=Platform.LINUX)
    store.put("openai:work", "secret-value")

    def boom(self, *args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr("pathlib.Path.unlink", boom)
    with pytest.raises(SecretStoreError):
        store.delete("openai:work")


def test_unknown_platform_uses_file_backend_only(tmp_path):
    store = SecretStore(tmp_path, platform=Platform.UNKNOWN)
    assert store.backend_name == "file"
    store.put("openai:work", "secret-value")
    assert store.get("openai:work") == "secret-value"


@pytest.mark.parametrize("encoded", [b"not valid base64!", base64.b64encode(b"\xff")])
def test_corrupt_file_secret_raises_content_free_store_error(tmp_path, encoded):
    store = SecretStore(tmp_path, platform=Platform.UNKNOWN)
    secret = "credential-not-in-error"
    store._file_path("openai:work").write_bytes(encoded)

    with pytest.raises(SecretStoreError) as exc_info:
        store.get("openai:work")

    assert secret not in str(exc_info.value)


def test_new_fallback_filenames_are_injective(tmp_path):
    store = SecretStore(tmp_path, platform=Platform.UNKNOWN)

    assert store._file_path("openai:a_b") != store._file_path("openai_a:b")


def test_reading_legacy_fallback_is_read_only_until_next_write(tmp_path):
    store = SecretStore(tmp_path, platform=Platform.UNKNOWN)
    key = "openai:work"
    legacy_path = store._legacy_file_path(key)
    legacy_path.write_bytes(base64.b64encode(b"secret-value"))

    assert store.get(key) == "secret-value"
    assert not store._file_path(key).exists()
    store.put(key, "rotated-secret")
    assert store.get(key) == "rotated-secret"
    assert not legacy_path.exists()


def test_new_opaque_key_never_reads_or_deletes_colliding_legacy_fallback(tmp_path):
    store = SecretStore(tmp_path, platform=Platform.UNKNOWN)
    old_key = "openai:a_b"
    new_key = "openai_a:b"
    legacy_path = store._legacy_file_path(old_key)
    legacy_path.write_bytes(base64.b64encode(b"old-secret"))

    assert store.get(new_key) is None
    store.put(new_key, "new-secret")

    assert legacy_path.exists()
    assert store.get(old_key) == "old-secret"
    assert store.get(new_key) == "new-secret"


def test_legacy_probe_rejects_extra_separator_alias(tmp_path):
    store = SecretStore(tmp_path, platform=Platform.UNKNOWN)
    old_key = "openai:a_b"
    alias_key = "openai:a:b"
    legacy_path = store._legacy_file_path(old_key)
    legacy_path.write_bytes(base64.b64encode(b"old-secret"))

    assert store.get(alias_key) is None
    store.put(alias_key, "new-secret")

    assert legacy_path.exists()
    assert store.get(old_key) == "old-secret"
    assert store.get(alias_key) == "new-secret"


def test_digest_fallback_filename_fits_maximum_account_name(tmp_path):
    store = SecretStore(tmp_path, platform=Platform.UNKNOWN)
    key = f"openai:{'a' * 130}"
    legacy_path = store._legacy_file_path(key)
    legacy_path.write_bytes(base64.b64encode(b"legacy-secret"))

    assert len(store._file_path(key).name.encode()) <= 255
    assert store.get(key) == "legacy-secret"
    store.put(key, "rotated-secret")
    assert store.get(key) == "rotated-secret"


def test_overlong_legacy_candidate_skips_legacy_probe(tmp_path):
    store = SecretStore(tmp_path, platform=Platform.UNKNOWN)
    key = f"openai:{'a' * 300}"

    assert store.get(key) is None
    store.put(key, "secret-value")
    assert store.get(key) == "secret-value"


def test_missing_secrets_dir_uses_existing_parent_name_limit(tmp_path, monkeypatch):
    store = SecretStore(tmp_path / "missing" / "secrets", platform=Platform.UNKNOWN)
    key = "openai:work"

    def name_max(path, name):
        assert path == tmp_path
        assert name == "PC_NAME_MAX"
        return 10

    monkeypatch.setattr("opencode_swap.store.os.pathconf", name_max)
    monkeypatch.setattr(store, "_legacy_file_path", lambda *args: (_ for _ in ()).throw(AssertionError("must not probe legacy")))

    assert store.get(key) is None


def test_linux_write_publishes_v2_when_legacy_cleanup_fails(tmp_path, monkeypatch):
    store = SecretStore(tmp_path, platform=Platform.LINUX)
    key = "openai:work"
    legacy_path = store._legacy_file_path(key)
    legacy_path.write_bytes(base64.b64encode(b"stale-secret"))
    original_unlink = type(legacy_path).unlink

    def fail_legacy_unlink(path, *args, **kwargs):
        if path == legacy_path:
            raise OSError("legacy cleanup failed")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(type(legacy_path), "unlink", fail_legacy_unlink)
    store.put(key, "fresh-secret")

    assert legacy_path.exists()
    assert store._file_path(key).exists()
    assert store.get(key) == "fresh-secret"


# --- v3 envelope (macOS) ---------------------------------------------------


def test_macos_large_value_stays_on_keychain_backend(tmp_path, mac_backend):
    """The original bug report: a >2KB OAuth record (way over `security
    -i`'s ~4095-char stdin limit) must not spuriously fall back to the
    file backend -- the envelope's Keychain value is a fixed-size data
    key, independent of credential size."""
    store = SecretStore(tmp_path, platform=Platform.MACOS)
    large_value = '{"padding": "' + "x" * 3000 + '"}'

    store.put("openai:work", large_value)

    assert store.backend_name == "keychain"
    assert store.get("openai:work") == large_value
    keychain_value = mac_backend.data[("opencode-swap", "openai:work")]
    assert len(keychain_value) < 100  # the data key, not the credential


def test_macos_sealed_file_never_contains_plaintext(tmp_path, mac_backend):
    store = SecretStore(tmp_path, platform=Platform.MACOS)
    secret = "super-secret-oauth-token-value"
    store.put("openai:work", secret)

    blob = store._sealed_file_path("openai:work").read_bytes()
    assert secret.encode("utf-8") not in blob


def test_macos_aad_binding_rejects_swapped_account_blob(tmp_path, mac_backend):
    """A v3 blob is bound to its own key_id via AAD, so copying it onto a
    different account's filename must fail to decrypt rather than
    silently reading as that other account's credential."""
    store = SecretStore(tmp_path, platform=Platform.MACOS)
    store.put("openai:work", "work-secret")
    store.put("openai:personal", "personal-secret")

    work_path = store._sealed_file_path("openai:work")
    personal_path = store._sealed_file_path("openai:personal")
    work_path.write_bytes(personal_path.read_bytes())  # swap in personal's blob

    with pytest.raises(SecretStoreError):
        store.get("openai:work")


def test_macos_orphaned_sealed_file_without_key_is_garbage_collected(tmp_path, mac_backend):
    store = SecretStore(tmp_path, platform=Platform.MACOS)
    store.put("openai:work", "secret-value")
    mac_backend.data.pop(("opencode-swap", "openai:work"))  # simulate a crash-orphaned file

    assert store.get("openai:work") is None
    assert not store._sealed_file_path("openai:work").exists()


def test_macos_delete_removes_key_before_file_leaves_blob_undecryptable(tmp_path, mac_backend, monkeypatch):
    store = SecretStore(tmp_path, platform=Platform.MACOS)
    store.put("openai:work", "secret-value")

    original_unlink = type(store._sealed_file_path("openai:work")).unlink

    def fail_unlink(path, *args, **kwargs):
        if path == store._sealed_file_path("openai:work"):
            raise OSError("simulated unlink failure")
        return original_unlink(path, *args, **kwargs)

    unlink_type = type(store._sealed_file_path("openai:work"))
    monkeypatch.setattr(unlink_type, "unlink", fail_unlink)
    with pytest.raises(SecretStoreError):
        store.delete("openai:work")

    # The Keychain key is gone even though the file survived -- the blob
    # is cryptographically dead, not recoverable plaintext left behind.
    assert ("opencode-swap", "openai:work") not in mac_backend.data
    assert store._sealed_file_path("openai:work").exists()
    # Restore only the unlink patch -- monkeypatch.undo() would also
    # revert the shared `mac_backend` fixture's Keychain patches.
    monkeypatch.setattr(unlink_type, "unlink", original_unlink)
    assert store.get("openai:work") is None


def test_macos_update_reuses_existing_data_key(tmp_path, mac_backend):
    """An update must not rotate the Keychain data key -- otherwise a
    crash between the Keychain write and the file write could leave a
    file encrypted under a key that no longer matches what's stored."""
    store = SecretStore(tmp_path, platform=Platform.MACOS)
    store.put("openai:work", "v1")
    first_key = mac_backend.data[("opencode-swap", "openai:work")]
    calls_before = mac_backend.calls

    store.put("openai:work", "v2")

    assert mac_backend.data[("opencode-swap", "openai:work")] == first_key
    assert mac_backend.calls == calls_before + 1  # one read, no rewrite
    assert store.get("openai:work") == "v2"


def test_upgrade_reseals_v2_file_into_v3_and_removes_it(tmp_path, mac_backend):
    store = SecretStore(tmp_path, platform=Platform.MACOS)
    store._put_file("openai:work", "legacy-secret")  # simulate a pre-existing v2 record

    store.upgrade("openai:work")

    assert store._sealed_file_path("openai:work").exists()
    assert not store._file_path("openai:work").exists()
    assert store.get("openai:work") == "legacy-secret"


def test_upgrade_is_noop_when_already_sealed(tmp_path, mac_backend):
    store = SecretStore(tmp_path, platform=Platform.MACOS)
    store.put("openai:work", "secret-value")
    calls_before = mac_backend.calls

    store.upgrade("openai:work")

    assert mac_backend.calls == calls_before
    assert store.get("openai:work") == "secret-value"


def test_upgrade_is_noop_when_absent(tmp_path, mac_backend):
    store = SecretStore(tmp_path, platform=Platform.MACOS)
    store.upgrade("openai:nonexistent")  # must not raise
    assert store.get("openai:nonexistent") is None


def test_record_location_reports_sealed_fallback_and_missing(tmp_path, mac_backend):
    store = SecretStore(tmp_path, platform=Platform.MACOS)
    store.put("openai:sealed", "value")
    store._put_file("openai:legacy", "value")

    assert store.record_location("openai:sealed") is RecordLocation.SEALED
    assert store.record_location("openai:legacy") is RecordLocation.FILE_FALLBACK
    assert store.record_location("openai:missing") is RecordLocation.MISSING
