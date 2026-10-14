# MIT License
#
# Copyright (c) 2026 Roberto Leinardi
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""Moving saved accounts into OpenCode 2's credential store.

OpenCode 2 keeps several credentials per integration in its SQLite DB and
reads `auth.json` only once, in a legacy-import migration that runs at most
once and imports at most one entry per provider. This module turns saved
accounts into entries for `opencode auth import`, after reading what the
destination already holds through `opencode auth export`.

Every OpenCode 2 behaviour relied on here is recorded, with source citations,
in docs/opencode-v2-integration.md; the mapping mirrors the legacy import
(V2) so an account imported either way ends up identical.

The mapping and planning functions are pure. `read_destination` is the only
I/O: it runs the `opencode` binary and keeps its output in memory.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from opencode_swap.exceptions import MigrationError, SchemaError
from opencode_swap.models import AuthRecord, JsonObject
from opencode_swap.providers import get_provider

# OpenCode 2's identifier alphabet (packages/schema/src/identifier.ts:4).
ID_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
ID_PREFIX = "cred_"
ID_LENGTH = 26
# `auth export`, `auth import` and `client.credential.list` all first ship in
# 2.0.20 (docs/opencode-v2-integration.md, V3 and V14).
MIN_OPENCODE_VERSION = (2, 0, 20)
EXPORT_TIMEOUT_SECONDS = 120

_DEVICE_INTEGRATIONS = frozenset({"github-copilot", "opencode", "xai"})
_LEGACY_FIELDS = {
    "oauth": {"type", "refresh", "access", "expires", "accountId", "enterpriseUrl"},
    "api": {"type", "key", "metadata"},
    "wellknown": {"type", "key", "token"},
}
_VALUE_FIELDS = {
    "oauth": ({"type", "methodID", "refresh", "access", "expires"}, {"metadata"}),
    "key": ({"type", "key"}, {"metadata", "configuration"}),
    "external": ({"type", "methodID"}, {"metadata"}),
}
_ENTRY_FIELDS = {"id", "integrationID", "label", "active", "value"}
_VERSION_PATTERN = re.compile(r"(\d+)\.(\d+)\.(\d+)")


# --- mapping ---------------------------------------------------------------


def integration_id(provider_id: str) -> str:
    """The OpenCode 2 integration an `auth.json` provider key imports into:
    the key with trailing slashes stripped, as the legacy import does."""
    integration = re.sub(r"/+$", "", provider_id)
    if not integration:
        raise SchemaError(f"provider id {provider_id!r} has no OpenCode 2 integration id")
    return integration


def method_id(integration: str) -> str:
    """The OAuth method the legacy import assigns to an integration."""
    if integration == "openai":
        return "chatgpt-browser"
    if integration in _DEVICE_INTEGRATIONS:
        return "device"
    return "oauth"


def credential_id(provider_id: str, name: str) -> str:
    """A deterministic OpenCode 2 credential id for a saved account.

    OpenCode 2 never decodes ids, and `auth import` skips an id that already
    exists, so the same account always maps to the same id and a re-run
    imports nothing twice. A changed or recreated account under the same name
    keeps its id; `plan_migration`'s id preflight catches that."""
    digest = int.from_bytes(hashlib.sha256(f"opencode-swap\0{provider_id}\0{name}".encode()).digest(), "big")
    chars = []
    for _ in range(ID_LENGTH):
        digest, index = divmod(digest, len(ID_ALPHABET))
        chars.append(ID_ALPHABET[index])
    return ID_PREFIX + "".join(chars)


def _require_str(raw: JsonObject, name: str, what: str) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or not value:
        raise SchemaError(f"{what} has a missing or invalid {name!r}")
    return value


