import json

import pytest

from opencode_swap import macos_keychain, transfer
from opencode_swap.exceptions import AccountExistsError, OpenCodeSwapError, SchemaError
from opencode_swap.models import AccountMeta, ImportConflictAction, Platform
from opencode_swap.switcher import Switcher
from tests.helpers import make_jwt
from tests.test_switcher import oauth_entry, write_auth


@pytest.fixture
def source(tmp_path):
    switcher = Switcher(tmp_path / "source-auth.json", tmp_path / "source-data", platform=Platform.UNKNOWN)
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-a", refresh="refresh-a"))
    switcher.add_account("work")
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-b", refresh="refresh-b"))
    switcher.add_account("personal")
    return switcher


def test_export_import_roundtrip_leaves_destination_live_state_unchanged(source, tmp_path):
    archive = tmp_path / "accounts.ocs"
    source.export_accounts(archive, "password")
    destination = Switcher(tmp_path / "destination-auth.json", tmp_path / "destination-data", platform=Platform.UNKNOWN)
    live = {"type": "oauth", "refresh": "foreign", "access": "x", "expires": 1, "accountId": "foreign"}
    write_auth(destination.opencode_auth_path, live)

    assert destination.import_accounts(archive, "password") == 2

    assert set(destination.registry.accounts()) == {"personal", "work"}
    assert destination.registry.get_active() is None
    assert json.loads(destination.secrets.get("openai:work"))["refresh"] == "refresh-a"
    assert json.loads(destination.opencode_auth_path.read_text()) == {"openai": live}


def test_export_syncs_rotated_live_credentials(source, tmp_path):
    write_auth(source.opencode_auth_path, oauth_entry(account_id="acct-b", refresh="refresh-b-rotated"))
    archive = tmp_path / "accounts.ocs"
    source.export_accounts(archive, "password")
    destination = Switcher(tmp_path / "destination-auth.json", tmp_path / "destination-data", platform=Platform.UNKNOWN)

    destination.import_accounts(archive, "password")

    assert json.loads(destination.secrets.get("openai:personal"))["refresh"] == "refresh-b-rotated"


def test_export_refuses_stable_to_refresh_identity_transition(source, tmp_path):
    live = oauth_entry(account_id="unused", refresh="new-live-refresh", access="e30.e30.sig")
    del live["accountId"]
    write_auth(source.opencode_auth_path, live)
    archive = tmp_path / "accounts.ocs"

    with pytest.raises(OpenCodeSwapError, match="does not match the registry-active account"):
        source.export_accounts(archive, "password")

    assert not archive.exists()
    assert json.loads(source.secrets.get("openai:personal"))["refresh"] == "refresh-b"


def test_export_refuses_refresh_to_stable_identity_transition(tmp_path):
    switcher = Switcher(tmp_path / "source-auth.json", tmp_path / "source-data", platform=Platform.UNKNOWN)
    saved = oauth_entry(account_id="unused", refresh="old-dead-refresh", access="e30.e30.sig")
    del saved["accountId"]
    write_auth(switcher.opencode_auth_path, saved)
    switcher.add_account("work")
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-a", refresh="new-live-refresh"))
    archive = tmp_path / "accounts.ocs"

    with pytest.raises(OpenCodeSwapError, match="does not match the registry-active account"):
        switcher.export_accounts(archive, "password")

    assert not archive.exists()
    assert json.loads(switcher.secrets.get("openai:work"))["refresh"] == "old-dead-refresh"


def test_name_conflict_fails_before_any_changes(source, tmp_path):
    archive = tmp_path / "accounts.ocs"
    source.export_accounts(archive, "password")
    destination = Switcher(tmp_path / "destination-auth.json", tmp_path / "destination-data", platform=Platform.UNKNOWN)
    write_auth(destination.opencode_auth_path, oauth_entry(account_id="different"))
    destination.add_account("work")

    with pytest.raises(AccountExistsError, match="already exists"):
        destination.import_accounts(archive, "password")

    assert set(destination.registry.accounts()) == {"work"}
    assert destination.secrets.get("openai:personal") is None


def test_name_conflict_can_be_skipped_while_other_accounts_import(source, tmp_path):
    archive = tmp_path / "accounts.ocs"
    source.export_accounts(archive, "password")
    destination = Switcher(tmp_path / "destination-auth.json", tmp_path / "destination-data", platform=Platform.UNKNOWN)
    write_auth(destination.opencode_auth_path, oauth_entry(account_id="existing-work", refresh="existing-refresh"))
    destination.add_account("work")

    count = destination.import_accounts(archive, "password", lambda _name: ImportConflictAction.SKIP)

    assert count == 1
    assert set(destination.registry.accounts()) == {"personal", "work"}
    assert json.loads(destination.secrets.get("openai:work"))["refresh"] == "existing-refresh"
    assert json.loads(destination.secrets.get("openai:personal"))["refresh"] == "refresh-b"


