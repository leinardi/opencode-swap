"""Real concurrency: two independent Switcher instances (standing in for two
`opencode-swap` CLI processes sharing the same data_root) racing to switch
accounts at the same time. FileLock's flock() is per-open-file-description,
so two Switcher instances each opening their own lock fd faithfully exercise
the same contention two real OS processes would hit -- threads are used
here only for test speed/reliability, not because the semantics differ.
"""

import json
import threading
import time

import pytest

from opencode_swap.models import Platform
from opencode_swap.switcher import Switcher
from tests.helpers import make_jwt


def oauth_entry(account_id, refresh):
    return {
        "type": "oauth",
        "refresh": refresh,
        "access": make_jwt({"chatgpt_account_id": account_id}),
        "expires": int((time.time() + 3600) * 1000),
        "accountId": account_id,
    }


def write_auth(path, openai_entry):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"openai": openai_entry}))


def make_switcher(auth_path, data_root):
    return Switcher(opencode_auth_path=auth_path, data_root=data_root, platform=Platform.UNKNOWN)


@pytest.fixture
def paths(tmp_path):
    auth_path = tmp_path / "opencode" / "auth.json"
    data_root = tmp_path / "opencode-swap"
    return auth_path, data_root


def test_two_switcher_instances_racing_use_serialize_without_corruption(paths):
    auth_path, data_root = paths
    setup = make_switcher(auth_path, data_root)
    write_auth(auth_path, oauth_entry("acct-a", "ra"))
    setup.add_account("a")
    write_auth(auth_path, oauth_entry("acct-b", "rb"))
    setup.add_account("b")

    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def worker(key, target):
        switcher = make_switcher(auth_path, data_root)
        switcher.lock.timeout = 5.0
        barrier.wait()
        try:
            results[key] = switcher.use_account(target).name
        except Exception as exc:
            results[key] = exc

    t1 = threading.Thread(target=worker, args=("t1", "a"))
    t2 = threading.Thread(target=worker, args=("t2", "b"))
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    for key, outcome in results.items():
        assert not isinstance(outcome, Exception), f"{key} raised: {outcome}"

    # Final live state is valid, complete JSON matching exactly one account
    # -- never a truncated file or a blend of both.
    final = json.loads(auth_path.read_text())
    assert final["openai"]["accountId"] in ("acct-a", "acct-b")
    if final["openai"]["accountId"] == "acct-a":
        assert final["openai"]["refresh"] == "ra"
    else:
        assert final["openai"]["refresh"] == "rb"

    # No leftover temp files from either writer.
    assert list(auth_path.parent.glob(".auth.*.tmp")) == []
    assert list((data_root / "backups").glob(".*.tmp")) == []
    assert list((data_root / "secrets").glob(".*.tmp")) == []


def test_many_concurrent_switches_leave_consistent_final_state(paths):
    """Ten threads hammering use_account("a")/("b") alternately: whatever
    the final winner is, auth.json must always be valid JSON for exactly
    one of the two accounts -- never corrupted, never mixed."""
    auth_path, data_root = paths
    setup = make_switcher(auth_path, data_root)
    write_auth(auth_path, oauth_entry("acct-a", "ra"))
    setup.add_account("a")
    write_auth(auth_path, oauth_entry("acct-b", "rb"))
    setup.add_account("b")

    barrier = threading.Barrier(10)
    errors = []
    errors_lock = threading.Lock()

    def worker(i):
        switcher = make_switcher(auth_path, data_root)
        switcher.lock.timeout = 10.0
        target = "a" if i % 2 == 0 else "b"
        barrier.wait()
        try:
            switcher.use_account(target)
        except Exception as exc:
            with errors_lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == []
    final = json.loads(auth_path.read_text())  # must parse cleanly
    assert final["openai"]["accountId"] in ("acct-a", "acct-b")
    assert list(auth_path.parent.glob(".auth.*.tmp")) == []
