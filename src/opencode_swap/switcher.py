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
from enum import Enum, auto
from pathlib import Path

from opencode_swap import backup, opencode_auth, paths, transfer, usage
from opencode_swap.exceptions import AccountExistsError, AuthFileError, OpenCodeSwapError, RefreshError, SchemaError
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


class RefreshOutcome(Enum):
    """Why `Switcher._ensure_refreshed` did or didn't perform a standalone
    refresh, for callers (currently `refresh_account`/the `refresh` CLI
    command) that need to report an accurate reason rather than let a
    caller-still-EXPIRED `Validity` alone imply the wrong one -- EXPIRED
    from an ambiguous live state and EXPIRED from "this provider/type has
    no standalone refresh at all" are different situations that call for
    different messages.
    """

    LIVE = auto()  # resolved via live auth.json; OpenCode owns its own refresh, never attempted here
    AMBIGUOUS = auto()  # live-ownership couldn't be verified; refresh skipped despite being supported
    NO_SUPPORT = auto()  # this provider/record type has no standalone refresh at all (Provider.refresh returned None)
    RESOLVED = auto()  # resolved from the stored record via normal means: already valid, or freshly refreshed


@dataclass(frozen=True)
class AccountRefreshResult:
    """`Switcher.refresh_account`'s result: the account's resulting
    validity, plus why (see `RefreshOutcome`) -- so a caller reporting
    "still expired" can say whether that's because this provider/type has
    no standalone refresh at all, or because the live account state
    couldn't be safely verified this time (worth retrying), rather than
    conflating the two under one misleading message.
    """

    validity: Validity
    outcome: RefreshOutcome


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

    def _sweep_secret_upgrades(self) -> None:
        """Best-effort: reseal any account still on an older secret-store
        format into the current v3 envelope (SecretStore.upgrade). Called
        under `self.lock` at the start of every mutating operation so a
        record that isn't otherwise rewritten by the operation itself
        still gets upgraded, without two concurrent invocations racing to
        upgrade the same key."""
        for provider_id, name in self.registry.scoped_accounts():
            self.secrets.upgrade(_secret_key(provider_id, name))

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

    def _live_attribution(self, provider_id: str, name: str) -> tuple[AuthRecord | None, bool]:
        """Attempt to attribute the live OpenCode-auth.json record to the
        saved account `name`. Returns `(live_record, ambiguous)`:

        - `(record, False)`: attribution succeeded -- `record` is `name`'s
          current live credential, more current than whatever
          opencode-swap last captured into the secret store (OpenCode
          rewrites auth.json in place on every token refresh; see
          docs/opencode-auth.md#loading-and-refresh).
        - `(None, False)`: there is definitely no live record to attribute
          -- auth.json doesn't exist, or this provider has no live entry.
          Safe to treat `name` as not currently live.
        - `(None, True)`: attribution is ambiguous. Two distinct cases collapse
          to this, both meaning "do not treat `name` as safe to
          standalone-refresh":
            1. auth.json exists but can't be read or parsed. A present-but-
               unreadable file might still hold `name`'s live credential --
               unlike a genuinely absent file, this is not proof of absence.
            2. A live record exists whose identity matches no saved
               account, `name` is this provider's registry-active account,
               shares the live record's type, and `name`'s own stored
               identity is unstable. This is the shape of an in-flight
               unstable-to-stable identity transition (OpenCode rotated
               `name`'s token and the new one happens to carry an
               `accountId` claim the old one lacked) -- or an unrelated
               foreign login. Either way, `name`'s stored refresh token may
               already have been invalidated by whatever this rotation was.

        Never accepts attribution through the registry's active-name hint
        alone (case 2 above refuses, it never accepts): `use_account` only
        ever uses that same hint to decide whether to *refuse* an ambiguous
        live credential (stash it as unclaimed and raise, see its
        ambiguous-identity branch) -- never to *accept* it as belonging to
        the active account. A foreign OpenCode login with no stable
        identity would otherwise be silently attributed to whatever account
        the registry last recorded as active, and a caller that then syncs
        it back (see `_ensure_refreshed`) would overwrite that account's
        real stored credentials with the foreign one.
        """
        if not self.opencode_auth_path.exists():
            return None, False
        try:
            live_record = self._read_live_record(provider_id)
        except AuthFileError:
            return None, True
        if live_record is None:
            return None, False

        provider = self._provider(provider_id)
        identity = provider.identity(live_record)
        owner = self._find_by_identity(provider_id, identity)
        if owner == name:
            return live_record, False

        if self.registry.get_active(provider_id) == name:
            meta = self.registry.scoped_accounts().get((provider_id, name))
            if meta is not None and meta.type == live_record.type:
                stored_record = self._load_record(provider_id, name)
                if stored_record is not None and not provider.identity_is_stable(stored_record):
                    return None, True

        return None, False

    def _ensure_refreshed(self, provider_id: str, name: str, *, allow_refresh: bool) -> tuple[AuthRecord, RefreshOutcome] | None:
        """The most current OAuth record attributable to saved account
        `name`, as `(record, outcome)`. None if there's no live-owned or
        stored record to resolve at all.

        Live-attribution, sync-back, and any standalone refresh all happen
        inside a single `self.lock` acquisition, and everything they act on
        (live auth.json, the stored secret) is re-read fresh *after* the
        lock is acquired rather than trusted from a value a caller computed
        before waiting for the lock. Both properties matter for the same
        reason: `use_account` (which also takes this lock) can splice a
        rotated live credential into the secret store as part of its own
        sync-back at any moment. Without re-checking under the lock, a
        delayed caller here could either (a) attribute a *stale* cached live
        snapshot to `name` and overwrite a newer sync-back `use_account` just
        performed, permanently losing the freshest token, or (b) spend a
        standalone refresh on `name`'s stored refresh token after `name`
        became the live-active account, invalidating the very token
        OpenCode itself is about to try to refresh with on its next
        request.

        Never triggers a standalone refresh for the account currently live
        in OpenCode, regardless of `allow_refresh`: OpenCode owns that
        refresh on its own next request (docs/opencode-auth.md#loading-and-refresh) --
        reported as `RefreshOutcome.LIVE`. Also never refreshes when
        `_live_attribution` reports the live state as ambiguous (auth.json
        exists but couldn't be read, or `name` is the registry-active
        account mid an unstable-to-stable identity transition) -- reported
        as `RefreshOutcome.AMBIGUOUS`, distinct from `RefreshOutcome.NO_SUPPORT`
        (this provider/record type has no standalone refresh at all): in
        both ambiguous and live cases `name`'s stored refresh token might
        already have been invalidated by whatever is currently live, so a
        standalone refresh attempt here could either spend a token that's
        about to be superseded anyway, or fail and misreport a perfectly
        healthy, refreshable account as needing re-login. The stored record
        (however stale it looks) is returned as-is instead. Callers must
        not assume EXPIRED plus a lack of refresh means "unsupported" --
        check `outcome`.

        When `allow_refresh` is True, attribution isn't ambiguous, and the
        resolved record is a stored (not live) copy that's expired,
        refreshes it and persists the rotated token before returning
        (`RefreshOutcome.RESOLVED`) -- serialized under the same lock so
        two concurrent callers can never spend the same single-use refresh
        token (OpenAI issues a new one on every grant and invalidates the
        old one; see oauth_refresh.py): the stored record is re-read once
        more immediately before refreshing, so a refresh a concurrent caller
        just completed is picked up instead of spending a second,
        already-superseded token. Raises RefreshError if a refresh was
        attempted and the grant was rejected or the request failed.
        """
        provider = self._provider(provider_id)
        with self.lock:
            live_record, ambiguous = self._live_attribution(provider_id, name)
            if live_record is not None:
                key = _secret_key(provider_id, name)
                stored = self.secrets.get(key)
                if stored is None or json.loads(stored) != live_record.raw:
                    self.secrets.put(key, json.dumps(live_record.raw))
                return live_record, RefreshOutcome.LIVE

            record = self._load_record(provider_id, name)
            if record is None:
                return None
            if ambiguous:
                return record, RefreshOutcome.AMBIGUOUS
            if not allow_refresh or provider.validate(record) == Validity.OK:
                return record, RefreshOutcome.RESOLVED
            refreshed = provider.refresh(record)
            if refreshed is None:
                return record, RefreshOutcome.NO_SUPPORT
            self.secrets.put(_secret_key(provider_id, name), json.dumps(refreshed.raw))
            meta = self.registry.scoped_accounts().get((provider_id, name))
            account_id = refreshed.raw.get("accountId")
            if meta is not None and isinstance(account_id, str) and account_id and meta.account_id != account_id:
                self.registry.upsert_account(replace(meta, account_id=account_id))
            return refreshed, RefreshOutcome.RESOLVED

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
            self._sweep_secret_upgrades()
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
            self._sweep_secret_upgrades()
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
            self._sweep_secret_upgrades()
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
            self._sweep_secret_upgrades()
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
            self._sweep_secret_upgrades()
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
        """Live usage lookup for a saved account, for providers that have a
        usage source (OpenAI OAuth, Z.AI GLM Coding Plan). None when the
        provider has no usage source, the saved record's type isn't one it
        can look up, or the secret store is out of sync -- a distinct "not
        applicable" case from usage.UsageSnapshot's own available=False
        ("looked it up, the request failed").

        Prefers OpenCode's own live auth.json over opencode-swap's stored
        snapshot when the live record can be positively attributed to
        `name` (see `_ensure_refreshed`) -- for whichever account is
        currently active in OpenCode, the live copy is authoritative and
        the stored copy is stale by construction. A drifted live record is
        captured back into the secret store as a side effect. That account
        is never refreshed standalone, live or expired: OpenCode owns that.

        For any other saved account, refreshes its stored OAuth token on
        demand when it's expired -- this is the one thing besides the
        explicit `refresh` command that spends network quota and a
        single-use refresh token, and only because the caller already
        opted in with `--usage`. (Static API-key providers never refresh;
        the whole block below is a no-op for them.)
        """
        provider = self._provider(provider_id)
        meta = self.registry.scoped_accounts().get((provider_id, name))
        if meta is None or meta.type not in provider.usage_record_types:
            return None

        try:
            result = self._ensure_refreshed(provider_id, name, allow_refresh=True)
        except RefreshError as exc:
            return usage.UsageSnapshot(available=False, message=str(exc))
        if result is None:
            return None
        record, outcome = result

        if provider.validate(record) != Validity.OK:
            if outcome is RefreshOutcome.LIVE:
                message = "expired; OpenCode refreshes on next request"
            elif outcome is RefreshOutcome.AMBIGUOUS:
                message = "expired; live account state could not be confirmed, refresh skipped"
            else:
                message = "expired and no standalone refresh available for this account type"
            return usage.UsageSnapshot(available=False, message=message)

        return provider.fetch_usage(record)

    def account_validity(self, name: str, provider_id: str = "openai") -> Validity:
        """Validity of a saved account's most current record. See
        `account_status` for the full semantics."""
        return self.account_status(name, provider_id)[0]

    def account_status(self, name: str, provider_id: str = "openai") -> tuple[Validity, AccountDesc | None]:
        """`(validity, description)` of a saved account's most current record.

        `validity` is OK/EXPIRED/INVALID; INVALID means the secret is missing
        or unreadable (registry entry orphaned), in which case `description`
        is None.

        Prefers the live auth.json record when it can be positively
        attributed to `name` (see `_ensure_refreshed`) -- OpenCode rotates
        tokens in `auth.json` in place, so the account currently active in
        OpenCode may look expired in opencode-swap's stored snapshot while
        the live copy is still perfectly valid. Never triggers a network
        refresh: this is called unconditionally by `list`, with no separate
        opt-in for a network call.
        """
        meta = self.registry.scoped_accounts().get((provider_id, name))
        if meta is None:
            raise OpenCodeSwapError(f"no such account: {name}")
        result = self._ensure_refreshed(provider_id, name, allow_refresh=False)
        if result is None:
            return Validity.INVALID, None
        record, _outcome = result
        provider = self._provider(meta.provider)
        return provider.validate(record), provider.describe(record)

    def refresh_account(self, name: str, provider_id: str = "openai") -> AccountRefreshResult:
        """Ensure a saved account's OAuth token is valid, refreshing it over
        the network and persisting the rotated token if it's expired.

        A no-op beyond the existence check when the token is already valid
        -- refreshing a still-valid token would needlessly spend its
        single-use refresh token for no benefit, which could break another
        holder of the same account (e.g. a second OpenCode install). Never
        refreshes the account currently active in OpenCode: that account's
        validity is reported from its live `auth.json` record instead (see
        `_ensure_refreshed`), since OpenCode owns its refresh on its own
        next request.

        Raises OpenCodeSwapError if the account doesn't exist or has no
        stored credentials. Raises RefreshError if a refresh was attempted
        and the grant was rejected or the request failed. Returns an
        `AccountRefreshResult` — the resulting Validity (OK if the token is
        now valid) alongside the `RefreshOutcome` explaining why, if it
        isn't: `NO_SUPPORT` (this provider/record type has no standalone
        refresh) is a different, unactionable-here situation from `LIVE`
        or `AMBIGUOUS` (this account genuinely supports refresh, but it was
        deliberately skipped this time) — callers must not collapse those
        into the same "no refresh available" message.
        """
        provider_id = normalize_provider_id(provider_id)
        name = normalize_account_name(name)
        with self.lock:
            self._sweep_secret_upgrades()
            if self.registry.scoped_accounts().get((provider_id, name)) is None:
                raise OpenCodeSwapError(f"no such account: {name}")
        result = self._ensure_refreshed(provider_id, name, allow_refresh=True)
        if result is None:
            raise OpenCodeSwapError(f"no stored credentials for '{name}' (secret store may be out of sync)")
        record, outcome = result
        return AccountRefreshResult(validity=self._provider(provider_id).validate(record), outcome=outcome)

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
