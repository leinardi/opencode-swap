import json
import os
import time
from pathlib import Path

import pytest

from opencode_swap import backup, macos_keychain, opencode_auth
from opencode_swap.exceptions import OpenCodeSwapError, SchemaError, SecretStoreError
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


def test_switch_round_trip_exact_bytes(switcher):
    _setup_two_accounts(switcher)

    switcher.use_account("a")
    assert live_openai(switcher.opencode_auth_path) == json.loads(switcher.secrets.get("openai:a"))

    switcher.use_account("b")
    assert live_openai(switcher.opencode_auth_path) == json.loads(switcher.secrets.get("openai:b"))

    switcher.use_account("a")
    assert live_openai(switcher.opencode_auth_path) == json.loads(switcher.secrets.get("openai:a"))


def test_use_sets_registry_active(switcher):
    _setup_two_accounts(switcher)
    switcher.use_account("a")
    assert switcher.registry.get_active() == "a"


def test_use_no_such_account_raises(switcher):
    with pytest.raises(OpenCodeSwapError, match="no such account"):
        switcher.use_account("ghost")


def test_use_missing_secret_raises(switcher):
    _setup_two_accounts(switcher)
    switcher.secrets.delete("openai:a")  # simulate out-of-sync secret store
    with pytest.raises(OpenCodeSwapError, match="no stored credentials"):
        switcher.use_account("a")


def test_use_malformed_live_entry_raises_and_leaves_auth_untouched(switcher):
    """Compatibility: a malformed live openai entry must abort the switch
    with SchemaError (fail-safe) rather than being silently overwritten or
    guessed at, and auth.json must stay exactly as it was."""
    _setup_two_accounts(switcher)
    write_auth(switcher.opencode_auth_path, {"type": "oauth", "refresh": "r"})  # missing access/expires
    before = switcher.opencode_auth_path.read_text()

    with pytest.raises(SchemaError):
        switcher.use_account("a")

    assert switcher.opencode_auth_path.read_text() == before
    assert switcher.registry.get_active() == "b"  # unchanged from setup


def test_use_preserves_other_provider_keys(switcher):
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
    live = json.loads(switcher.opencode_auth_path.read_text())
    assert live["anthropic"] == {"type": "api", "key": "keep-me"}


def test_use_captures_rotation_on_switch_out(switcher):
    """R1: OpenCode rotates the active account's tokens in place; opencode-swap
    must capture that rotation before overwriting with a different account,
    or the saved copy goes stale (dead refresh token)."""
    _setup_two_accounts(switcher)
    switcher.use_account("a")
    assert json.loads(switcher.secrets.get("openai:a"))["refresh"] == "ra"

    # Simulate OpenCode refreshing a's token while a is live.
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-a", refresh="ra-rotated"))

    switcher.use_account("b")
    stored_a = json.loads(switcher.secrets.get("openai:a"))
    assert stored_a["refresh"] == "ra-rotated"  # captured, not stale

    switcher.use_account("a")
    assert live_openai(switcher.opencode_auth_path)["refresh"] == "ra-rotated"


def test_use_self_switch_is_idempotent(switcher):
    _setup_two_accounts(switcher)
    switcher.use_account("a")
    before = live_openai(switcher.opencode_auth_path)
    switcher.use_account("a")
    assert live_openai(switcher.opencode_auth_path) == before


def test_use_self_switch_keeps_rotated_credentials(switcher):
    _setup_two_accounts(switcher)
    switcher.use_account("a")
    rotated = oauth_entry(account_id="acct-a", refresh="ra-rotated")
    write_auth(switcher.opencode_auth_path, rotated)

    switcher.use_account("a")

    assert live_openai(switcher.opencode_auth_path)["refresh"] == "ra-rotated"
    assert json.loads(switcher.secrets.get("openai:a"))["refresh"] == "ra-rotated"


def test_use_rejects_malformed_stored_target_before_live_write(switcher):
    _setup_two_accounts(switcher)
    before = switcher.opencode_auth_path.read_text()
    switcher.secrets.put("openai:a", json.dumps({"type": "oauth"}))

    with pytest.raises(SchemaError):
        switcher.use_account("a")

    assert switcher.opencode_auth_path.read_text() == before


def test_use_aborts_ambiguous_no_id_live_credential(switcher):
    def fallback_entry(refresh):
        entry = oauth_entry(account_id="unused", refresh=refresh, access=make_jwt({}))
        del entry["accountId"]
        return entry

    write_auth(switcher.opencode_auth_path, fallback_entry("ra"))
    switcher.add_account("a")
    write_auth(switcher.opencode_auth_path, fallback_entry("rb"))
    switcher.add_account("b")
    switcher.use_account("a")
    write_auth(switcher.opencode_auth_path, fallback_entry("ra-rotated"))

    with pytest.raises(OpenCodeSwapError, match="refused to overwrite"):
        switcher.use_account("b")

    assert json.loads(switcher.secrets.get("openai:a"))["refresh"] == "ra"
    assert live_openai(switcher.opencode_auth_path)["refresh"] == "ra-rotated"
    unclaimed = list((switcher.data_root / "backups").glob("unclaimed-*.json"))
    assert len(unclaimed) == 1
    assert json.loads(unclaimed[0].read_text())["refresh"] == "ra-rotated"


