"""Recovery snapshots of OpenCode's auth.json, kept under
``<data_root>/backups/``. All files 0600.

- ``auth.json.bak`` — the live auth.json content immediately before the most
  recent switch. Lets a user manually recover if opencode-swap's own
  transaction rollback (see switcher.py) can't run (e.g. opencode-swap was
  killed mid-operation).
- ``auth.json.pristine`` — the very first live auth.json content ever seen,
  written once and never overwritten. The ultimate fallback: what OpenCode's
  auth looked like before opencode-swap ever touched it.
- ``unclaimed-<provider>-<timestamp>.json`` — a live provider record that
  didn't belong to any managed account at switch time (an external
  `opencode auth login` opencode-swap wasn't told about). Preserved instead
  of silently overwritten so nothing is lost.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import cast

from opencode_swap.atomic import atomic_write_json
from opencode_swap.models import JsonObject

BACKUP_DIRNAME = "backups"
BAK_FILENAME = "auth.json.bak"
PRISTINE_FILENAME = "auth.json.pristine"


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
    path = _backups_dir(data_root) / BAK_FILENAME
    if not path.exists():
        return None
    return cast(JsonObject, json.loads(path.read_text(encoding="utf-8")))


def write_pristine_if_absent(data_root: Path, auth: JsonObject) -> None:
    path = _backups_dir(data_root) / PRISTINE_FILENAME
    if path.exists():
        return
    atomic_write_json(path, auth)


def read_pristine(data_root: Path) -> JsonObject | None:
    path = _backups_dir(data_root) / PRISTINE_FILENAME
    if not path.exists():
        return None
    return cast(JsonObject, json.loads(path.read_text(encoding="utf-8")))


def write_unclaimed(data_root: Path, provider_id: str, record: JsonObject) -> Path:
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = _backups_dir(data_root) / f"unclaimed-{provider_id}-{timestamp}.json"
    atomic_write_json(path, record)
    return path
