"""Cross-process file lock serializing opencode-swap's own invocations.

Ported from claude-swap's locking.py (fcntl.flock on an exclusive,
non-blocking lock file, polled with a timeout); the Windows msvcrt branch is
dropped since opencode-swap v1 targets Linux and macOS only.

This lock has no relationship to OpenCode's own process — it only prevents
two concurrent `opencode-swap` invocations from interleaving writes to the
same account data. See switcher.py for how a *running OpenCode* is handled
(detected and warned about separately, since OpenCode has no cooperative
lock protocol to join).
"""

from __future__ import annotations

import fcntl
import time
from pathlib import Path
from types import TracebackType
from typing import IO, Self

from opencode_swap.exceptions import LockError


class FileLock:
    def __init__(self, lock_path: Path, timeout: float = 10.0):
        self.lock_path = lock_path
        self.timeout = timeout
        self._lock_file: IO[str] | None = None
        self._locked = False

    def acquire(self, timeout: float | None = None) -> bool:
        if timeout is None:
            timeout = self.timeout
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = open(self.lock_path, "w")  # noqa: SIM115
        self._lock_file = lock_file

        start = time.monotonic()
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._locked = True
                return True
            except (BlockingIOError, OSError):
                if time.monotonic() - start > timeout:
                    lock_file.close()
                    self._lock_file = None
                    return False
                time.sleep(0.1)

    def release(self) -> None:
        if self._lock_file and self._locked:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            self._lock_file.close()
            self._lock_file = None
            self._locked = False

    def __enter__(self) -> Self:
        if not self.acquire():
            raise LockError(f"failed to acquire lock at {self.lock_path} within {self.timeout}s — another opencode-swap instance may be running")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
