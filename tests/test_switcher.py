import json
import stat
import time

import pytest

from opencode_swap import macos_keychain
from opencode_swap.exceptions import AccountExistsError, OpenCodeSwapError, SchemaError
from opencode_swap.models import AccountMeta, Platform
from opencode_swap.switcher import Switcher
from tests.helpers import make_jwt


def oauth_entry(account_id="acct-1", refresh="refresh-1", **overrides):
    entry = {
        "type": "oauth",
        "refresh": refresh,
        "access": make_jwt({"chatgpt_account_id": account_id}),
        "expires": int((time.time() + 3600) * 1000),
        "accountId": account_id,
    }
    entry.update(overrides)
    return entry


def write_auth(path, openai_entry=None, extra=None):
    data = dict(extra or {})
    if openai_entry is not None:
        data["openai"] = openai_entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


@pytest.fixture
def switcher(tmp_path):
    auth_path = tmp_path / "opencode" / "auth.json"
    data_root = tmp_path / "opencode-swap"
    return Switcher(opencode_auth_path=auth_path, data_root=data_root, platform=Platform.UNKNOWN)


def test_add_account_imports_and_marks_active(switcher):
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-1"))
    meta = switcher.add_account("work")

    assert meta.name == "work"
    assert meta.account_id == "acct-1"
    assert switcher.registry.get_active() == "work"

    stored = json.loads(switcher.secrets.get("openai:work"))
    assert stored["accountId"] == "acct-1"


def test_add_account_no_live_entry_raises(switcher):
    write_auth(switcher.opencode_auth_path, None, extra={"anthropic": {"type": "api", "key": "x"}})
    with pytest.raises(OpenCodeSwapError, match="no active OpenAI account"):
        switcher.add_account("work")


def test_add_account_missing_auth_file_raises(switcher):
    with pytest.raises(OpenCodeSwapError, match="run `opencode auth login`"):
        switcher.add_account("work")


def test_add_account_malformed_live_entry_raises_schema_error_not_silently(switcher):
    """Compatibility: an unrecognized/malformed openai entry must fail loud
    (SchemaError), never be silently ignored or guessed at, and must not
    leave any partial registry/secret-store side effects."""
    write_auth(switcher.opencode_auth_path, {"type": "oauth", "refresh": "r"})  # missing access/expires
    with pytest.raises(SchemaError):
        switcher.add_account("work")
    assert switcher.registry.accounts() == {}
    assert switcher.secrets.get("openai:work") is None


def test_readd_same_identity_refreshes_in_place(switcher):
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-1", refresh="r1"))
    first = switcher.add_account("work")

    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-1", refresh="r2-rotated"))
    second = switcher.add_account("work")

    assert second.added == first.added  # original timestamp preserved
    assert len(switcher.registry.accounts()) == 1
    stored = json.loads(switcher.secrets.get("openai:work"))
    assert stored["refresh"] == "r2-rotated"  # captured the rotation


def test_add_same_identity_under_new_name_raises(switcher):
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-1"))
    switcher.add_account("work")
    with pytest.raises(AccountExistsError, match="already saved as 'work'"):
        switcher.add_account("work-again")


def test_add_name_collision_different_identity_raises(switcher):
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-1"))
    switcher.add_account("work")

    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-2"))
    with pytest.raises(AccountExistsError, match="already used by a different account"):
        switcher.add_account("work")


def test_add_refuses_to_overwrite_divergent_orphan_secret(switcher, monkeypatch):
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-a"))
    original_upsert = switcher.registry.upsert_account
    monkeypatch.setattr(switcher.registry, "upsert_account", lambda *args: (_ for _ in ()).throw(OSError("registry write failed")))
    with pytest.raises(OSError, match="registry write failed"):
        switcher.add_account("work")
    monkeypatch.setattr(switcher.registry, "upsert_account", original_upsert)

    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-b"))
    with pytest.raises(AccountExistsError, match="unregistered stored credentials"):
        switcher.add_account("work")

    assert json.loads(switcher.secrets.get("openai:work"))["accountId"] == "acct-a"


def test_add_recovers_matching_orphan_secret_after_registry_failure(switcher, monkeypatch):
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-a"))
    original_upsert = switcher.registry.upsert_account
    monkeypatch.setattr(switcher.registry, "upsert_account", lambda *args: (_ for _ in ()).throw(OSError("registry write failed")))
    with pytest.raises(OSError):
        switcher.add_account("work")
    monkeypatch.setattr(switcher.registry, "upsert_account", original_upsert)

    switcher.add_account("work")
    assert switcher.registry.accounts()["work"].account_id == "acct-a"


