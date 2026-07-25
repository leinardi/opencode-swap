import json
import time

import pytest

from opencode_swap import backup
from opencode_swap.exceptions import OpenCodeSwapError
from opencode_swap.models import Platform
from opencode_swap.switcher import Switcher
from tests.helpers import make_jwt


def oauth_entry(account_id="acct-a", refresh="refresh-1", **overrides):
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


def live_openai(path):
    return json.loads(path.read_text())["openai"]


@pytest.fixture
def switcher(tmp_path):
    auth_path = tmp_path / "opencode" / "auth.json"
    data_root = tmp_path / "opencode-swap"
    return Switcher(opencode_auth_path=auth_path, data_root=data_root, platform=Platform.UNKNOWN)


def _setup_two_accounts(switcher):
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-a", refresh="ra"))
    switcher.add_account("a")
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-b", refresh="rb"))
    switcher.add_account("b")


def test_restore_bak_no_snapshot_raises(switcher):
    with pytest.raises(OpenCodeSwapError, match=r"no .bak snapshot"):
        switcher.restore(source="bak")


def test_restore_pristine_no_snapshot_raises(switcher):
    with pytest.raises(OpenCodeSwapError, match="no pristine snapshot"):
        switcher.restore(source="pristine")


def test_restore_unknown_source_raises(switcher):
    with pytest.raises(ValueError, match="unknown restore source"):
        switcher.restore(source="nonsense")


def test_restore_bak_restores_content_and_identifies_owner(switcher):
    _setup_two_accounts(switcher)
    switcher.use_account("a")
    switcher.use_account("b")  # .bak now holds a's state (pre-switch-to-b)

    meta = switcher.restore(source="bak")
    assert meta.name == "a"
    assert live_openai(switcher.opencode_auth_path)["accountId"] == "acct-a"
    assert switcher.registry.get_active() == "a"


def test_restore_pristine_restores_content(switcher):
    _setup_two_accounts(switcher)
    switcher.use_account("a")  # pristine snapshot taken here, holds whatever was live (b, from add)
    switcher.use_account("b")

    meta = switcher.restore(source="pristine")
    assert live_openai(switcher.opencode_auth_path)["accountId"] == "acct-b"
    assert meta.name == "b"


def test_restore_chains_current_into_bak(switcher):
    """A restore is itself undoable: it snapshots what was live right
    before the restore into .bak first."""
    _setup_two_accounts(switcher)
    switcher.use_account("a")
    switcher.use_account("b")

    switcher.restore(source="bak")  # live is now a; .bak now holds what was live (b) just before this restore
    assert live_openai(switcher.opencode_auth_path)["accountId"] == "acct-a"
    assert backup.read_bak(switcher.data_root)["openai"]["accountId"] == "acct-b"

    switcher.restore(source="bak")  # undo the undo
    assert live_openai(switcher.opencode_auth_path)["accountId"] == "acct-b"


def test_restore_recovers_corrupted_live_file(switcher):
    _setup_two_accounts(switcher)
    switcher.use_account("a")
    switcher.use_account("b")  # .bak now holds a

    switcher.opencode_auth_path.write_text("{not valid json")
    meta = switcher.restore(source="bak")

    assert meta.name == "a"
    assert live_openai(switcher.opencode_auth_path)["accountId"] == "acct-a"


def test_restore_unidentifiable_record_still_succeeds(switcher):
    """If the backed-up data's provider entry can't be interpreted, the
    restore itself must still succeed (fail-safe on identification only)."""
    _setup_two_accounts(switcher)
    backup.write_bak(switcher.data_root, {"openai": {"type": "oauth"}})  # missing required fields

    meta = switcher.restore(source="bak")
    assert meta is None  # couldn't identify, but didn't raise
    assert live_openai(switcher.opencode_auth_path) == {"type": "oauth"}


def test_restore_preserves_other_provider_keys(switcher):
    write_auth(
        switcher.opencode_auth_path,
        oauth_entry(account_id="acct-a"),
        extra={"anthropic": {"type": "api", "key": "keep-me"}},
    )
    switcher.add_account("a")
    write_auth(
        switcher.opencode_auth_path,
        oauth_entry(account_id="acct-b"),
        extra={"anthropic": {"type": "api", "key": "keep-me"}},
    )
    switcher.add_account("b")
    switcher.use_account("a")
    switcher.use_account("b")

    switcher.restore(source="bak")
    live = json.loads(switcher.opencode_auth_path.read_text())
    assert live["anthropic"] == {"type": "api", "key": "keep-me"}
