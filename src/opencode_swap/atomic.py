"""Shared atomic-write primitives: 0600 temp file in the target's own
directory, then os.replace. The only publish point is the rename, so a crash
mid-write never leaves a truncated or partially-written file behind.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

DEFAULT_MODE = 0o600


def atomic_write_bytes(path: Path, data: bytes, mode: int = DEFAULT_MODE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, data: Any, mode: int = DEFAULT_MODE, indent: int = 2) -> None:
    atomic_write_bytes(path, json.dumps(data, indent=indent, allow_nan=False).encode("utf-8"), mode=mode)


def atomic_write_json_exclusive(path: Path, data: Any, mode: int = DEFAULT_MODE, indent: int = 2) -> None:
    """Atomically create JSON only if `path` does not already exist."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(json.dumps(data, indent=indent, allow_nan=False).encode("utf-8"))
        os.chmod(tmp_path, mode)
        os.link(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    # The hard link already published the backup; a leftover private temp
    # file must not turn that successful publication into a failure.
    with suppress(OSError):
        tmp_path.unlink()