def test_use_unclaimed_foreign_login_is_stashed(switcher):
    _setup_two_accounts(switcher)
    # An external `opencode auth login` to an account opencode-swap has
    # never been told about.
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-unknown", refresh="unknown-r"))

    switcher.use_account("a")

    unclaimed = list((switcher.data_root / "backups").glob("unclaimed-*.json"))
    assert len(unclaimed) == 1
    assert json.loads(unclaimed[0].read_text())["accountId"] == "acct-unknown"
    assert live_openai(switcher.opencode_auth_path)["accountId"] == "acct-a"


def test_use_aborts_instead_of_orphaning_credential_on_keychain_outage(tmp_path, monkeypatch):
    """A macOS Keychain outage must not be misread as "this account's
    secret is absent" (which would misclassify the live, still-owned
    credential as foreign and stash it as unclaimed while reporting a
    successful switch). It must instead abort the switch before auth.json
    or the secret store are touched."""

    class FlakyKeychain:
        """Answers the first `succeed_for` reads normally, then errors on
        every call after that -- reproducing a Keychain that goes
        unreachable partway through a single `use_account` invocation
        (screen lock, SSH session, `security` timeout), rather than being
        down from the very start."""

        def __init__(self):
            self.data: dict[tuple[str, str], str] = {}
            self.succeed_for = 0
            self._reads = 0

        def get_password(self, service, account):
            self._reads += 1
            if self._reads > self.succeed_for:
                raise macos_keychain.KeychainError("boom")
            return self.data.get((service, account))

        def set_password(self, service, account, value):
            self.data[(service, account)] = value

        def delete_password(self, service, account):
            self.data.pop((service, account), None)

    keychain = FlakyKeychain()
    keychain.succeed_for = 10_000  # unlimited during setup
    monkeypatch.setattr(macos_keychain, "get_password", keychain.get_password)
    monkeypatch.setattr(macos_keychain, "set_password", keychain.set_password)
    monkeypatch.setattr(macos_keychain, "delete_password", keychain.delete_password)

    auth_path = tmp_path / "opencode" / "auth.json"
    data_root = tmp_path / "opencode-swap"
    switcher = Switcher(opencode_auth_path=auth_path, data_root=data_root, platform=Platform.MACOS)

    write_auth(auth_path, oauth_entry(account_id="acct-a", refresh="r1"))
    switcher.add_account("work")
    write_auth(auth_path, oauth_entry(account_id="acct-b", refresh="r1b"))
    switcher.add_account("home")
    # `work` is live again, with a rotated refresh token opencode itself wrote.
    write_auth(auth_path, oauth_entry(account_id="acct-a", refresh="r2-rotated"))
    auth_before = json.loads(auth_path.read_text())
    work_secret_before = switcher.secrets.get("openai:work")

    # The outage starts after the target record's own read succeeds (as in
    # the real finding), then fails every read after that.
    keychain._reads = 0
    keychain.succeed_for = 1
    with pytest.raises(SecretStoreError):
        switcher.use_account("home")

    assert json.loads(auth_path.read_text()) == auth_before
    assert list((data_root / "backups").glob("unclaimed-*.json")) == []

    # This SecretStore instance now stickily pins to the file backend (by
    # design, once an OS-backend call has failed). Recovery is verified
    # through a fresh instance -- e.g. the next CLI invocation -- once the
    # Keychain is healthy again, exactly like a real second `opencode-swap`
    # call after the outage clears.
    keychain.succeed_for = 10_000
    recovered = Switcher(opencode_auth_path=auth_path, data_root=data_root, platform=Platform.MACOS)
    assert recovered.secrets.get("openai:work") == work_secret_before


def test_pristine_snapshot_written_once(switcher):
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-a"))
    switcher.add_account("a")
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-b"))
    switcher.add_account("b")

    switcher.use_account("a")
    first_pristine = backup.read_pristine(switcher.data_root)
    assert first_pristine["openai"]["accountId"] == "acct-b"  # whatever was live at first use

    switcher.use_account("b")
    assert backup.read_pristine(switcher.data_root) == first_pristine  # unchanged


def test_bak_reflects_most_recent_pre_switch_state(switcher):
    _setup_two_accounts(switcher)
    switcher.use_account("a")
    switcher.use_account("b")
    bak = backup.read_bak(switcher.data_root)
    assert bak["openai"]["accountId"] == "acct-a"  # state right before switching to b


# -- Failure injection: a failed switch must never leave OpenCode with
# truncated/invalid/mismatched auth.json, and the original account must
# stay recoverable. --


