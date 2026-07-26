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
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from opencode_swap import backup, opencode_auth, paths, transfer, usage
from opencode_swap.exceptions import AccountExistsError, AuthFileError, OpenCodeSwapError, SchemaError
from opencode_swap.locking import FileLock
from opencode_swap.models import (
    AccountDesc,
    AccountMeta,
    AuthRecord,
    ImportConflictAction,
    JsonObject,
    Platform,
    SwitchTransaction,
    Validity,
    normalize_account_name,
    normalize_provider_id,
)
from opencode_swap.providers import get_provider
from opencode_swap.providers.base import Provider
from opencode_swap.store import Registry, SecretStore


def _secret_key(provider_id: str, name: str) -> str:
    return f"{provider_id}:{name}"


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _transfer_added(value: str) -> str:
    normalized = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%dT%H:%M:%SZ")
    if normalized != value:
        raise ValueError("timestamp is not canonical")
    return normalized


def _without_archive_credential(value: str | None, credentials: set[str]) -> str | None:
    if value is None or any(secret in value for secret in credentials):
        return None
    return value


def _same_orphan_record(provider: Provider, stored: AuthRecord, incoming: AuthRecord) -> bool:
    if stored.raw == incoming.raw:
        return True
    stored_identity = provider.identity(stored)
    return stored_identity == provider.identity(incoming) and provider.identity_is_stable(stored)


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
        with self.lock:
            self.registry.migrate()

    @classmethod
    def default(cls) -> Switcher:
        return cls(opencode_auth_path=paths.get_opencode_auth_path(), data_root=paths.get_data_root())

    @staticmethod
    def _provider(provider_id: str) -> Provider:
        return get_provider(normalize_provider_id(provider_id))

    def _load_record(self, provider_id: str, name: str, *, confirmed: bool = False) -> AuthRecord | None:
        key = _secret_key(provider_id, name)
        stored = self.secrets.get_confirmed(key) if confirmed else self.secrets.get(key)
        if stored is None:
            return None
        return self._parse_stored_record(provider_id, name, stored)

    def _parse_stored_record(self, provider_id: str, name: str, stored: str) -> AuthRecord:
        meta = self.registry.scoped_accounts().get((provider_id, name))
        try:
            raw = json.loads(stored)
        except json.JSONDecodeError as exc:
            raise SchemaError(f"stored credentials for '{name}' are not valid JSON") from exc
        record = self._provider(provider_id).extract({provider_id: raw})
        if record is None:  # extract only returns None for an absent provider key
            raise SchemaError(f"stored credentials for '{name}' are missing")
        if meta is not None and record.type != meta.type:
            raise SchemaError(f"stored credentials for '{name}' do not match registry type")
        return record

    def _find_by_identity(self, provider_id: str, identity: str, *, confirmed: bool = False) -> str | None:
        provider = self._provider(provider_id)
        for _stored_provider, name in self.registry.scoped_accounts(provider_id):
            record = self._load_record(provider_id, name, confirmed=confirmed)
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
        return self._provider(provider_id).extract(auth)

    def add_account(self, name: str, provider_id: str = "openai") -> AccountMeta:
        provider_id = normalize_provider_id(provider_id)
        name = normalize_account_name(name)
        provider = self._provider(provider_id)

        with self.lock:
            try:
                record = self._read_live_record(provider_id)
            except AuthFileError as exc:
                raise OpenCodeSwapError(f"could not read OpenCode's auth file ({exc}); run `opencode auth login` first") from exc
            if record is None:
                raise OpenCodeSwapError(f"no active {provider_id} account in OpenCode; run `opencode auth login` first")

            identity = provider.identity(record)
            existing_owner = self._find_by_identity(provider_id, identity, confirmed=True)

            if existing_owner is not None and existing_owner != name:
                raise AccountExistsError(
                    f"this account is already saved as '{existing_owner}' "
                    f"(use `opencode-swap use {provider_id} {existing_owner}` or "
                    f"`opencode-swap rename {provider_id} {existing_owner} {name}`)"
                )

            existing_meta = self.registry.scoped_accounts().get((provider_id, name))
            if existing_meta is not None and existing_owner is None:
                raise AccountExistsError(f"account name '{name}' is already used by a different account; remove it first or choose another name")
            if existing_meta is None:
                destination_secret = self.secrets.get_confirmed(_secret_key(provider_id, name))
                destination_record = self._parse_stored_record(provider_id, name, destination_secret) if destination_secret is not None else None
                if destination_record is not None and not _same_orphan_record(provider, destination_record, record):
                    raise AccountExistsError(
                        f"account name '{name}' has unregistered stored credentials; recover it with the matching account or remove it first"
                    )

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
            self.registry.set_active(name, provider_id)

        return meta

    def _sync_live_for_export(self, accounts: dict[tuple[str, str], AccountMeta]) -> None:
        if not self.opencode_auth_path.exists():
            return
        auth = opencode_auth.read_auth(self.opencode_auth_path)
        for provider_id in {meta.provider for meta in accounts.values()}:
            provider = self._provider(provider_id)
            live_record = provider.extract(auth)
            if live_record is None:
                continue
            live_identity = provider.identity(live_record)
            owner_name = self._find_by_identity(provider_id, live_identity, confirmed=True)
            if owner_name is not None:
                stored = self.secrets.get_confirmed(_secret_key(provider_id, owner_name))
                if stored is None or json.loads(stored) != live_record.raw:
                    self.secrets.put(_secret_key(provider_id, owner_name), json.dumps(live_record.raw))
                continue
            active_name = self.registry.get_active(provider_id)
            active_meta = accounts.get((provider_id, active_name)) if active_name is not None else None
            if active_meta is None or active_meta.type != live_record.type:
                continue
            raise OpenCodeSwapError(
                "live credential does not match the registry-active account; cannot safely determine which saved account to refresh before export"
            )

    def export_accounts(self, path: Path, password: str) -> int:
        """Export every managed account into a password-encrypted archive."""
        with self.lock:
            accounts = self.registry.scoped_accounts()
            if not accounts:
                raise OpenCodeSwapError("no saved accounts to export")
            self._sync_live_for_export(accounts)

            entries: list[transfer.TransferEntry] = []
            for provider_id, name in sorted(accounts):
                meta = accounts[(provider_id, name)]
                record = self._load_record(meta.provider, name, confirmed=True)
                if record is None:
                    raise OpenCodeSwapError(f"no stored credentials for '{name}' (secret store may be unavailable)")
                try:
                    _transfer_added(meta.added)
                except ValueError:
                    meta = replace(meta, added=_now_iso())
                entries.append(transfer.TransferEntry(meta=meta, record=record.raw))
            transfer.write_archive(path, entries, password)
            return len(entries)

    def import_accounts(  # noqa: PLR0912, PLR0915
        self,
        path: Path,
        password: str,
        resolve_conflict: Callable[[str], ImportConflictAction] | None = None,
    ) -> int:
        """Import an archive, optionally resolving existing-name conflicts."""
        entries = transfer.read_archive(path, password)
        if not entries:
            raise OpenCodeSwapError("account archive contains no accounts")

        with self.lock:
            validated: list[tuple[transfer.TransferEntry, Provider, AuthRecord]] = []
            for entry in entries:
                try:
                    provider = self._provider(entry.meta.provider)
                    record = provider.extract({entry.meta.provider: entry.record})
                except (SchemaError, ValueError):
                    raise SchemaError("account archive contains invalid credentials") from None
                if record is None:
                    raise SchemaError("account archive contains missing credentials")
                if record.type != entry.meta.type:
                    raise SchemaError("account archive credential type does not match metadata")
                validated.append((entry, provider, record))

            incoming: list[tuple[AccountMeta, AuthRecord]] = []
            incoming_identities: set[tuple[str, str]] = set()
            archive_credentials = {value for _entry, provider, record in validated for value in provider.credential_values(record)}
            if any(
                secret in value
                for entry in entries
                for value in (entry.meta.provider, entry.meta.name, entry.meta.type, entry.meta.added)
                for secret in archive_credentials
            ):
                raise SchemaError("account archive contains credential data in non-secret metadata")
            for entry, provider, record in validated:
                try:
                    added = _transfer_added(entry.meta.added)
                except ValueError as exc:
                    raise SchemaError(f"imported metadata for '{entry.meta.name}' has an invalid added timestamp") from exc
                desc = provider.describe(record)
                meta = AccountMeta(
                    name=entry.meta.name,
                    provider=entry.meta.provider,
                    type=record.type,
                    account_id=_without_archive_credential(desc.account_id, archive_credentials),
                    email=_without_archive_credential(desc.email, archive_credentials),
                    added=added,
                )
                identity_key = (entry.meta.provider, provider.identity(record))
                if identity_key in incoming_identities:
                    raise AccountExistsError("account archive contains duplicate account identities")
                incoming_identities.add(identity_key)
                incoming.append((meta, record))

            existing_accounts = self.registry.scoped_accounts()
            selected: list[tuple[AccountMeta, AuthRecord]] = []
            overwritten_keys: set[tuple[str, str]] = set()
            for meta, record in incoming:
                account_key = (meta.provider, meta.name)
                if account_key in existing_accounts:
                    if resolve_conflict is None:
                        raise AccountExistsError(f"account '{meta.provider}:{meta.name}' already exists; import made no changes")
                    action = resolve_conflict(f"{meta.provider}:{meta.name}")
                    if action is ImportConflictAction.ABORT:
                        raise OpenCodeSwapError("import aborted; no changes made")
                    if action is ImportConflictAction.SKIP:
                        continue
                    if action is not ImportConflictAction.OVERWRITE:
                        raise ValueError("invalid import conflict action")
                    overwritten_keys.add(account_key)
                selected.append((meta, record))

            if not selected:
                return 0

            existing_identities: set[tuple[str, str]] = set()
            original_secrets: dict[str, str | None] = {}
            for (provider_id, name), meta in existing_accounts.items():
                key = _secret_key(meta.provider, name)
                stored = self.secrets.get_confirmed(key)
                if (provider_id, name) in overwritten_keys:
                    original_secrets[key] = stored
                    continue
                if stored is None:
                    raise OpenCodeSwapError(f"no stored credentials for existing account '{name}'; import made no changes")
                record = self._parse_stored_record(meta.provider, name, stored)
                existing_identities.add((meta.provider, self._provider(meta.provider).identity(record)))
            selected_identities = {(meta.provider, self._provider(meta.provider).identity(record)) for meta, record in selected}
            if selected_identities & existing_identities:
                raise AccountExistsError("an imported account identity is already saved under another name; import made no changes")

            for meta, _record in selected:
                key = _secret_key(meta.provider, meta.name)
                if (meta.provider, meta.name) not in overwritten_keys and self.secrets.get_confirmed(key) is not None:
                    raise AccountExistsError(f"account name '{meta.name}' has unregistered stored credentials; import made no changes")
                original_secrets.setdefault(key, None)

            attempted_keys: list[str] = []
            try:
                for meta, record in selected:
                    key = _secret_key(meta.provider, meta.name)
                    attempted_keys.append(key)
                    self.secrets.put(key, json.dumps(record.raw))
                self.registry.upsert_accounts([meta for meta, _record in selected])
            except BaseException as exc:
                cleanup_failed = False
                for key in reversed(attempted_keys):
                    try:
                        original = original_secrets[key]
                        if original is None:
                            self.secrets.delete(key)
                        else:
                            self.secrets.put(key, original)
                    except BaseException:
                        cleanup_failed = True
                if cleanup_failed:
                    raise OpenCodeSwapError("import failed and cleanup could not restore all previous credentials; registry was not changed") from exc
                raise
            return len(selected)

    def use_account(self, name: str, provider_id: str = "openai") -> AccountMeta:  # noqa: PLR0912, PLR0915
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
        provider_id = normalize_provider_id(provider_id)
        name = normalize_account_name(name)
        with self.lock:
            target_meta = self.registry.scoped_accounts().get((provider_id, name))
            if target_meta is None:
                raise OpenCodeSwapError(f"no such account: {name}")
            provider = self._provider(provider_id)

            target_record = self._load_record(provider_id, name, confirmed=True)
            if target_record is None:
                raise OpenCodeSwapError(
                    f"no stored credentials for '{name}' (secret store may be out of sync); "
                    "try `opencode-swap add <provider> <name>` again while that account is active in OpenCode"
                )

            auth = opencode_auth.read_auth(self.opencode_auth_path) if self.opencode_auth_path.exists() else {}

            backup.write_pristine_if_absent(self.data_root, auth)
            transaction = SwitchTransaction(original_auth=auth)

            try:
                live_record = provider.extract(auth)
                if live_record is not None:
                    live_identity = provider.identity(live_record)
                    owner_name = self._find_by_identity(provider_id, live_identity, confirmed=True)
                    if owner_name is None and not provider.identity_is_stable(live_record):
                        active_name = self.registry.get_active(provider_id)
                        if active_name is not None:
                            active_meta = self.registry.scoped_accounts().get((provider_id, active_name))
                            if active_meta is not None and active_meta.type == live_record.type:
                                active_record = self._load_record(provider_id, active_name, confirmed=True)
                                if active_record is not None and not provider.identity_is_stable(active_record):
                                    backup.write_unclaimed(self.data_root, provider_id, live_record.raw)
                                    transaction.record_step("unclaimed_stashed")
                                    raise OpenCodeSwapError(
                                        f"live {provider_id} credential changed without a stable account identity; "
                                        "preserved it as an unclaimed backup and refused to overwrite it"
                                    )
                    if owner_name is not None:
                        stored = self.secrets.get_confirmed(_secret_key(provider_id, owner_name))
                        if stored is None or json.loads(stored) != live_record.raw:
                            self.secrets.put(_secret_key(provider_id, owner_name), json.dumps(live_record.raw))
                            transaction.record_step("sync_captured")
                        if owner_name == name:
                            # A self-switch may have just captured rotated tokens;
                            # never splice the pre-sync cached record back over them.
                            target_record = self._load_record(provider_id, name, confirmed=True)
                            if target_record is None:
                                raise OpenCodeSwapError(f"stored credentials for '{name}' disappeared during switch")
                    else:
                        backup.write_unclaimed(self.data_root, provider_id, live_record.raw)
                        transaction.record_step("unclaimed_stashed")

                backup.write_bak(self.data_root, auth)
                transaction.record_step("bak_written")

                new_auth = provider.splice(auth, target_record)
                opencode_auth.atomic_write_auth(self.opencode_auth_path, new_auth)
                transaction.record_step("auth_written")

                self.registry.set_active(name, provider_id)
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
        provider_id = normalize_provider_id(provider_id)
        name = normalize_account_name(name)
        with self.lock:
            meta = self.registry.scoped_accounts().get((provider_id, name))
            if meta is None:
                raise OpenCodeSwapError(f"no such account: {name}")
            original_active = self.registry.get_active(provider_id)
            secret_key = _secret_key(meta.provider, name)
            secret = self.secrets.get(secret_key)
            self.registry.remove_account(name, provider_id)
            try:
                self.secrets.delete(secret_key)
            except BaseException:
                # OS deletion errors are ambiguous: it might have completed
                # before timing out. Preserve captured credentials in the
                # pinned file fallback before re-registering this account.
                if secret is not None:
                    self.secrets.put(secret_key, secret)
                self.registry.upsert_account(meta)
                self.registry.set_active(original_active, provider_id)
                raise

    def rename_account(self, old: str, new: str, provider_id: str = "openai") -> None:
        provider_id = normalize_provider_id(provider_id)
        old = normalize_account_name(old)
        new = normalize_account_name(new)
        with self.lock:
            meta = self.registry.scoped_accounts().get((provider_id, old))
            if meta is None:
                raise OpenCodeSwapError(f"no such account: {old}")
            if self.registry.scoped_accounts().get((provider_id, new)) is not None:
                raise AccountExistsError(f"account already exists: {new}")

            secret = self.secrets.get(_secret_key(meta.provider, old))
            if secret is None:
                raise OpenCodeSwapError(f"no stored credentials for '{old}' (secret store may be unavailable)")
            provider = self._provider(meta.provider)
            source_record = self._load_record(meta.provider, old)
            if source_record is None:
                raise OpenCodeSwapError(f"no stored credentials for '{old}' (secret store may be unavailable)")
            old_key = _secret_key(meta.provider, old)
            new_key = _secret_key(meta.provider, new)
            previous_new_secret = self.secrets.get_confirmed(new_key)
            if previous_new_secret is not None:
                destination_record = self._parse_stored_record(meta.provider, new, previous_new_secret)
                if destination_record is not None and not _same_orphan_record(provider, destination_record, source_record):
                    raise AccountExistsError(
                        f"account name '{new}' has unregistered stored credentials; recover it with the matching account or remove it first"
                    )
            self.secrets.put(new_key, secret)
            registry_renamed = False
            try:
                self.registry.rename_account(old, new, provider_id)
                registry_renamed = True
                self.secrets.delete(old_key)
            except BaseException:
                if registry_renamed:
                    # Keep the new registry mapping if recovery fails: it
                    # still names the pre-copied usable credential.
                    self.secrets.put(old_key, secret)
                    self.registry.rename_account(new, old, provider_id)
                if previous_new_secret is None:
                    with suppress(OpenCodeSwapError, OSError):
                        self.secrets.delete(new_key)
                else:
                    self.secrets.put(new_key, previous_new_secret)
                raise

    def _finish_restore(self, data: JsonObject) -> list[AccountMeta]:
        """Best-effort bookkeeping after a restore has committed live state."""
        active: list[AccountMeta] = []
        provider_ids = {meta.provider for meta in self.registry.scoped_accounts().values()}
        for provider_id in provider_ids:
            try:
                provider = self._provider(provider_id)
                record = provider.extract(data)
                owner_name = self._find_by_identity(provider_id, provider.identity(record)) if record else None
            except OpenCodeSwapError:
                owner_name = None
            with suppress(OpenCodeSwapError):
                self.registry.set_active(owner_name, provider_id)
            meta = self.registry.scoped_accounts().get((provider_id, owner_name)) if owner_name else None
            if meta is not None:
                active.append(meta)
        return active

    def restore(self, source: str = "bak", *, discard_pending: bool = False) -> list[AccountMeta]:
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
        with self.lock:
            recovery = backup.read_restore_snapshot(self.data_root)
            if recovery is not None:
                try:
                    live = opencode_auth.read_auth(self.opencode_auth_path)
                except AuthFileError:
                    live = None
                if live == recovery:
                    backup.remove_restore_snapshot(self.data_root)
                    return self._finish_restore(recovery)
                if discard_pending:
                    # The pending snapshot may be the only surviving copy of
                    # whatever .bak held before this restore attempt started
                    # (chaining below overwrites .bak with current live state
                    # every time) -- archive it before dropping the marker.
                    backup.write_discarded_restore(self.data_root, recovery)
                    backup.remove_restore_snapshot(self.data_root)
                else:
                    raise OpenCodeSwapError(
                        "a previous restore failed and its recovery source is retained at "
                        f"{self.data_root / 'backups' / backup.RESTORE_SNAPSHOT_FILENAME}; "
                        "rerun with --discard-pending to drop it, or delete that file manually before restoring again"
                    )
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

            # A restore from .bak must not erase its only source before the
            # new live auth.json has landed. Keep this durable until chaining
            # current live state into .bak and the live replacement both pass.
            backup.write_restore_snapshot(self.data_root, data)
            if self.opencode_auth_path.exists():
                try:
                    current_live = opencode_auth.read_auth(self.opencode_auth_path)
                    backup.write_bak(self.data_root, current_live)
                except AuthFileError:
                    pass  # current file is unreadable -- exactly the case restore exists for

            opencode_auth.atomic_write_auth(self.opencode_auth_path, data)
            backup.remove_restore_snapshot(self.data_root)
            return self._finish_restore(data)

    def fetch_usage(self, name: str, provider_id: str = "openai") -> usage.UsageSnapshot | None:
        """Live OpenAI usage lookup for a saved account. None if the
        account has no OAuth record to look up usage for (e.g. an API-key
        account, or a secret store that's out of sync) -- this is a
        distinct "not applicable" case from usage.UsageSnapshot's own
        available=False ("looked it up, the request failed")."""
        meta = self.registry.scoped_accounts().get((provider_id, name))
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

    def account_validity(self, name: str, provider_id: str = "openai") -> Validity:
        """Validity of a saved account's stored record (OK/EXPIRED/INVALID).

        INVALID here means the secret is missing or unreadable (e.g. deleted
        out-of-band from the secret store) — the registry entry is orphaned.
        """
        meta = self.registry.scoped_accounts().get((provider_id, name))
        if meta is None:
            raise OpenCodeSwapError(f"no such account: {name}")
        record = self._load_record(meta.provider, name)
        if record is None:
            return Validity.INVALID
        return self._provider(meta.provider).validate(record)

    def next_account(self, provider_id: str = "openai") -> AccountMeta:
        """Return next saved account after the live managed account, wrapping around."""
        current, _ = self.current(provider_id)
        if current is None:
            raise OpenCodeSwapError("current OpenCode account is not managed; use `opencode-swap use <provider> <name>` first")
        accounts = self.registry.scoped_accounts()
        names = sorted(name for (stored_provider, name) in accounts if stored_provider == provider_id)
        return accounts[(provider_id, names[(names.index(current.name) + 1) % len(names)])]

    def current(self, provider_id: str = "openai") -> tuple[AccountMeta | None, AccountDesc | None]:
        """Return (managed account meta if recognized, live account description).

        (None, None) if OpenCode has no active account for this provider.
        A non-None description with a None meta means OpenCode is logged
        into an account opencode-swap hasn't been told to manage.
        """
        try:
            auth = opencode_auth.read_auth(self.opencode_auth_path)
        except AuthFileError:
            return None, None
        return self.current_from_auth(auth, provider_id)

    def current_from_auth(self, auth: JsonObject, provider_id: str = "openai") -> tuple[AccountMeta | None, AccountDesc | None]:
        """Identify current provider account from already-validated auth data."""
        record = self._provider(provider_id).extract(auth)
        if record is None:
            return None, None

        provider = self._provider(provider_id)
        identity = provider.identity(record)
        owner = self._find_by_identity(provider_id, identity)
        meta = self.registry.scoped_accounts().get((provider_id, owner)) if owner else None
        return meta, provider.describe(record)
