# MIT License
#
# Copyright (c) 2026 Roberto Leinardi
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import threading
import time

import pytest

from opencode_swap.exceptions import LockError
from opencode_swap.locking import FileLock


def test_acquire_and_release(tmp_path):
    lock = FileLock(tmp_path / ".lock")
    assert lock.acquire() is True
    lock.release()


def test_second_acquire_blocked_until_release(tmp_path):
    path = tmp_path / ".lock"
    first = FileLock(path)
    assert first.acquire() is True

    second = FileLock(path, timeout=0.3)
    assert second.acquire() is False  # first still holds it

    first.release()
    assert second.acquire() is True
    second.release()


def test_context_manager_raises_on_contention(tmp_path):
    path = tmp_path / ".lock"
    first = FileLock(path)
    first.acquire()
    try:
        with pytest.raises(LockError), FileLock(path, timeout=0.2):
            pass
    finally:
        first.release()


def test_context_manager_releases_on_exit(tmp_path):
    path = tmp_path / ".lock"
    with FileLock(path):
        pass
    # lock is free again
    other = FileLock(path)
    assert other.acquire() is True
    other.release()


def test_concurrent_threads_never_hold_lock_simultaneously(tmp_path):
    """Real concurrency proof (not just sequential acquire/release): five
    threads race for the same lock file; flock()'s mutual exclusion is
    per-open-file-description, so this exercises the exact kernel primitive
    two separate `opencode-swap` processes would contend on."""
    path = tmp_path / ".lock"
    guard = threading.Lock()
    state = {"active": 0, "max_active": 0, "runs": 0}

    def worker():
        lock = FileLock(path, timeout=5.0)
        with lock:
            with guard:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            time.sleep(0.02)
            with guard:
                state["active"] -= 1
                state["runs"] += 1

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert state["runs"] == 5  # all completed, none deadlocked/timed out
    assert state["max_active"] == 1  # never more than one holder at a time