def test_failure_before_backup_leaves_auth_untouched(switcher, monkeypatch):
    _setup_two_accounts(switcher)
    switcher.use_account("a")
    original = switcher.opencode_auth_path.read_text()

    monkeypatch.setattr(backup, "write_bak", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        switcher.use_account("b")

    assert switcher.opencode_auth_path.read_text() == original


def test_failure_during_secret_store_sync_write_leaves_auth_untouched(switcher, monkeypatch):
    _setup_two_accounts(switcher)
    switcher.use_account("a")
    # Rotate a's live token so the switch attempts a sync-back put().
    write_auth(switcher.opencode_auth_path, oauth_entry(account_id="acct-a", refresh="ra-rotated"))
    original = switcher.opencode_auth_path.read_text()

    monkeypatch.setattr(switcher.secrets, "put", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        switcher.use_account("b")

    assert switcher.opencode_auth_path.read_text() == original


def test_failure_during_temp_file_write_leaves_auth_untouched(switcher, monkeypatch):
    """Fail specifically on auth.json's own atomic write (not the earlier
    .bak write, which also happens to serialize via the same atomic_write_json
    helper) by only breaking opencode_auth's write, not backup's."""
    _setup_two_accounts(switcher)
    switcher.use_account("a")
    original = switcher.opencode_auth_path.read_text()

    monkeypatch.setattr(opencode_auth, "atomic_write_json", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        switcher.use_account("b")

    assert switcher.opencode_auth_path.read_text() == original
    assert list(switcher.opencode_auth_path.parent.glob(".auth.*.tmp")) == []


def test_failure_before_rename_leaves_auth_untouched(switcher, monkeypatch):
    """Fail the chmod that immediately precedes auth.json's atomic rename
    (not the .bak file's own chmod, a different call to the same function)."""
    _setup_two_accounts(switcher)
    switcher.use_account("a")
    original = switcher.opencode_auth_path.read_text()

    real_chmod = os.chmod

    def selective_chmod(path, mode, *a, **k):
        if str(path).endswith(".tmp") and Path(path).parent == switcher.opencode_auth_path.parent:
            raise RuntimeError("boom")
        return real_chmod(path, mode, *a, **k)

    monkeypatch.setattr("os.chmod", selective_chmod)
    with pytest.raises(RuntimeError):
        switcher.use_account("b")

    assert switcher.opencode_auth_path.read_text() == original
    assert list(switcher.opencode_auth_path.parent.glob(".auth.*.tmp")) == []


def test_failure_after_replacement_rolls_back_auth(switcher, monkeypatch):
    """registry.set_active fails *after* auth.json was already swapped to
    b's content -- the transaction must roll auth.json back to a's content."""
    _setup_two_accounts(switcher)
    switcher.use_account("a")
    original = switcher.opencode_auth_path.read_text()

    monkeypatch.setattr(switcher.registry, "set_active", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        switcher.use_account("b")

    assert switcher.opencode_auth_path.read_text() == original  # rolled back, not left as b
    assert live_openai(switcher.opencode_auth_path)["accountId"] == "acct-a"


FRACTIONAL_EXPIRY = 1_787_739_643_640.0754


def test_switch_heals_a_stored_fractional_expiry(switcher):
    """A record stored by an older version carries a float `expires`
    (time.time() * 1000). OpenCode types `expires` as NonNegativeInt and
    drops undecodable entries without an error, so splicing that float back
    into auth.json makes the whole provider disappear from OpenCode. The
    switch must write an integer instead, with no manual repair step.

    `a` is made live first so the sync-back attributes the live record to
    `a` and leaves `b`'s stored record alone -- switching to whichever
    account is already live would re-capture the live record over the
    injected one and exercise self-switch sync-back instead of activation
    of a stored record.
    """
    _setup_two_accounts(switcher)  # leaves b live
    switcher.use_account("a")
    switcher.secrets.put("openai:b", json.dumps(oauth_entry(account_id="acct-b", refresh="rb", expires=FRACTIONAL_EXPIRY)))

    switcher.use_account("b")

    live = live_openai(switcher.opencode_auth_path)
    assert live["accountId"] == "acct-b"  # the stored record really is the one that landed
    assert live["expires"] == int(FRACTIONAL_EXPIRY)
    assert isinstance(live["expires"], int)
    assert '"expires": 1787739643640,' in switcher.opencode_auth_path.read_text()


def test_switch_leaves_the_stored_fractional_expiry_in_place(switcher):
    """Normalization is a publication step, not a rewrite of stored state:
    only what OpenCode reads is constrained."""
    _setup_two_accounts(switcher)
    switcher.use_account("a")
    switcher.secrets.put("openai:b", json.dumps(oauth_entry(account_id="acct-b", refresh="rb", expires=FRACTIONAL_EXPIRY)))

    switcher.use_account("b")

    assert json.loads(switcher.secrets.get("openai:b"))["expires"] == FRACTIONAL_EXPIRY