def test_name_conflict_can_be_overwritten(source, tmp_path):
    archive = tmp_path / "accounts.ocs"
    source.export_accounts(archive, "password")
    destination = Switcher(tmp_path / "destination-auth.json", tmp_path / "destination-data", platform=Platform.UNKNOWN)
    write_auth(destination.opencode_auth_path, oauth_entry(account_id="existing-work", refresh="existing-refresh"))
    destination.add_account("work")

    count = destination.import_accounts(archive, "password", lambda _name: ImportConflictAction.OVERWRITE)

    assert count == 2
    assert destination.registry.accounts()["work"].account_id == "acct-a"
    assert json.loads(destination.secrets.get("openai:work"))["refresh"] == "refresh-a"


def test_overwrite_repairs_registered_account_with_missing_secret(source, tmp_path):
    archive = tmp_path / "accounts.ocs"
    source.export_accounts(archive, "password")
    destination = Switcher(tmp_path / "destination-auth.json", tmp_path / "destination-data", platform=Platform.UNKNOWN)
    destination.registry.upsert_account(AccountMeta("work", "openai", "oauth", "lost", None, "2026-01-01T00:00:00Z"))

    count = destination.import_accounts(archive, "password", lambda _name: ImportConflictAction.OVERWRITE)

    assert count == 2
    assert destination.registry.accounts()["work"].account_id == "acct-a"
    assert json.loads(destination.secrets.get("openai:work"))["refresh"] == "refresh-a"


def test_aborted_name_conflict_makes_no_changes(source, tmp_path):
    archive = tmp_path / "accounts.ocs"
    source.export_accounts(archive, "password")
    destination = Switcher(tmp_path / "destination-auth.json", tmp_path / "destination-data", platform=Platform.UNKNOWN)
    write_auth(destination.opencode_auth_path, oauth_entry(account_id="existing-work", refresh="existing-refresh"))
    destination.add_account("work")

    with pytest.raises(OpenCodeSwapError, match="import aborted"):
        destination.import_accounts(archive, "password", lambda _name: ImportConflictAction.ABORT)

    assert set(destination.registry.accounts()) == {"work"}
    assert destination.secrets.get("openai:personal") is None
    assert json.loads(destination.secrets.get("openai:work"))["refresh"] == "existing-refresh"


def test_identity_conflict_under_different_name_fails_before_changes(source, tmp_path):
    archive = tmp_path / "accounts.ocs"
    source.export_accounts(archive, "password")
    destination = Switcher(tmp_path / "destination-auth.json", tmp_path / "destination-data", platform=Platform.UNKNOWN)
    write_auth(destination.opencode_auth_path, oauth_entry(account_id="acct-a", refresh="other"))
    destination.add_account("existing")

    with pytest.raises(AccountExistsError, match="identity"):
        destination.import_accounts(archive, "password")

    assert set(destination.registry.accounts()) == {"existing"}
    assert destination.secrets.get("openai:personal") is None


def test_import_write_failure_cleans_new_secrets_and_registry(source, tmp_path, monkeypatch):
    archive = tmp_path / "accounts.ocs"
    source.export_accounts(archive, "password")
    destination = Switcher(tmp_path / "destination-auth.json", tmp_path / "destination-data", platform=Platform.UNKNOWN)
    original_put = destination.secrets.put

    def fail_second(key, value):
        if key == "openai:work":
            raise OSError("disk full")
        original_put(key, value)

    monkeypatch.setattr(destination.secrets, "put", fail_second)

    with pytest.raises(OSError, match="disk full"):
        destination.import_accounts(archive, "password")

    assert destination.registry.accounts() == {}
    assert destination.secrets.get("openai:personal") is None
    assert destination.secrets.get("openai:work") is None


def test_import_registry_failure_restores_overwritten_secret(source, tmp_path, monkeypatch):
    archive = tmp_path / "accounts.ocs"
    source.export_accounts(archive, "password")
    destination = Switcher(tmp_path / "destination-auth.json", tmp_path / "destination-data", platform=Platform.UNKNOWN)
    write_auth(destination.opencode_auth_path, oauth_entry(account_id="existing-work", refresh="existing-refresh"))
    destination.add_account("work")

    def fail_registry(_metas):
        raise OSError("disk full")

    monkeypatch.setattr(destination.registry, "upsert_accounts", fail_registry)

    with pytest.raises(OSError, match="disk full"):
        destination.import_accounts(archive, "password", lambda _name: ImportConflictAction.OVERWRITE)

    assert set(destination.registry.accounts()) == {"work"}
    assert destination.secrets.get("openai:personal") is None
    assert json.loads(destination.secrets.get("openai:work"))["refresh"] == "existing-refresh"