def test_add_aborts_when_destination_os_absence_cannot_be_confirmed(tmp_path, monkeypatch):
    switcher = Switcher(tmp_path / "opencode" / "auth.json", tmp_path / "opencode-swap", platform=Platform.MACOS)
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-a"))
    monkeypatch.setattr(macos_keychain, "get_password", lambda *args: (_ for _ in ()).throw(macos_keychain.KeychainError("backend unavailable")))
    monkeypatch.setattr(macos_keychain, "set_password", lambda *args: (_ for _ in ()).throw(AssertionError("must not write")))

    with pytest.raises(OpenCodeSwapError, match=r"cannot confirm|could not confirm"):
        switcher.add_account("work")

    assert switcher.registry.accounts() == {}
    assert not switcher.secrets._file_path("openai:work").exists()


def test_rename_aborts_when_destination_os_absence_cannot_be_confirmed(tmp_path, monkeypatch):
    credentials = {}

    def get_password(service, key):
        if key == "openai:personal":
            raise macos_keychain.KeychainError("backend unavailable")
        return credentials.get((service, key))

    def set_password(service, key, value):
        credentials[(service, key)] = value

    def delete_password(service, key):
        credentials.pop((service, key), None)

    monkeypatch.setattr(macos_keychain, "get_password", get_password)
    monkeypatch.setattr(macos_keychain, "set_password", set_password)
    monkeypatch.setattr(macos_keychain, "delete_password", delete_password)
    switcher = Switcher(tmp_path / "opencode" / "auth.json", tmp_path / "opencode-swap", platform=Platform.MACOS)
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-a"))
    switcher.add_account("work")
    credentials[("opencode-swap", "openai:personal")] = json.dumps(oauth_entry(account_id="acct-b"))

    with pytest.raises(OpenCodeSwapError, match=r"cannot confirm|could not confirm"):
        switcher.rename_account("work", "personal")

    assert "work" in switcher.registry.accounts()
    assert "personal" not in switcher.registry.accounts()
    assert json.loads(credentials[("opencode-swap", "openai:personal")])["accountId"] == "acct-b"


def test_add_recovers_orphan_after_stable_account_token_rotation(switcher, monkeypatch):
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-a", refresh="r1"))
    original_upsert = switcher.registry.upsert_account
    monkeypatch.setattr(switcher.registry, "upsert_account", lambda *args: (_ for _ in ()).throw(OSError("registry write failed")))
    with pytest.raises(OSError):
        switcher.add_account("work")
    monkeypatch.setattr(switcher.registry, "upsert_account", original_upsert)

    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-a", refresh="r2"))
    switcher.add_account("work")

    assert json.loads(switcher.secrets.get("openai:work"))["refresh"] == "r2"


def test_add_rejects_rotated_refresh_only_orphan(switcher, monkeypatch):
    def refresh_only(refresh):
        entry = oauth_entry(account_id="unused", refresh=refresh, access=make_jwt({}))
        del entry["accountId"]
        return entry

    write_auth(switcher.opencode_auth_path, refresh_only("r1"))
    original_upsert = switcher.registry.upsert_account
    monkeypatch.setattr(switcher.registry, "upsert_account", lambda *args: (_ for _ in ()).throw(OSError("registry write failed")))
    with pytest.raises(OSError):
        switcher.add_account("work")
    monkeypatch.setattr(switcher.registry, "upsert_account", original_upsert)

    write_auth(switcher.opencode_auth_path, refresh_only("r2"))
    with pytest.raises(AccountExistsError, match="unregistered stored credentials"):
        switcher.add_account("work")


def test_two_distinct_accounts_second_add_becomes_active(switcher):
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-1"))
    switcher.add_account("work")

    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-2"))
    switcher.add_account("personal")

    assert set(switcher.registry.accounts()) == {"work", "personal"}
    assert switcher.registry.get_active() == "personal"


def test_current_unmanaged_live_account(switcher):
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-1"))
    meta, desc = switcher.current()
    assert meta is None
    assert desc.account_id == "acct-1"


