"""Shared atomic-write primitives: 0600 temp file in the target's own
directory, fsynced, then os.replace, with the containing directory fsynced
afterward. The only publish point is the rename, so a crash mid-write never
leaves a truncated or partially-written file behind, and the fsyncs mean the
guarantee holds across a power loss, not just a process crash.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

DEFAULT_MODE = 0o600


def _fsync_dir(directory: Path) -> None:
    """Best-effort: make the preceding rename itself durable, not just its
    contents. Suppressed because the publish has already committed by this
    point, and some filesystems (notably network ones) reject fsync on a
    directory fd."""
    with suppress(OSError):
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic_write_bytes(path: Path, data: bytes, mode: int = DEFAULT_MODE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    _fsync_dir(path.parent)


def atomic_write_bytes_exclusive(path: Path, data: bytes, mode: int = DEFAULT_MODE) -> None:
    """Atomically create bytes only if `path` does not already exist."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    reserved = False
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, mode)
        try:
            os.link(tmp_path, path)
        except FileExistsError:
            raise  # exclusivity is the point; never fall back
        except OSError:
            # Destination filesystem has no hardlinks (e.g. a FAT32/exFAT
            # export target). `open(O_CREAT|O_EXCL)` is the only race-safe
            # exclusive-create primitive left without them. Once it reserves
            # `path`, publish tmp_path's already-fsynced content over the
            # reservation with the same atomic os.replace used everywhere
            # else in this module -- so a failure partway through can never
            # leave a truncated file under the real name; at worst it leaves
            # the empty reservation, which the handler below also cleans up.
            os.close(os.open(path, os.O_CREAT | os.O_EXCL, mode))
            reserved = True
            os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        if reserved:
            path.unlink(missing_ok=True)
        raise
    with suppress(OSError):
        tmp_path.unlink()
    _fsync_dir(path.parent)


def atomic_write_json(path: Path, data: Any, mode: int = DEFAULT_MODE, indent: int = 2) -> None:
    atomic_write_bytes(path, json.dumps(data, indent=indent, allow_nan=False).encode("utf-8"), mode=mode)


def atomic_write_json_exclusive(path: Path, data: Any, mode: int = DEFAULT_MODE, indent: int = 2) -> None:
    """Atomically create JSON only if `path` does not already exist."""
    encoded = json.dumps(data, indent=indent, allow_nan=False).encode("utf-8")
    atomic_write_bytes_exclusive(path, encoded, mode=mode)
