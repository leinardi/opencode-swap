"""Orchestrates opencode-swap's account operations.

Ties together paths.py (where OpenCode's and our own state live),
opencode_auth.py (safe auth.json I/O), the Provider seam (record
interpretation), and store.py (SecretStore + Registry) into the actual
add/remove/rename/current commands. `use` (the risky part — swapping the
live account, with sync-back and atomic replace) lands separately.

Every mutating operation holds the same FileLock, serializing opencode-swap's
own concurrent invocations (see locking.py). This lock has no relationship
to OpenCode's own process.
"""

from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from opencode_swap import backup, opencode_auth, paths, usage
from opencode_swap.exceptions import AccountExistsError, AuthFileError, OpenCodeSwapError, SchemaError
from opencode_swap.locking import FileLock
from opencode_swap.models import (
    AccountDesc,
    AccountMeta,
    AuthRecord,
    Platform,
    SwitchTransaction,
    Validity,
    normalize_account_name,
)
from opencode_swap.providers import PROVIDERS
from opencode_swap.store import Registry, SecretStore


def _secret_key(provider_id: str, name: str) -> str:
    return f"{provider_id}:{name}"


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Switcher:
    opencode_auth_path: Path
    data_root: Path
    platform: Platform | None = None  # override for tests; None = auto-detect
    registry: Registry = field(init=False)
    secrets: SecretStore = field(init=False)
    lock: FileLock = field(init=False)

    def __post_init__(self) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.data_root.chmod(0o700)
        self.registry = Registry(self.data_root / "registry.json")
        self.secrets = SecretStore(self.data_root / "secrets", platform=self.platform)
        self.lock = FileLock(self.data_root / ".lock")

    @classmethod
    def default(cls) -> Switcher:
        return cls(opencode_auth_path=paths.get_opencode_auth_path(), data_root=paths.get_data_root())

    def _load_record(self, provider_id: str, name: str) -> AuthRecord | None:
        stored = self.secrets.get(_secret_key(provider_id, name))
        if stored is None:
            return None
        meta = self.registry.accounts().get(name)
        record_type = meta.type if meta is not None else json.loads(stored).get("type", "oauth")
        return AuthRecord(type=record_type, raw=json.loads(stored))

    def _find_by_identity(self, provider_id: str, identity: str) -> str | None:
        provider = PROVIDERS[provider_id]
        for name, meta in self.registry.accounts().items():
            if meta.provider != provider_id:
                continue
            record = self._load_record(provider_id, name)
            if record is None:
                continue
            if provider.identity(record) == identity:
                return name
        return None

    def _read_live_record(self, provider_id: str) -> AuthRecord | None:
        """Read + extract the live provider record.

        Raises AuthFileError if auth.json is missing/unreadable/malformed,
        SchemaError if the provider's entry doesn't match a known shape.
        Deliberately unwrapped: add_account/use_account want a friendly
        "run opencode auth login" message for AuthFileError but must let
        SchemaError propagate untouched (fail-safe — never silently
        guessed at); current() treats AuthFileError as "nothing active"
        but must *also* let SchemaError propagate, so an incompatible
        schema is reported rather than masqueraded as "no active account".
        """
        auth = opencode_auth.read_auth(self.opencode_auth_path)
        return PROVIDERS[provider_id].extract(auth)

    def add_account(self, name: str, provider_id: str = "openai") -> AccountMeta:
        name = normalize_account_name(name)
        provider = PROVIDERS[provider_id]

        with self.lock:
            try:
                record = self._read_live_record(provider_id)
            except AuthFileError as exc:
                raise OpenCodeSwapError(f"could not read OpenCode's auth file ({exc}); run `opencode auth login` first") from exc
            if record is None:
                raise OpenCodeSwapError("no active OpenAI account in OpenCode; run `opencode auth login` first")

            identity = provider.identity(record)
            existing_owner = self._find_by_identity(provider_id, identity)

            if existing_owner is not None and existing_owner != name:
                raise AccountExistsError(
                    f"this account is already saved as '{existing_owner}' "
                    f"(use `opencode-swap use {existing_owner}` or "
                    f"`opencode-swap rename {existing_owner} {name}`)"
                )

            existing_meta = self.registry.accounts().get(name)
            if existing_meta is not None and existing_owner is None:
                raise AccountExistsError(f"account name '{name}' is already used by a different account; remove it first or choose another name")

            desc = provider.describe(record)
            added = existing_meta.added if existing_meta is not None else _now_iso()
            meta = AccountMeta(
                name=name,
                provider=provider_id,
                type=record.type,
                account_id=desc.account_id,
                email=desc.email,
                added=added,
            )

            self.secrets.put(_secret_key(provider_id, name), json.dumps(record.raw))
            self.registry.upsert_account(meta)
            self.registry.set_active(name)

        return meta

    def use_account(self, name: str) -> AccountMeta:
        """Switch OpenCode's active account to the saved account `name`.

        Algorithm (see plan §8 for the full rationale):
          1. Sync-back: if the currently-live record belongs to a *different*
             managed account than `name`, capture its (possibly rotated)
             tokens before it's overwritten. If it belongs to no managed
             account at all, stash it under backups/ instead of losing it.
          2. Snapshot the live auth.json to backups/auth.json.bak (and, the
             very first time ever, to backups/auth.json.pristine).
          3. Atomically replace the target provider's entry in auth.json.
          4. Record `name` as the active account in the registry.

        Only step 3 mutates OpenCode's live state, and atomic_write_auth
        guarantees it's all-or-nothing — so a failure anywhere in this
        method either leaves auth.json completely untouched, or (if it fails
        after step 3 succeeded, e.g. in step 4) gets rolled back by
        restoring the pre-switch auth.json content captured in step 2.
        """
        name = normalize_account_name(name)
        with self.lock:
            target_meta = self.registry.accounts().get(name)
            if target_meta is None:
                raise OpenCodeSwapError(f"no such account: {name}")
            provider_id = target_meta.provider
            provider = PROVIDERS[provider_id]

            target_secret = self.secrets.get(_secret_key(provider_id, name))
            if target_secret is None:
                raise OpenCodeSwapError(
                    f"no stored credentials for '{name}' (secret store may be out of sync); "
                    f"try `opencode-swap add {name}` again while that account is active in OpenCode"
                )
            target_record = AuthRecord(type=target_meta.type, raw=json.loads(target_secret))

            auth = opencode_auth.read_auth(self.opencode_auth_path) if self.opencode_auth_path.exists() else {}

            backup.write_pristine_if_absent(self.data_root, auth)
            transaction = SwitchTransaction(original_auth=auth, original_active=self.registry.get_active())

            try:
                live_record = provider.extract(auth)
                if live_record is not None:
                    live_identity = provider.identity(live_record)
                    owner_name = self._find_by_identity(provider_id, live_identity)
                    if owner_name is not None:
                        stored = self.secrets.get(_secret_key(provider_id, owner_name))
                        if stored is None or json.loads(stored) != live_record.raw:
                            self.secrets.put(_secret_key(provider_id, owner_name), json.dumps(live_record.raw))
                            transaction.record_step("sync_captured")
                    else:
                        backup.write_unclaimed(self.data_root, provider_id, live_record.raw)
                        transaction.record_step("unclaimed_stashed")

                backup.write_bak(self.data_root, auth)
                transaction.record_step("bak_written")

                new_auth = provider.splice(auth, target_record)
                opencode_auth.atomic_write_auth(self.opencode_auth_path, new_auth)
                transaction.record_step("auth_written")

                self.registry.set_active(name)
                transaction.record_step("registry_written")
            except BaseException:
                self._rollback(transaction)
                raise

        return target_meta

    def _rollback(self, transaction: SwitchTransaction) -> None:
        if "auth_written" not in transaction.completed_steps:
            return  # live auth.json was never touched; nothing to undo
        try:
            opencode_auth.atomic_write_auth(self.opencode_auth_path, transaction.original_auth)
        except OSError:
            raise OpenCodeSwapError(
                "switch failed partway and automatic rollback of auth.json also failed; "
                f"restore manually from {self.data_root / 'backups' / 'auth.json.bak'}"
            ) from None

    def remove_account(self, name: str, provider_id: str = "openai") -> None:
        name = normalize_account_name(name)
        with self.lock:
            meta = self.registry.accounts().get(name)
            if meta is None:
                raise OpenCodeSwapError(f"no such account: {name}")
            self.secrets.delete(_secret_key(meta.provider, name))
            self.registry.remove_account(name)

    def rename_account(self, old: str, new: str) -> None:
        old = normalize_account_name(old)
        new = normalize_account_name(new)
        with self.lock:
            meta = self.registry.accounts().get(old)
            if meta is None:
                raise OpenCodeSwapError(f"no such account: {old}")
            if self.registry.accounts().get(new) is not None:
                raise AccountExistsError(f"account already exists: {new}")

            secret = self.secrets.get(_secret_key(meta.provider, old))
            if secret is not None:
                self.secrets.put(_secret_key(meta.provider, new), secret)
                self.secrets.delete(_secret_key(meta.provider, old))
            self.registry.rename_account(old, new)

    def restore(self, source: str = "bak", provider_id: str = "openai") -> AccountMeta | None:
        """Restore OpenCode's auth.json from a backup snapshot.

        `source` is "bak" (most recent pre-switch state) or "pristine" (the
        very first live state opencode-swap ever saw). The current live
        content is itself chained into .bak first (if readable) so a
        restore is always undoable by restoring again.

        Returns the managed account that now matches the restored live
        state, or None if it doesn't match any saved account (including
        when the restored data's provider entry can't even be interpreted —
        the restore itself still succeeds; only identification is skipped).
        """
        if source == "bak":
            data = backup.read_bak(self.data_root)
            if data is None:
                raise OpenCodeSwapError("no .bak snapshot found; nothing to restore")
        elif source == "pristine":
            data = backup.read_pristine(self.data_root)
            if data is None:
                raise OpenCodeSwapError("no pristine snapshot found; nothing to restore")
        else:
            raise ValueError(f"unknown restore source: {source!r}")

        with self.lock:
            if self.opencode_auth_path.exists():
                try:
                    current_live = opencode_auth.read_auth(self.opencode_auth_path)
                    backup.write_bak(self.data_root, current_live)
                except AuthFileError:
                    pass  # current file is unreadable -- exactly the case restore exists for

            opencode_auth.atomic_write_auth(self.opencode_auth_path, data)

            provider = PROVIDERS[provider_id]
            try:
                record = provider.extract(data)
            except SchemaError:
                record = None
            owner_name = None
            if record is not None:
                owner_name = self._find_by_identity(provider_id, provider.identity(record))
            with suppress(OpenCodeSwapError):
                self.registry.set_active(owner_name)

        return self.registry.accounts().get(owner_name) if owner_name else None

    def fetch_usage(self, name: str) -> usage.UsageSnapshot | None:
        """Live OpenAI usage lookup for a saved account. None if the
        account has no OAuth record to look up usage for (e.g. an API-key
        account, or a secret store that's out of sync) -- this is a
        distinct "not applicable" case from usage.UsageSnapshot's own
        available=False ("looked it up, the request failed")."""
        meta = self.registry.accounts().get(name)
        if meta is None or meta.provider != "openai" or meta.type != "oauth":
            return None
        record = self._load_record("openai", name)
        if record is None:
            return None
        access = record.raw.get("access")
        if not isinstance(access, str):
            return None
        account_id = record.raw.get("accountId")
        return usage.fetch_openai_oauth_usage(access, account_id if isinstance(account_id, str) else None)

    def account_validity(self, name: str) -> Validity:
        """Validity of a saved account's stored record (OK/EXPIRED/INVALID).

        INVALID here means the secret is missing or unreadable (e.g. deleted
        out-of-band from the secret store) — the registry entry is orphaned.
        """
        meta = self.registry.accounts().get(name)
        if meta is None:
            raise OpenCodeSwapError(f"no such account: {name}")
        record = self._load_record(meta.provider, name)
        if record is None:
            return Validity.INVALID
        return PROVIDERS[meta.provider].validate(record)

    def next_account(self, provider_id: str = "openai") -> AccountMeta:
        """Return next saved account after the live managed account, wrapping around."""
        current, _ = self.current(provider_id)
        if current is None:
            raise OpenCodeSwapError("current OpenCode account is not managed; use `opencode-swap use <name>` first")
        accounts = self.registry.accounts()
        names = sorted(name for name, meta in accounts.items() if meta.provider == provider_id)
        return accounts[names[(names.index(current.name) + 1) % len(names)]]

    def current(self, provider_id: str = "openai") -> tuple[AccountMeta | None, AccountDesc | None]:
        """Return (managed account meta if recognized, live account description).

        (None, None) if OpenCode has no active account for this provider.
        A non-None description with a None meta means OpenCode is logged
        into an account opencode-swap hasn't been told to manage.
        """
        try:
            record = self._read_live_record(provider_id)
        except AuthFileError:
            return None, None
        if record is None:
            return None, None

        provider = PROVIDERS[provider_id]
        identity = provider.identity(record)
        owner = self._find_by_identity(provider_id, identity)
        meta = self.registry.accounts().get(owner) if owner else None
        return meta, provider.describe(record)