def test_import_routes_credentials_to_macos_keychain(source, tmp_path, monkeypatch):
    archive = tmp_path / "accounts.ocs"
    source.export_accounts(archive, "password")
    credentials = {}
    monkeypatch.setattr(macos_keychain, "get_password", lambda service, key: credentials.get((service, key)))
    monkeypatch.setattr(macos_keychain, "set_password", lambda service, key, value: credentials.__setitem__((service, key), value))
    monkeypatch.setattr(macos_keychain, "delete_password", lambda service, key: credentials.pop((service, key), None))
    destination = Switcher(tmp_path / "destination-auth.json", tmp_path / "destination-data", platform=Platform.MACOS)

    destination.import_accounts(archive, "password")

    assert json.loads(credentials[("opencode-swap", "openai:work")])["accountId"] == "acct-a"
    assert not (destination.data_root / "secrets").exists()


def test_import_derives_registry_metadata_instead_of_trusting_archive(tmp_path):
    secret = "super-secret-refresh"
    record = oauth_entry(account_id="acct-a", refresh=secret)
    malicious_meta = AccountMeta(
        name="work",
        provider="openai",
        type="oauth",
        account_id=secret,
        email=secret,
        added="2026-01-01T00:00:00Z",
    )
    archive = tmp_path / "accounts.ocs"
    transfer.write_archive(archive, [transfer.TransferEntry(malicious_meta, record)], "password")
    destination = Switcher(tmp_path / "destination-auth.json", tmp_path / "destination-data", platform=Platform.UNKNOWN)

    destination.import_accounts(archive, "password")

    registry_text = (destination.data_root / "registry.json").read_text()
    assert secret not in registry_text
    assert destination.registry.accounts()["work"].account_id == "acct-a"


def test_import_filters_credentials_from_other_account_metadata(tmp_path):
    secret = "account-a-super-secret-refresh"
    record_a = oauth_entry(account_id="acct-a", refresh=secret)
    record_b = oauth_entry(account_id="acct-b", refresh="refresh-b", access=make_jwt({"email": secret}))
    entries = [
        transfer.TransferEntry(AccountMeta("a", "openai", "oauth", "acct-a", None, "2026-01-01T00:00:00Z"), record_a),
        transfer.TransferEntry(AccountMeta("b", "openai", "oauth", "acct-b", None, "2026-01-01T00:00:00Z"), record_b),
    ]
    archive = tmp_path / "accounts.ocs"
    transfer.write_archive(archive, entries, "password")
    destination = Switcher(tmp_path / "destination-auth.json", tmp_path / "destination-data", platform=Platform.UNKNOWN)

    destination.import_accounts(archive, "password")

    assert destination.registry.accounts()["b"].email is None
    assert secret not in (destination.data_root / "registry.json").read_text()


def test_import_rejects_credential_used_as_account_name_without_printing_it(tmp_path):
    secret = "secret-token-valid-as-name"
    meta = AccountMeta(secret, "openai", "oauth", "acct-a", None, "2026-01-01T00:00:00Z")
    archive = tmp_path / "accounts.ocs"
    transfer.write_archive(archive, [transfer.TransferEntry(meta, oauth_entry(account_id="acct-a", refresh=secret))], "password")
    destination = Switcher(tmp_path / "destination-auth.json", tmp_path / "destination-data", platform=Platform.UNKNOWN)

    with pytest.raises(SchemaError) as exc_info:
        destination.import_accounts(archive, "password")

    assert secret not in str(exc_info.value)
    assert destination.registry.accounts() == {}


def test_import_rejects_invalid_added_timestamp_before_writes(tmp_path):
    meta = AccountMeta("work", "openai", "oauth", "acct-a", None, "not-a-timestamp")
    archive = tmp_path / "accounts.ocs"
    transfer.write_archive(archive, [transfer.TransferEntry(meta, oauth_entry(account_id="acct-a"))], "password")
    destination = Switcher(tmp_path / "destination-auth.json", tmp_path / "destination-data", platform=Platform.UNKNOWN)

    with pytest.raises(SchemaError, match="invalid added timestamp"):
        destination.import_accounts(archive, "password")

    assert destination.registry.accounts() == {}
    assert destination.secrets.get("openai:work") is None


def test_export_normalizes_legacy_missing_added_timestamp(source, tmp_path):
    registry_path = source.data_root / "registry.json"
    registry = json.loads(registry_path.read_text())
    del registry["accounts"]["work"]["added"]
    registry_path.write_text(json.dumps(registry))
    archive = tmp_path / "accounts.ocs"
    source.export_accounts(archive, "password")
    destination = Switcher(tmp_path / "destination-auth.json", tmp_path / "destination-data", platform=Platform.UNKNOWN)

    destination.import_accounts(archive, "password")

    assert destination.registry.accounts()["work"].added
