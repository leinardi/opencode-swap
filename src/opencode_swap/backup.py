"""Recovery snapshots of OpenCode's auth.json, kept under
``<data_root>/backups/``. All files 0600.

- ``auth.json.bak`` — the live auth.json content immediately before the most
  recent switch. Lets a user manually recover if opencode-swap's own
  transaction rollback (see switcher.py) can't run (e.g. opencode-swap was
  killed mid-operation).
- ``auth.json.pristine`` — the very first live auth.json content ever seen,
  written once and never overwritten. The ultimate fallback: what OpenCode's
  auth looked like before opencode-swap ever touched it.
- ``unclaimed-<provider>-<timestamp>-<suffix>.json`` — a live provider record that
  didn't belong to any managed account at switch time (an external
  `opencode auth login` opencode-swap wasn't told about). Preserved instead
  of silently overwritten so nothing is lost.
"""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import cast

from opencode_swap.atomic import atomic_write_json, atomic_write_json_exclusive
from opencode_swap.exceptions import BackupError
from opencode_swap.models import JsonObject

BACKUP_DIRNAME = "backups"
BAK_FILENAME = "auth.json.bak"
PRISTINE_FILENAME = "auth.json.pristine"
RESTORE_SNAPSHOT_FILENAME = "auth.json.restore"


def _backups_dir(data_root: Path) -> Path:
    """Return the backups dir, creating it with 0700 if needed.

    atomic_write_json's own mkdir doesn't set a mode (it's shared with
    OpenCode's own auth.json directory, which opencode-swap must not
    presumptuously chmod) — so opencode-swap's own subdirectories are
    responsible for locking themselves down, same as store.py's secrets/.
    """
    d = data_root / BACKUP_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)
    return d


def write_bak(data_root: Path, auth: JsonObject) -> None:
    atomic_write_json(_backups_dir(data_root) / BAK_FILENAME, auth)


def read_bak(data_root: Path) -> JsonObject | None:
    return _read_snapshot(_backups_dir(data_root) / BAK_FILENAME)


def write_pristine_if_absent(data_root: Path, auth: JsonObject) -> None:
    path = _backups_dir(data_root) / PRISTINE_FILENAME
    if path.exists():
        return
    atomic_write_json(path, auth)


def read_pristine(data_root: Path) -> JsonObject | None:
    return _read_snapshot(_backups_dir(data_root) / PRISTINE_FILENAME)


def _read_snapshot(path: Path) -> JsonObject | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError(f"could not read backup at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise BackupError(f"backup at {path} does not contain a JSON object at the top level")
    return cast(JsonObject, data)


def write_restore_snapshot(data_root: Path, auth: JsonObject) -> None:
    """Keep restore source durable while .bak is chained to current live state."""
    atomic_write_json(_backups_dir(data_root) / RESTORE_SNAPSHOT_FILENAME, auth)


def read_restore_snapshot(data_root: Path) -> JsonObject | None:
    return _read_snapshot(_backups_dir(data_root) / RESTORE_SNAPSHOT_FILENAME)


def remove_restore_snapshot(data_root: Path) -> None:
    (_backups_dir(data_root) / RESTORE_SNAPSHOT_FILENAME).unlink(missing_ok=True)


def write_unclaimed(data_root: Path, provider_id: str, record: JsonObject) -> Path:
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    directory = _backups_dir(data_root)
    # Random suffixes make collisions improbable; exclusive publication makes
    # even a repeated suffix unable to replace another foreign credential.
    for _ in range(100):
        path = directory / f"unclaimed-{provider_id}-{timestamp}-{secrets.token_hex(8)}.json"
        try:
            atomic_write_json_exclusive(path, record)
        except FileExistsError:
            continue
        return path
    raise BackupError("could not allocate a unique unclaimed credential backup")