def _require_fields(raw: JsonObject, allowed: set[str], what: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise SchemaError(f"{what} has unsupported field {unknown[0]!r}")


def to_value(record: AuthRecord, integration: str, what: str) -> JsonObject | None:
    """The OpenCode 2 credential value for a saved account's record, or None
    for a `wellknown` record, which is not migrated.

    Mirrors the legacy import's mapping without its leniency: a record it
    would drop or reshape (a fractional `expires`, non-string metadata, an
    unknown field) raises SchemaError instead. `what` names the account in
    errors; no value ever appears in one."""
    raw = record.raw
    allowed = _LEGACY_FIELDS.get(record.type)
    if allowed is None or raw.get("type") != record.type:
        raise SchemaError(f"{what} has unsupported record type {record.type!r}")
    _require_fields(raw, allowed, what)
    if record.type == "wellknown":
        return None
    if record.type == "api":
        value: JsonObject = {"type": "key", "key": _require_str(raw, "key", what)}
        if "metadata" in raw:
            legacy_metadata = raw["metadata"]
            if not isinstance(legacy_metadata, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in legacy_metadata.items()):
                raise SchemaError(f"{what} has metadata that is not a string record")
            value["metadata"] = dict(legacy_metadata)
        return value
    expires = raw.get("expires")
    if type(expires) is not int or expires < 0:
        raise SchemaError(f"{what} has an 'expires' that is not a non-negative integer")
    value = {
        "type": "oauth",
        "methodID": method_id(integration),
        "refresh": _require_str(raw, "refresh", what),
        "access": _require_str(raw, "access", what),
        "expires": expires,
    }
    metadata: dict[str, str] = {}
    for source, target in (("accountId", "accountID"), ("enterpriseUrl", "enterpriseUrl")):
        if source in raw:
            item = raw[source]
            if not isinstance(item, str):
                raise SchemaError(f"{what} has an invalid {source!r}")
            if item:
                metadata[target] = item
    if metadata:
        value["metadata"] = metadata
    return value


def from_value(integration: str, value: JsonObject) -> AuthRecord | None:
    """The `auth.json` record an OpenCode 2 credential value corresponds to,
    or None when it cannot be reversed: `external` credentials, keys with a
    form `configuration`, OAuth methods other than the one the legacy import
    assigns, and metadata outside the legacy shape. Such a credential is
    unverifiable: it can neither prove nor rule out that it holds an
    account."""
    metadata = value.get("metadata", {})
    if not isinstance(metadata, dict) or any(not isinstance(item, str) for item in metadata.values()):
        return None
    if value.get("type") == "key":
        if "configuration" in value:
            return None
        raw: JsonObject = {"type": "api", "key": value["key"]}
        if "metadata" in value:
            raw["metadata"] = dict(metadata)
        return AuthRecord(type="api", raw=raw)
    if value.get("type") != "oauth" or value.get("methodID") != method_id(integration) or set(metadata) - {"accountID", "enterpriseUrl"}:
        return None
    raw = {"type": "oauth", "refresh": value["refresh"], "access": value["access"], "expires": value["expires"]}
    if "accountID" in metadata:
        raw["accountId"] = metadata["accountID"]
    if "enterpriseUrl" in metadata:
        raw["enterpriseUrl"] = metadata["enterpriseUrl"]
    return AuthRecord(type="oauth", raw=raw)


# --- destination -----------------------------------------------------------


@dataclass(frozen=True)
class DestinationCredential:
    """One credential `opencode auth export` reported. `value` holds secrets
    and never leaves memory."""

    id: str
    integration: str
    label: str
    active: bool
    value: JsonObject = field(repr=False)


def _parse_value(value: object, what: str) -> JsonObject:
    if not isinstance(value, dict):
        raise SchemaError(f"{what} has a value that is not an object")
    shape = _VALUE_FIELDS.get(value.get("type"))  # type: ignore[arg-type]
    if shape is None:
        raise SchemaError(f"{what} has an unknown credential type")
    required, optional = shape
    missing = sorted(required - set(value))
    if missing:
        raise SchemaError(f"{what} is missing {missing[0]!r}")
    _require_fields(value, required | optional, what)
    for name in required - {"type", "expires"}:
        if not isinstance(value[name], str):
            raise SchemaError(f"{what} has an invalid {name!r}")
    if "expires" in required and (type(value["expires"]) is not int or value["expires"] < 0):
        raise SchemaError(f"{what} has an invalid 'expires'")
    for name in optional:
        if name in value and not (isinstance(value[name], dict) and all(isinstance(key, str) for key in value[name])):
            raise SchemaError(f"{what} has an invalid {name!r}")
    return value


def parse_destination(text: str) -> list[DestinationCredential]:
    """Strictly parse `opencode auth export` output against OpenCode 2's
    `Credential.Entry` schema (packages/schema/src/credential.ts:32-70).
    Anything else raises SchemaError: the export is what proves which
    accounts the destination already holds, so a shape this code does not
    know cannot be trusted to prove anything."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SchemaError("opencode auth export did not print JSON") from exc
    if not isinstance(data, list):
        raise SchemaError("opencode auth export did not print a JSON array")
    result = []
    for index, entry in enumerate(data):
        what = f"OpenCode 2 credential #{index + 1}"
        if not isinstance(entry, dict):
            raise SchemaError(f"{what} is not an object")
        if set(entry) != _ENTRY_FIELDS:
            raise SchemaError(f"{what} does not have the fields of a credential entry")
        if not all(isinstance(entry[name], str) and entry[name] for name in ("id", "integrationID")) or not isinstance(entry["label"], str):
            raise SchemaError(f"{what} has an invalid id, integration or label")
        if not isinstance(entry["active"], bool):
            raise SchemaError(f"{what} has an invalid 'active'")
        value = _parse_value(entry["value"], what)
        result.append(DestinationCredential(entry["id"], entry["integrationID"], entry["label"], entry["active"], value))
    return result


def parse_version(text: str) -> tuple[int, int, int]:
    match = _VERSION_PATTERN.search(text)
    if match is None:
        raise MigrationError("could not read the OpenCode version")
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def read_destination(opencode: str, run: Runner = subprocess.run) -> list[DestinationCredential]:
    """Check that `opencode` is OpenCode 2.0.20+, then read its credentials.

    `auth export --standalone` uses a private server: it neither starts the
    background service nor talks to a running service of another version
    (docs/opencode-v2-integration.md, V3). Its output holds every stored
    secret, so it is captured in memory and never echoed, not even on
    failure."""
    try:
        version = run([opencode, "--version"], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=EXPORT_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise MigrationError(f"could not run {opencode!r}; install OpenCode 2 or pass --opencode PATH") from exc
    if version.returncode != 0:
        raise MigrationError(f"{opencode} --version failed (exit {version.returncode})")
    found = parse_version(version.stdout)
    if found[0] != 2:
        raise MigrationError(f"{opencode} is OpenCode {'.'.join(map(str, found))}, not OpenCode 2; pass the OpenCode 2 binary with --opencode PATH")
    if found < MIN_OPENCODE_VERSION:
        raise MigrationError(f"OpenCode {'.'.join(map(str, found))} has no 'auth import'; upgrade to OpenCode 2.0.20 or newer")
    try:
        export = run(
            [opencode, "auth", "export", "--standalone"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=EXPORT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MigrationError("could not run 'opencode auth export'") from exc
    if export.returncode != 0:
        raise MigrationError(f"'opencode auth export' failed (exit {export.returncode}); check that OpenCode 2 starts, then retry")
    return parse_destination(export.stdout)


# --- planning --------------------------------------------------------------


@dataclass(frozen=True)
class SourceAccount:
    """A saved account to migrate. `record` is the live record when OpenCode
    1 holds a fresher copy of it (see Switcher.migration_source)."""

    provider_id: str
    name: str
    record: AuthRecord = field(repr=False)
    live: bool
    registry_active: bool

    @property
    def label(self) -> str:
        return f"{self.provider_id}/{self.name}"


@dataclass(frozen=True)
class UnmanagedLogin:
    """OpenCode 1's live login for a provider that matches no saved account.
    `record` is None when its shape is unknown."""

    provider_id: str
    record: AuthRecord | None = field(repr=False)


@dataclass
class MigrationPlan:
    entries: list[JsonObject] = field(default_factory=list, repr=False)
    exported: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _identity(provider_id: str, record: AuthRecord) -> tuple[str, bool] | None:
    """`(provider.identity, provider.identity_is_stable)`, or None for a
    destination record the provider rejects: such a credential is
    unverifiable, never an error."""
    provider = get_provider(provider_id)
    try:
        checked = provider.extract({provider_id: record.raw})
        if checked is None:
            return None
        return provider.identity(checked), provider.identity_is_stable(checked)
    except SchemaError:
        return None


def _ownership(provider_id: str, identity: tuple[str, bool], credential: DestinationCredential) -> bool | None:
    """True if `credential` verifiably holds the account with `identity`,
    False if it verifiably does not, None if that cannot be told.

    An unstable identity (an OpenAI login whose tokens carry no account id
    falls back to its refresh token) proves ownership when equal, but a
    difference proves nothing: OpenCode 2 may have refreshed that very
    account since importing it, so treating it as a different account would
    import the vault's spent token as a silent duplicate."""
    record = from_value(credential.integration, credential.value)
    if record is None:
        return None
    found = _identity(provider_id, record)
    if found is None:
        return None
    if found[0] == identity[0]:
        return True
    if identity[1] and found[1]:
        return False
    return None


def plan_migration(
    sources: Sequence[SourceAccount],
    destination: Sequence[DestinationCredential],
    unmanaged: Iterable[UnmanagedLogin] = (),
    skip: Iterable[str] = (),
) -> MigrationPlan:
    """Decide which saved accounts to emit for `opencode auth import`.

    - An account the destination verifiably holds (same integration, equal
      provider identity) is not emitted again.
    - Everything else is emitted; when the destination has credentials for
      that integration it cannot verify (`from_value` returns None), a
      possible duplicate is reported instead of guessed away.
    - `auth import` skips an existing id without comparing values, so a
      generated id already present without verified ownership would be
      silently dropped: the whole run refuses and lists those conflicts.
    - `active` is set only on the registry-active account of an integration
      the destination lacks. OpenCode activates the first credential created
      for a new integration whatever its flag, so that entry comes first in
      its integration (docs/opencode-v2-integration.md, V3).

    Raises MigrationError on an id conflict, SchemaError on a record that
    cannot be mapped."""
    plan = MigrationPlan()
    skipped = set(skip)
    by_id = {credential.id: credential for credential in destination}
    by_integration: dict[str, list[DestinationCredential]] = {}
    for credential in destination:
        by_integration.setdefault(credential.integration, []).append(credential)
    conflicts: list[str] = []
    emitted: list[tuple[SourceAccount, str, JsonObject]] = []

    for source in sources:
        if source.label in skipped:
            plan.notes.append(f"{source.label}: skipped (--skip)")
            continue
        integration = integration_id(source.provider_id)
        value = to_value(source.record, integration, f"saved account {source.label}")
        if value is None:
            plan.notes.append(f"{source.label}: skipped, a 'wellknown' login is not migrated; connect it again in OpenCode 2")
            continue
        cid = credential_id(source.provider_id, source.name)
        existing = by_integration.get(integration, [])
        identity = _identity(source.provider_id, source.record) if existing or cid in by_id else None
        if cid in by_id:
            holder = by_id[cid]
            owned = identity is not None and holder.integration == integration and _ownership(source.provider_id, identity, holder)
            if owned:
                plan.notes.append(f"{source.label}: already present in OpenCode 2 as {holder.label!r}")
            else:
                conflicts.append(f"{source.label}: {holder.integration} credential {cid} ({holder.label!r})")
            continue
        holders = [c for c in existing if identity is not None and _ownership(source.provider_id, identity, c)]
        if holders:
            plan.notes.append(f"{source.label}: already in OpenCode 2 as {holders[0].label!r} (rename it in /connect with ctrl+r)")
            continue
        unverifiable = [c for c in existing if identity is None or _ownership(source.provider_id, identity, c) is None]
        if unverifiable:
            labels = ", ".join(repr(c.label) for c in unverifiable)
            plan.warnings.append(
                f"{source.label}: OpenCode 2 has {integration} credentials it cannot compare ({labels}); the import may duplicate one of them"
            )
        emitted.append((source, integration, value))

    if conflicts:
        lines = "\n  ".join(conflicts)
        raise MigrationError(
            "these generated credential ids already exist in OpenCode 2 holding a different or unverifiable credential, "
            "and 'opencode auth import' would silently keep the old one:\n  "
            f"{lines}\n"
            "If one is stale, remove it with 'opencode auth logout <integration> <id>'; if it may be the "
            "account itself, refreshed by OpenCode 2 since, keep it and leave the account out with --skip PROVIDER/NAME."
        )

    _emit(plan, emitted, set(by_integration))
    _note_unmanaged(plan, unmanaged, by_integration)
    return plan


def _emit(plan: MigrationPlan, emitted: list[tuple[SourceAccount, str, JsonObject]], present: set[str]) -> None:
    """Append the entries, each integration's registry-active account first
    and the only one marked active, and only for integrations not `present`
    in the destination."""
    activated: set[str] = set()
    for source, integration, value in sorted(emitted, key=lambda item: (item[1], not item[0].registry_active, item[0].label)):
        active = source.registry_active and integration not in present and integration not in activated
        if active:
            activated.add(integration)
        plan.entries.append(
            {
                "id": credential_id(source.provider_id, source.name),
                "integrationID": integration,
                "label": source.name,
                "active": active,
                "value": value,
            }
        )
        details = [detail for detail, applies in (("active", active), ("from OpenCode 1's live login", source.live)) if applies]
        plan.exported.append(f"{source.label} -> {integration} {source.name!r}" + (f" ({', '.join(details)})" if details else ""))


def _note_unmanaged(plan: MigrationPlan, unmanaged: Iterable[UnmanagedLogin], by_integration: dict[str, list[DestinationCredential]]) -> None:
    for login in unmanaged:
        integration = integration_id(login.provider_id)
        identity = _identity(login.provider_id, login.record) if login.record is not None else None
        held = identity is not None and any(_ownership(login.provider_id, identity, c) for c in by_integration.get(integration, []))
        if not held:
            plan.notes.append(
                f"OpenCode 1's {login.provider_id} login is not a saved account and is not exported; "
                f"log in again in OpenCode 2 with 'opencode auth login {integration}'"
            )
