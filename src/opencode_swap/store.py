"""Secure per-account secret storage, and the non-secret account registry.

SecretStore: macOS Keychain or private file routing.

- macOS: ``/usr/bin/security`` CLI (macos_keychain.py) — pinned binary so
  creator == reader across interpreter upgrades (ported from claude-swap).
- Linux: 0600 files under a 0700 directory, matching OpenCode's own
  filesystem trust boundary without Secret Service unlock prompts or D-Bus
  availability requirements.
- File backend is obfuscation (base64), not encryption — same as
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
import stat
from contextlib import suppress
from pathlib import Path

from opencode_swap import macos_keychain
from opencode_swap.atomic import atomic_write_bytes, atomic_write_json, atomic_write_json_exclusive
from opencode_swap.exceptions import RegistryError, SecretStoreError
from opencode_swap.models import AccountKey, AccountMeta, JsonObject, Platform, normalize_account_name, normalize_provider_id

SERVICE_NAME = "opencode-swap"
REGISTRY_VERSION = 2
REGISTRY_V1_BACKUP = "registry.v1.json.bak"
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
        self._use_file_backend = self._platform is not Platform.MACOS
        self._backend_errors: tuple[type[BaseException], ...] = ()
        if self._platform is Platform.MACOS:
            self._backend_errors = macos_keychain.KEYCHAIN_ERRORS

    @property
    def backend_name(self) -> str:
        if self._use_file_backend:
            return "file"
        return "keychain"

    def _pin_file_backend(self) -> None:
        self._use_file_backend = True

    def _put_os(self, key: str, value: str) -> None:
        macos_keychain.set_password(SERVICE_NAME, key, value)

    def _get_os(self, key: str) -> str | None:
        return macos_keychain.get_password(SERVICE_NAME, key)

    def _delete_os(self, key: str) -> None:
        macos_keychain.delete_password(SERVICE_NAME, key)

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
            if self._platform is Platform.MACOS:
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
            self._delete_file_checked(key)
            return
        if self._platform is Platform.MACOS:
            raise SecretStoreError("cannot confirm deletion from the OS credential store while it is unavailable")
        self._delete_file_checked(key)

    def _delete_file_checked(self, key: str) -> None:
        try:
            self._delete_file(key)
        except OSError as exc:
            raise SecretStoreError("could not delete stored file credential") from exc


class Registry:
    """registry.json: non-secret account metadata."""

    def __init__(self, path: Path):
        self._path = path

    @staticmethod
    def _verify_existing_v1_backup(path: Path, data: JsonObject) -> None:
        try:
            backup_stat = path.lstat()
            if not stat.S_ISREG(backup_stat.st_mode):
                raise RegistryError(f"existing registry migration backup at {path} is not a regular file")
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RegistryError(f"could not verify existing registry migration backup at {path}") from exc
        if previous != data:
            raise RegistryError(f"refusing registry migration because {path} contains a different backup")
        path.chmod(0o600)

    def migrate(self) -> bool:
        """Atomically migrate registry v1; secret-store keys already use provider scope."""
        if not self._path.exists():
            return False
        data = self._read_json()
        if data.get("version") == REGISTRY_VERSION:
            return False
        if data.get("version") != 1 or set(data) != {"version", "active", "accounts"}:
            raise RegistryError(f"{self._path} has an unsupported registry version")
        old_accounts = data.get("accounts")
        old_active = data.get("active")
        if not isinstance(old_accounts, dict) or (old_active is not None and not isinstance(old_active, str)):
            raise RegistryError(f"{self._path} does not look like a valid opencode-swap registry")

        nested: JsonObject = {}
        active: JsonObject = {}
        for name, raw_meta in old_accounts.items():
            if not isinstance(name, str):
                raise RegistryError(f"{self._path} has an invalid account name")
            meta = AccountMeta.from_dict(name, raw_meta)
            try:
                normalize_provider_id(meta.provider)
            except ValueError as exc:
                raise RegistryError(f"{self._path} has an invalid provider id") from exc
            provider_accounts = nested.setdefault(meta.provider, {})
            assert isinstance(provider_accounts, dict)
            provider_accounts[name] = meta.to_dict()
            if old_active == name:
                active[meta.provider] = name
        if old_active is not None and not active:
            raise RegistryError(f"{self._path} has an invalid active account")

        backup_path = self._path.with_name(REGISTRY_V1_BACKUP)
        try:
            atomic_write_json_exclusive(backup_path, data)
        except FileExistsError:
            self._verify_existing_v1_backup(backup_path, data)
        self._save({"version": REGISTRY_VERSION, "active": active, "accounts": nested})
        return True

    def _read_json(self) -> JsonObject:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RegistryError(f"could not read registry at {self._path}: {exc}") from exc
        if not isinstance(data, dict):
            raise RegistryError(f"{self._path} does not look like a valid opencode-swap registry")
        return data

    def _load(self) -> JsonObject:
        if not self._path.exists():
            return {"version": REGISTRY_VERSION, "active": {}, "accounts": {}}
        data = self._read_json()
        if not isinstance(data, dict) or set(data) != {"version", "active", "accounts"}:
            raise RegistryError(f"{self._path} does not look like a valid opencode-swap registry")
        if type(data["version"]) is not int or data["version"] != REGISTRY_VERSION:
            raise RegistryError(f"{self._path} has an unsupported registry version")
        if not isinstance(data["accounts"], dict) or not isinstance(data["active"], dict):
            raise RegistryError(f"{self._path} does not look like a valid opencode-swap registry")
        accounts = data["accounts"]
        active = data["active"]
        assert isinstance(accounts, dict) and isinstance(active, dict)
        for provider_id, provider_accounts in accounts.items():
            try:
                normalize_provider_id(provider_id)
            except (TypeError, ValueError) as exc:
                raise RegistryError(f"{self._path} has an invalid provider id") from exc
            if not isinstance(provider_accounts, dict):
                raise RegistryError(f"{self._path} has invalid provider accounts")
            for name, meta in provider_accounts.items():
                parsed = AccountMeta.from_dict(name, meta)
                if parsed.provider != provider_id:
                    raise RegistryError(f"registry account {name!r} has mismatched provider")
        for provider_id, name in active.items():
            provider_accounts = accounts.get(provider_id)
            if not isinstance(name, str) or not isinstance(provider_accounts, dict) or name not in provider_accounts:
                raise RegistryError(f"{self._path} has an invalid active account")
        return data

    def _save(self, data: JsonObject) -> None:
        atomic_write_json(self._path, data)

    def scoped_accounts(self, provider_id: str | None = None) -> dict[AccountKey, AccountMeta]:
        data = self._load()
        accounts = data["accounts"]
        assert isinstance(accounts, dict)
        result: dict[AccountKey, AccountMeta] = {}
        for stored_provider, provider_accounts in accounts.items():
            if provider_id is not None and stored_provider != provider_id:
                continue
            assert isinstance(stored_provider, str) and isinstance(provider_accounts, dict)
            for name, meta in provider_accounts.items():
                assert isinstance(name, str)
                result[(stored_provider, name)] = AccountMeta.from_dict(name, meta)
        return result

    def accounts(self, provider_id: str | None = None) -> dict[str, AccountMeta]:
        """Return name-keyed accounts for one provider; defaults to OpenAI."""
        selected_provider = provider_id or "openai"
        return {name: meta for (_provider, name), meta in self.scoped_accounts(selected_provider).items()}

    def get_active(self, provider_id: str = "openai") -> str | None:
        active = self._load().get("active")
        value = active.get(provider_id) if isinstance(active, dict) else None
        return value if isinstance(value, str) else None

    def upsert_account(self, meta: AccountMeta) -> None:
        data = self._load()
        accounts = data["accounts"]
        assert isinstance(accounts, dict)
        provider_accounts = accounts.setdefault(meta.provider, {})
        assert isinstance(provider_accounts, dict)
        provider_accounts[meta.name] = meta.to_dict()
        self._save(data)

    def add_accounts(self, metas: list[AccountMeta]) -> None:
        """Add several new accounts in one atomic registry publication."""
        data = self._load()
        accounts = data["accounts"]
        assert isinstance(accounts, dict)
        collisions = [meta.name for meta in metas if isinstance(accounts.get(meta.provider), dict) and meta.name in accounts[meta.provider]]
        if collisions:
            raise RegistryError(f"account already exists: {collisions[0]}")
        for meta in metas:
            provider_accounts = accounts.setdefault(meta.provider, {})
            assert isinstance(provider_accounts, dict)
            provider_accounts[meta.name] = meta.to_dict()
        self._save(data)

    def upsert_accounts(self, metas: list[AccountMeta]) -> None:
        """Add or replace several accounts in one atomic registry publication."""
        data = self._load()
        accounts = data["accounts"]
        assert isinstance(accounts, dict)
        for meta in metas:
            provider_accounts = accounts.setdefault(meta.provider, {})
            assert isinstance(provider_accounts, dict)
            provider_accounts[meta.name] = meta.to_dict()
        self._save(data)

    def remove_account(self, name: str, provider_id: str = "openai") -> None:
        data = self._load()
        accounts = data["accounts"]
        assert isinstance(accounts, dict)
        provider_accounts = accounts.get(provider_id)
        if isinstance(provider_accounts, dict):
            provider_accounts.pop(name, None)
            if not provider_accounts:
                accounts.pop(provider_id, None)
        active = data["active"]
        assert isinstance(active, dict)
        if active.get(provider_id) == name:
            active.pop(provider_id, None)
        self._save(data)

    def rename_account(self, old: str, new: str, provider_id: str = "openai") -> None:
        data = self._load()
        accounts = data["accounts"]
        assert isinstance(accounts, dict)
        provider_accounts = accounts.get(provider_id)
        if not isinstance(provider_accounts, dict) or old not in provider_accounts:
            raise RegistryError(f"no such account: {old}")
        if new in provider_accounts:
            raise RegistryError(f"account already exists: {new}")
        provider_accounts[new] = provider_accounts.pop(old)
        active = data["active"]
        assert isinstance(active, dict)
        if active.get(provider_id) == old:
            active[provider_id] = new
        self._save(data)

    def set_active(self, name: str | None, provider_id: str = "openai") -> None:
        data = self._load()
        accounts = data["accounts"]
        assert isinstance(accounts, dict)
        provider_accounts = accounts.get(provider_id)
        if name is not None and (not isinstance(provider_accounts, dict) or name not in provider_accounts):
            raise RegistryError(f"no such account: {name}")
        active = data["active"]
        assert isinstance(active, dict)
        if name is None:
            active.pop(provider_id, None)
        else:
            active[provider_id] = name
        self._save(data)
