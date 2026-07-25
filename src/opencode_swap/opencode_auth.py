"""Whole-file read/validate/atomic-write of OpenCode's auth.json.

Generic across providers — a Provider only interprets the one key it owns
(see providers/base.py). This module never inspects provider-specific
fields; it just guarantees a safe, atomic round-trip of the file OpenCode
itself writes non-atomically and without a lock (verified:
opencode/packages/core/src/fs-util.ts:110-114 truncate+write then a separate
chmod, no temp file, no rename; opencode/packages/opencode/src/auth/index.ts
:75-79 does an unlocked read-merge-write). opencode-swap holds itself to a
higher bar: 0600 temp file in the same directory, then os.replace — the only
publish point, so a crash mid-write never leaves auth.json truncated.
"""

from __future__ import annotations

import json
from pathlib import Path

from opencode_swap.atomic import atomic_write_json
from opencode_swap.exceptions import AuthFileError
from opencode_swap.models import JsonObject


def read_auth(path: Path) -> JsonObject:
    """Read and parse auth.json. Raises AuthFileError if missing/malformed.

    An absent file is a distinct, expected state (OpenCode with no accounts
    configured yet) — callers that want to treat "no file" as "empty auth"
    should catch AuthFileError and check path.exists() themselves; this
    function never silently invents an empty dict, since a genuinely
    unreadable file (permissions, corruption) must not look identical to
    "nothing here yet."
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise AuthFileError(f"auth.json not found at {path}") from exc
    except OSError as exc:
        raise AuthFileError(f"could not read {path}: {exc}") from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AuthFileError(f"{path} is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise AuthFileError(f"{path} does not contain a JSON object at the top level")

    return data


def atomic_write_auth(path: Path, data: JsonObject) -> None:
    """Write auth.json atomically: 0600 temp file in the same dir, then rename."""
    atomic_write_json(path, data)