def test_current_recognizes_managed_account(switcher):
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-1"))
    switcher.add_account("work")
    meta, desc = switcher.current()
    assert meta.name == "work"
    assert desc.account_id == "acct-1"


def test_current_none_when_no_auth_file(switcher):
    meta, desc = switcher.current()
    assert meta is None
    assert desc is None


def test_current_none_when_no_openai_entry(switcher):
    write_auth(switcher.opencode_auth_path, None, extra={"anthropic": {"type": "api", "key": "x"}})
    meta, desc = switcher.current()
    assert meta is None
    assert desc is None


def test_current_malformed_live_entry_raises_instead_of_masquerading_as_empty(switcher):
    """Regression: current() must not swallow SchemaError as a generic
    OpenCodeSwapError (it's a subclass) and silently report "no active
    account" -- that would hide a real incompatibility from the user."""
    write_auth(switcher.opencode_auth_path, {"type": "oauth", "refresh": "r"})  # missing access/expires
    with pytest.raises(SchemaError):
        switcher.current()


def test_remove_account(switcher):
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-1"))
    switcher.add_account("work")
    switcher.remove_account("work")
    assert switcher.registry.accounts() == {}
    assert switcher.secrets.get("openai:work") is None


def test_remove_keeps_secret_when_registry_write_fails(switcher, monkeypatch):
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-1"))
    switcher.add_account("work")
    monkeypatch.setattr(switcher.registry, "remove_account", lambda *args: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        switcher.remove_account("work")

    assert switcher.secrets.get("openai:work") is not None


def test_remove_recovers_ambiguous_secret_deletion_in_file_fallback(switcher, monkeypatch):
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-1"))
    switcher.add_account("work")
    original_delete = switcher.secrets.delete

    def delete_then_fail(key):
        original_delete(key)
        raise OpenCodeSwapError("OS deletion timed out after committing")

    monkeypatch.setattr(switcher.secrets, "delete", delete_then_fail)
    with pytest.raises(OpenCodeSwapError, match="timed out"):
        switcher.remove_account("work")

    assert "work" in switcher.registry.accounts()
    assert switcher.secrets.get("openai:work") is not None


def test_remove_unknown_account_raises(switcher):
    with pytest.raises(OpenCodeSwapError, match="no such account"):
        switcher.remove_account("ghost")


def test_use_rejects_unregistered_registry_provider_at_orchestration_boundary(switcher):
    switcher.registry.upsert_account(
        AccountMeta(name="future", provider="future", type="oauth", account_id=None, email=None, added="2026-01-01T00:00:00Z")
    )

    with pytest.raises(OpenCodeSwapError, match="unsupported provider"):
        switcher.use_account("future")


def test_unsupported_provider_error_never_includes_registry_value(switcher):
    secret = "provider-field-token"
    switcher.registry.upsert_account(
        AccountMeta(name="future", provider=secret, type="oauth", account_id=None, email=None, added="2026-01-01T00:00:00Z")
    )

    with pytest.raises(OpenCodeSwapError) as exc_info:
        switcher.use_account("future")

    assert secret not in str(exc_info.value)


def test_rename_account_moves_secret(switcher):
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-1"))
    switcher.add_account("work")
    switcher.rename_account("work", "account@example.test")

    assert "work" not in switcher.registry.accounts()
    assert "account@example.test" in switcher.registry.accounts()
    assert switcher.secrets.get("openai:work") is None
    stored = json.loads(switcher.secrets.get("openai:account@example.test"))
    assert stored["accountId"] == "acct-1"
    assert switcher.registry.get_active() == "account@example.test"


def test_rename_keeps_old_secret_when_registry_write_fails(switcher, monkeypatch):
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-1"))
    switcher.add_account("work")
    monkeypatch.setattr(switcher.registry, "rename_account", lambda *args: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        switcher.rename_account("work", "personal")

    assert switcher.secrets.get("openai:work") is not None
    assert switcher.secrets.get("openai:personal") is None


def test_rename_aborts_when_old_secret_cannot_be_loaded(switcher, monkeypatch):
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-1"))
    switcher.add_account("work")
    original_get = switcher.secrets.get
    monkeypatch.setattr(switcher.secrets, "get", lambda key: None if key == "openai:work" else original_get(key))

    with pytest.raises(OpenCodeSwapError, match="no stored credentials"):
        switcher.rename_account("work", "personal")

    assert "work" in switcher.registry.accounts()
    assert "personal" not in switcher.registry.accounts()


def test_rename_compensates_when_old_secret_cleanup_fails(switcher, monkeypatch):
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-1"))
    switcher.add_account("work")
    original_delete = switcher.secrets.delete

    def fail_old_delete(key):
        if key == "openai:work":
            raise OpenCodeSwapError("boom")
        original_delete(key)

    monkeypatch.setattr(switcher.secrets, "delete", fail_old_delete)
    with pytest.raises(OpenCodeSwapError, match="boom"):
        switcher.rename_account("work", "personal")

    assert "work" in switcher.registry.accounts()
    assert "personal" not in switcher.registry.accounts()
    assert switcher.secrets.get("openai:work") is not None


def test_rename_retains_new_mapping_when_old_secret_recovery_fails(switcher, monkeypatch):
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-1"))
    switcher.add_account("work")
    original_delete = switcher.secrets.delete
    original_put = switcher.secrets.put

    def delete_then_fail(key):
        if key == "openai:work":
            original_delete(key)
            raise OpenCodeSwapError("old deletion timed out")
        original_delete(key)

    def fail_old_recovery(key, value):
        if key == "openai:work":
            raise OpenCodeSwapError("old credential recovery failed")
        original_put(key, value)

    monkeypatch.setattr(switcher.secrets, "delete", delete_then_fail)
    monkeypatch.setattr(switcher.secrets, "put", fail_old_recovery)
    with pytest.raises(OpenCodeSwapError, match="old credential recovery failed"):
        switcher.rename_account("work", "personal")

    assert "personal" in switcher.registry.accounts()
    assert "work" not in switcher.registry.accounts()
    assert switcher.secrets.get("openai:personal") is not None


def test_rename_unknown_raises(switcher):
    with pytest.raises(OpenCodeSwapError, match="no such account"):
        switcher.rename_account("ghost", "new")


def test_rename_to_existing_raises(switcher):
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-1"))
    switcher.add_account("work")
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-2"))
    switcher.add_account("personal")

    with pytest.raises(AccountExistsError):
        switcher.rename_account("work", "personal")


def test_rename_refuses_to_overwrite_divergent_orphan_secret(switcher):
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-a"))
    switcher.add_account("work")
    switcher.secrets.put("openai:personal", json.dumps(oauth_entry(account_id="acct-b")))

    with pytest.raises(AccountExistsError, match="unregistered stored credentials"):
        switcher.rename_account("work", "personal")

    assert "work" in switcher.registry.accounts()
    assert "personal" not in switcher.registry.accounts()
    assert json.loads(switcher.secrets.get("openai:personal"))["accountId"] == "acct-b"


def test_fetch_usage_none_for_unknown_account(switcher):
    assert switcher.fetch_usage("ghost") is None


def test_fetch_usage_none_for_api_key_account(switcher, monkeypatch):
    write_auth(switcher.opencode_auth_path, {"type": "api", "key": "sk-abc"})
    switcher.add_account("work")
    assert switcher.fetch_usage("work") is None


def test_fetch_usage_none_when_secret_missing(switcher):
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-1"))
    switcher.add_account("work")
    switcher.secrets.delete("openai:work")
    assert switcher.fetch_usage("work") is None


def test_fetch_usage_delegates_with_access_and_account_id(switcher, monkeypatch):
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-1", refresh="r1"))
    switcher.add_account("work")

    captured = {}

    def fake_fetch(access_token, account_id):
        captured["access"] = access_token
        captured["account_id"] = account_id
        return "sentinel"

    monkeypatch.setattr("opencode_swap.switcher.usage.fetch_openai_oauth_usage", fake_fetch)
    result = switcher.fetch_usage("work")
    assert result == "sentinel"
    assert captured["account_id"] == "acct-1"
    assert captured["access"]  # the stored access token was passed through


def test_data_root_created_with_0700(switcher):
    assert stat.S_IMODE(switcher.data_root.stat().st_mode) == 0o700


def test_add_preserves_other_provider_keys_untouched(switcher):
    write_auth(
        switcher.opencode_auth_path,
        oauth_entry(account_id="acct-1"),
        extra={"anthropic": {"type": "api", "key": "keep-me"}},
    )
    switcher.add_account("work")
    live = json.loads(switcher.opencode_auth_path.read_text())
    assert live["anthropic"] == {"type": "api", "key": "keep-me"}
