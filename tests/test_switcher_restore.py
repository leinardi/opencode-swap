import json
import threading
import time

import pytest

from opencode_swap import backup, opencode_auth
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

    metas = switcher.restore(source="bak")
    assert [meta.name for meta in metas] == ["a"]
    assert live_openai(switcher.opencode_auth_path)["accountId"] == "acct-a"
    assert switcher.registry.get_active() == "a"


def test_restore_pristine_restores_content(switcher):
    _setup_two_accounts(switcher)
    switcher.use_account("a")  # pristine snapshot taken here, holds whatever was live (b, from add)
    switcher.use_account("b")

    metas = switcher.restore(source="pristine")
    assert live_openai(switcher.opencode_auth_path)["accountId"] == "acct-b"
    assert [meta.name for meta in metas] == ["b"]


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


def test_restore_write_failure_preserves_restore_source(switcher, monkeypatch):
    _setup_two_accounts(switcher)
    switcher.use_account("a")
    switcher.use_account("b")  # .bak contains a; live contains b

    monkeypatch.setattr("opencode_swap.opencode_auth.atomic_write_auth", lambda *args: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        switcher.restore(source="bak")

    snapshot = switcher.data_root / "backups" / backup.RESTORE_SNAPSHOT_FILENAME
    assert live_openai(switcher.opencode_auth_path)["accountId"] == "acct-b"
    assert backup.read_bak(switcher.data_root)["openai"]["accountId"] == "acct-b"
    assert json.loads(snapshot.read_text())["openai"]["accountId"] == "acct-a"

    with pytest.raises(OpenCodeSwapError, match="previous restore failed"):
        switcher.restore(source="bak")
    assert json.loads(snapshot.read_text())["openai"]["accountId"] == "acct-a"


def test_restore_discard_pending_hint_is_in_the_error(switcher, monkeypatch):
    _setup_two_accounts(switcher)
    switcher.use_account("a")
    switcher.use_account("b")
    monkeypatch.setattr("opencode_swap.opencode_auth.atomic_write_auth", lambda *args: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        switcher.restore(source="bak")

    with pytest.raises(OpenCodeSwapError, match="--discard-pending"):
        switcher.restore(source="bak")


def test_restore_discard_pending_drops_stuck_snapshot_and_proceeds(switcher, monkeypatch):
    """A crash between write_restore_snapshot and the live replace leaves a
    `.restore` snapshot that would otherwise block every future restore
    forever. --discard-pending is the documented escape hatch."""
    _setup_two_accounts(switcher)
    switcher.use_account("a")  # .bak now holds whatever was live before (b)

    original_write = opencode_auth.atomic_write_auth
    monkeypatch.setattr("opencode_swap.opencode_auth.atomic_write_auth", lambda *args: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        switcher.restore(source="bak")
    snapshot = switcher.data_root / "backups" / backup.RESTORE_SNAPSHOT_FILENAME
    assert snapshot.exists()
    # The failed attempt's chaining step already moved current live (a) into
    # .bak before the (mocked) live write failed.
    assert backup.read_bak(switcher.data_root)["openai"]["accountId"] == "acct-a"

    monkeypatch.setattr("opencode_swap.opencode_auth.atomic_write_auth", original_write)
    metas = switcher.restore(source="bak", discard_pending=True)

    assert not snapshot.exists()
    assert [meta.name for meta in metas] == ["a"]
    assert live_openai(switcher.opencode_auth_path)["accountId"] == "acct-a"

    # The discarded pending snapshot (b's state) must be archived, not lost.
    archived = list((switcher.data_root / "backups").glob("discarded-restore-*.json"))
    assert len(archived) == 1
    assert json.loads(archived[0].read_text())["openai"]["accountId"] == "acct-b"


def test_restore_clears_marker_when_live_replacement_already_committed(switcher, monkeypatch):
    _setup_two_accounts(switcher)
    switcher.use_account("a")
    switcher.use_account("b")
    original_remove = backup.remove_restore_snapshot
    monkeypatch.setattr(backup, "remove_restore_snapshot", lambda *args: (_ for _ in ()).throw(OSError("cleanup failed")))

    with pytest.raises(OSError, match="cleanup failed"):
        switcher.restore(source="bak")

    assert live_openai(switcher.opencode_auth_path)["accountId"] == "acct-a"
    monkeypatch.setattr(backup, "remove_restore_snapshot", original_remove)
    metas = switcher.restore(source="bak")

    assert [meta.name for meta in metas] == ["a"]
    assert not (switcher.data_root / "backups" / backup.RESTORE_SNAPSHOT_FILENAME).exists()
    assert live_openai(switcher.opencode_auth_path)["accountId"] == "acct-a"


def test_restore_reads_backup_after_acquiring_lock(switcher, monkeypatch):
    first = {"openai": oauth_entry(account_id="acct-a")}
    second = {"openai": oauth_entry(account_id="acct-b")}
    backup.write_bak(switcher.data_root, first)
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-live"))
    restorer = Switcher(switcher.opencode_auth_path, switcher.data_root, platform=Platform.UNKNOWN)
    errors = []
    attempted_lock = threading.Event()
    release_lock = threading.Event()
    acquire = restorer.lock.acquire

    def gated_acquire(timeout=None):
        attempted_lock.set()
        assert release_lock.wait(timeout=5)
        return acquire(timeout)

    monkeypatch.setattr(restorer.lock, "acquire", gated_acquire)

    def restore():
        try:
            restorer.restore()
        except Exception as exc:  # noqa: BLE001 -- captured for the main thread to assert on, not swallowed
            errors.append(exc)

    thread = threading.Thread(target=restore)
    thread.start()
    assert attempted_lock.wait(timeout=5)
    backup.write_bak(switcher.data_root, second)
    release_lock.set()

    thread.join(timeout=5)
    assert not thread.is_alive()
    assert errors == []
    assert json.loads(switcher.opencode_auth_path.read_text()) == second


def test_restore_recovers_corrupted_live_file(switcher):
    _setup_two_accounts(switcher)
    switcher.use_account("a")
    switcher.use_account("b")  # .bak now holds a

    switcher.opencode_auth_path.write_text("{not valid json")
    metas = switcher.restore(source="bak")

    assert [meta.name for meta in metas] == ["a"]
    assert live_openai(switcher.opencode_auth_path)["accountId"] == "acct-a"


def test_restore_unidentifiable_record_still_succeeds(switcher):
    """If the backed-up data's provider entry can't be interpreted, the
    restore itself must still succeed (fail-safe on identification only)."""
    _setup_two_accounts(switcher)
    backup.write_bak(switcher.data_root, {"openai": {"type": "oauth"}})  # missing required fields

    metas = switcher.restore(source="bak")
    assert metas == []  # couldn't identify, but didn't raise
    assert live_openai(switcher.opencode_auth_path) == {"type": "oauth"}


def test_restore_ignores_malformed_unrelated_stored_credential(switcher):
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-bad"))
    switcher.add_account("bad")
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-a"))
    switcher.add_account("a")
    switcher.secrets.put("openai:bad", "{")
    data = {"openai": oauth_entry(account_id="acct-a")}
    backup.write_bak(switcher.data_root, data)

    assert switcher.restore(source="bak") == []
    assert json.loads(switcher.opencode_auth_path.read_text()) == data


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
