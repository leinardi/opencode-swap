"""Secure per-account secret storage, and the non-secret account registry.

SecretStore: keychain/keyring/file routing, with a sticky fallback.

- macOS: ``/usr/bin/security`` CLI (macos_keychain.py) — pinned binary so
  creator == reader across interpreter upgrades (ported from claude-swap).
- Linux: OS keyring via the ``keyring`` library (Secret Service / libsecret),
  falling back to 0600 files when no keyring is available (headless hosts,
  CI, servers). claude-swap has no such fallback on Linux — files only; we
  add it because plaintext-file-only-by-default is exactly the posture the
  new tool is meant to improve on (see opencode-balancer's plaintext
  SQLite).
- Fallback file backend is obfuscation (base64), not encryption — same as
  claude-swap's ``.enc`` files — protected by 0600 file / 0700 dir perms,
  matching (not exceeding) OpenCode's own auth.json trust boundary.

Read/write ordering, ported from claude-swap's credentials.py:

- **File-wins-on-read**: if a fallback file exists for a key, it is
  authoritative — it may be fresher than a stale or currently-unreachable
  OS-backend copy (written during a prior fallback episode).
- **Reconcile-on-write**: after a successful OS-backend write, any stale
  fallback file for that key is deleted.
- **Sticky fallback**: once an OS-backend operation fails during this
  process, this SecretStore instance pins itself to the file backend for
  the rest of its life — a single CLI invocation never flip-flops between
  backends mid-operation.

Registry: registry.json — non-secret account metadata (name, provider,
type, account id, email, which account is active). Never holds tokens.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import cast

from opencode_swap import macos_keychain
from opencode_swap.atomic import atomic_write_bytes, atomic_write_json
from opencode_swap.exceptions import RegistryError
from opencode_swap.models import AccountMeta, JsonObject, Platform

SERVICE_NAME = "opencode-swap"
REGISTRY_VERSION = 1


def _safe_filename(key: str) -> str:
    return key.replace(":", "_").replace("/", "_") + ".enc"


class SecretStore:
    """String key-value secret store. Keys are opaque; callers own their format."""

    def __init__(self, secrets_dir: Path, platform: Platform | None = None):
        self._dir = secrets_dir
        self._platform = platform or Platform.detect()
        self._use_file_backend = self._platform not in (Platform.MACOS, Platform.LINUX)
        self._backend_errors: tuple[type[BaseException], ...] = ()
        if self._platform is Platform.MACOS:
            self._backend_errors = macos_keychain.KEYCHAIN_ERRORS
        elif self._platform is Platform.LINUX:
            import keyring.errors  # noqa: PLC0415

            self._backend_errors = (keyring.errors.KeyringError,)

    @property
    def backend_name(self) -> str:
        if self._use_file_backend:
            return "file"
        return "keychain" if self._platform is Platform.MACOS else "keyring"

    def _pin_file_backend(self) -> None:
        self._use_file_backend = True

    def _put_os(self, key: str, value: str) -> None:
        if self._platform is Platform.MACOS:
            macos_keychain.set_password(SERVICE_NAME, key, value)
        else:
            import keyring  # noqa: PLC0415

            keyring.set_password(SERVICE_NAME, key, value)

    def _get_os(self, key: str) -> str | None:
        if self._platform is Platform.MACOS:
            return macos_keychain.get_password(SERVICE_NAME, key)
        import keyring  # noqa: PLC0415

        return keyring.get_password(SERVICE_NAME, key)

    def _delete_os(self, key: str) -> None:
        if self._platform is Platform.MACOS:
            macos_keychain.delete_password(SERVICE_NAME, key)
            return
        import keyring  # noqa: PLC0415

        # keyring's delete_password raises PasswordDeleteError for a
        # backend that can't confirm absence, ambiguous with "already
        # absent" — check first so a normal no-op is never misclassified as
        # a broken backend (which would incorrectly pin the file fallback).
        if keyring.get_password(SERVICE_NAME, key) is None:
            return
        keyring.delete_password(SERVICE_NAME, key)

    def _file_path(self, key: str) -> Path:
        return self._dir / _safe_filename(key)

    def _put_file(self, key: str, value: str) -> None:
        encoded = base64.b64encode(value.encode("utf-8"))
        atomic_write_bytes(self._file_path(key), encoded, mode=0o600)
        self._dir.chmod(0o700)

    def _get_file(self, key: str) -> str | None:
        try:
            encoded = self._file_path(key).read_bytes()
        except FileNotFoundError:
            return None
        return base64.b64decode(encoded).decode("utf-8")

    def _delete_file(self, key: str) -> None:
        self._file_path(key).unlink(missing_ok=True)

    def get(self, key: str) -> str | None:
        file_value = self._get_file(key)
        if file_value is not None:
            return file_value
        if self._use_file_backend:
            return None
        try:
            return self._get_os(key)
        except self._backend_errors:
            self._pin_file_backend()
            return None

    def put(self, key: str, value: str) -> None:
        if not self._use_file_backend:
            try:
                self._put_os(key, value)
                self._delete_file(key)  # reconcile: drop any stale fallback copy
                return
            except self._backend_errors:
                self._pin_file_backend()
        self._put_file(key, value)

    def delete(self, key: str) -> None:
        if not self._use_file_backend:
            try:
                self._delete_os(key)
            except self._backend_errors:
                self._pin_file_backend()
        self._delete_file(key)


class Registry:
    """registry.json: non-secret account metadata."""

    def __init__(self, path: Path):
        self._path = path

    def _load(self) -> JsonObject:
        if not self._path.exists():
            return {"version": REGISTRY_VERSION, "active": None, "accounts": {}}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RegistryError(f"could not read registry at {self._path}: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("accounts"), dict):
            raise RegistryError(f"{self._path} does not look like a valid opencode-swap registry")
        return cast(JsonObject, data)

    def _save(self, data: JsonObject) -> None:
        atomic_write_json(self._path, data)

    def accounts(self) -> dict[str, AccountMeta]:
        data = self._load()
        accounts = data["accounts"]
        assert isinstance(accounts, dict)
        return {name: AccountMeta.from_dict(name, meta) for name, meta in accounts.items()}

    def get_active(self) -> str | None:
        active = self._load().get("active")
        return active if isinstance(active, str) else None

    def upsert_account(self, meta: AccountMeta) -> None:
        data = self._load()
        accounts = data["accounts"]
        assert isinstance(accounts, dict)
        accounts[meta.name] = meta.to_dict()
        self._save(data)

    def remove_account(self, name: str) -> None:
        data = self._load()
        accounts = data["accounts"]
        assert isinstance(accounts, dict)
        accounts.pop(name, None)
        if data.get("active") == name:
            data["active"] = None
        self._save(data)

    def rename_account(self, old: str, new: str) -> None:
        data = self._load()
        accounts = data["accounts"]
        assert isinstance(accounts, dict)
        if old not in accounts:
            raise RegistryError(f"no such account: {old}")
        if new in accounts:
            raise RegistryError(f"account already exists: {new}")
        accounts[new] = accounts.pop(old)
        if data.get("active") == old:
            data["active"] = new
        self._save(data)

    def set_active(self, name: str | None) -> None:
        data = self._load()
        accounts = data["accounts"]
        assert isinstance(accounts, dict)
        if name is not None and name not in accounts:
            raise RegistryError(f"no such account: {name}")
        data["active"] = name
        self._save(data)
