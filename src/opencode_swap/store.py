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
import binascii
import hashlib
import json
import os
from contextlib import suppress
from pathlib import Path
from typing import cast

from opencode_swap import macos_keychain
from opencode_swap.atomic import atomic_write_bytes, atomic_write_json
from opencode_swap.exceptions import RegistryError, SecretStoreError
from opencode_swap.models import AccountMeta, JsonObject, Platform, normalize_account_name

SERVICE_NAME = "opencode-swap"
REGISTRY_VERSION = 1
_LEGACY_KEY_PREFIX = "openai:"
_FALLBACK_NAME_MAX = 255


def _safe_filename(key: str) -> str:
    return f"v2-{hashlib.sha256(key.encode('utf-8')).hexdigest()}.enc"


def _legacy_filename(key: str) -> str:
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

    def _legacy_file_path(self, key: str) -> Path:
        return self._dir / _legacy_filename(key)

    def _has_legacy_fallback(self, key: str) -> bool:
        # v1 only supported normalized OpenAI account names. Extra separators
        # could alias another legacy file, and overlong names cannot be
        # probed on common filesystems even though v2's digest handles them.
        if not key.startswith(_LEGACY_KEY_PREFIX):
            return False
        name = key.removeprefix(_LEGACY_KEY_PREFIX)
        try:
            if normalize_account_name(name) != name:
                return False
        except ValueError:
            return False
        name_max_path = self._dir
        while not name_max_path.exists() and name_max_path != name_max_path.parent:
            name_max_path = name_max_path.parent
        try:
            name_max = os.pathconf(name_max_path, "PC_NAME_MAX")
        except (OSError, ValueError):
            name_max = _FALLBACK_NAME_MAX
        return len(_legacy_filename(key).encode("utf-8")) <= name_max

    def _put_file(self, key: str, value: str) -> None:
        encoded = base64.b64encode(value.encode("utf-8"))
        # atomic_write_bytes creates missing parents with the process umask;
        # tighten our private directory before publishing any secret into it.
        self._dir.mkdir(parents=True, exist_ok=True)
        self._dir.chmod(0o700)
        atomic_write_bytes(self._file_path(key), encoded, mode=0o600)
        if self._has_legacy_fallback(key):
            # v2 now wins every read, so a failed legacy cleanup cannot make
            # a stale credential authoritative again.
            with suppress(OSError):
                self._legacy_file_path(key).unlink(missing_ok=True)

    def _get_file(self, key: str) -> str | None:
        try:
            encoded = self._file_path(key).read_bytes()
        except FileNotFoundError:
            if not self._has_legacy_fallback(key):
                return None
            try:
                encoded = self._legacy_file_path(key).read_bytes()
            except FileNotFoundError:
                return None
            except OSError as exc:
                raise SecretStoreError("could not read stored file credential") from exc
            return self._decode_file_value(encoded)
        except OSError as exc:
            raise SecretStoreError("could not read stored file credential") from exc
        return self._decode_file_value(encoded)

    @staticmethod
    def _decode_file_value(encoded: bytes) -> str:
        try:
            return base64.b64decode(encoded, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise SecretStoreError("stored file credential is corrupt") from exc

    def _delete_file(self, key: str) -> None:
        self._file_path(key).unlink(missing_ok=True)
        if self._has_legacy_fallback(key):
            self._legacy_file_path(key).unlink(missing_ok=True)

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

    def get_confirmed(self, key: str) -> str | None:
        """Read `key`, refusing to report absence when OS state is unavailable."""
        file_value = self._get_file(key)
        if file_value is not None:
            return file_value
        if self._use_file_backend:
            if self._platform in (Platform.MACOS, Platform.LINUX):
                raise SecretStoreError("cannot confirm credential absence while OS credential store is unavailable")
            return None
        try:
            return self._get_os(key)
        except self._backend_errors as exc:
            self._pin_file_backend()
            raise SecretStoreError("could not confirm credential absence from OS credential store") from exc

    def put(self, key: str, value: str) -> None:
        if not self._use_file_backend:
            try:
                self._put_os(key, value)
                try:
                    self._delete_file(key)  # reconcile: drop any stale fallback copy
                except OSError:
                    # OS write committed. Publish v2 fallback so a stale
                    # legacy file cannot win subsequent reads.
                    self._put_file(key, value)
                return
            except self._backend_errors:
                self._pin_file_backend()
        self._put_file(key, value)

    def delete(self, key: str) -> None:
        if not self._use_file_backend:
            try:
                self._delete_os(key)
            except self._backend_errors as exc:
                self._pin_file_backend()
                raise SecretStoreError("could not confirm deletion from the OS credential store") from exc
            self._delete_file(key)
            return
        if self._platform in (Platform.MACOS, Platform.LINUX):
            raise SecretStoreError("cannot confirm deletion from the OS credential store while it is unavailable")
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
        if not isinstance(data, dict) or set(data) != {"version", "active", "accounts"}:
            raise RegistryError(f"{self._path} does not look like a valid opencode-swap registry")
        if type(data["version"]) is not int or data["version"] != REGISTRY_VERSION:
            raise RegistryError(f"{self._path} has an unsupported registry version")
        if not isinstance(data["accounts"], dict):
            raise RegistryError(f"{self._path} does not look like a valid opencode-swap registry")
        active = data["active"]
        if active is not None:
            try:
                valid_active = isinstance(active, str) and normalize_account_name(active) == active and active in data["accounts"]
            except ValueError:
                valid_active = False
            if not valid_active:
                raise RegistryError(f"{self._path} has an invalid active account")
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

    def add_accounts(self, metas: list[AccountMeta]) -> None:
        """Add several new accounts in one atomic registry publication."""
        data = self._load()
        accounts = data["accounts"]
        assert isinstance(accounts, dict)
        collisions = [meta.name for meta in metas if meta.name in accounts]
        if collisions:
            raise RegistryError(f"account already exists: {collisions[0]}")
        for meta in metas:
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
