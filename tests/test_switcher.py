import json
import stat
import time

import pytest

from opencode_swap.exceptions import AccountExistsError, OpenCodeSwapError, SchemaError
from opencode_swap.models import Platform
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


def test_remove_unknown_account_raises(switcher):
    with pytest.raises(OpenCodeSwapError, match="no such account"):
        switcher.remove_account("ghost")


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
