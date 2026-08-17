"""Shared validation for OpenCode's canonical auth record shapes."""

from __future__ import annotations

import math
from typing import TypeGuard

from opencode_swap.exceptions import SchemaError
from opencode_swap.models import AuthRecord, JsonObject


def require_str(raw: JsonObject, field: str, provider_id: str, entry_type: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value:
        raise SchemaError(f"{provider_id} {entry_type} entry missing/invalid field {field!r}")
    return value


def optional_str(raw: JsonObject, field: str, provider_id: str, entry_type: str) -> str | None:
    if field not in raw:
        return None
    value = raw[field]
    if not isinstance(value, str):
        raise SchemaError(f"{provider_id} {entry_type} entry has invalid field {field!r}")
    return value


def is_json_number(value: object) -> TypeGuard[int | float]:
    """Whether value survives OpenCode's JavaScript JSON-number boundary."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def require_expiry(raw: JsonObject, provider_id: str) -> int | float:
    expires = raw.get("expires")
    if not is_json_number(expires) or expires < 0:
        raise SchemaError(f"{provider_id} oauth entry has invalid 'expires'")
    return expires


def published_raw(raw: JsonObject) -> JsonObject:
    """Copy of `raw` safe to publish into OpenCode's auth.json.

    Every `Provider.splice` routes its record through this -- splice is the
    only path by which opencode-swap content reaches auth.json, and the one
    place where our tolerance for what we *read* must stop.

    OpenCode types `expires` as `NonNegativeInt` (auth/index.ts's Oauth
    class, via packages/schema/src/schema.ts's `Schema.Int`), and `Auth.all()`
    decodes with `Record.filterMap`, which drops entries that fail to decode
    instead of reporting them. Publishing a fractional `expires` therefore
    makes the provider vanish from OpenCode entirely, with the first symptom
    appearing far away as an `auth.type` access on an undefined record
    (codex.ts:331).

    Deliberately not done in `extract`: that is a validator on records we
    did not write, and normalizing there would also rewrite the live record
    captured by `backup.write_unclaimed`, whose entire purpose is to
    preserve a foreign login exactly as found. Reads stay verbatim; only
    what we hand back to OpenCode is constrained. Records written by older
    versions (see oauth_refresh.py) are healed on the next switch as a
    consequence.

    Scoped to `type == "oauth"`: that is the only OpenCode auth variant
    whose schema types `expires` as `NonNegativeInt` at all (Api and
    WellKnown carry no `expires` field in OpenCode's schema). Truncating an
    `expires`-named key on any other type -- or any other unknown field --
    would silently mutate data that isn't ours to touch and OpenCode was
    never going to reject in the first place.
    """
    if raw.get("type") != "oauth":
        return dict(raw)
    expires = raw.get("expires")
    if not is_json_number(expires) or isinstance(expires, int):
        return dict(raw)  # absent, non-numeric (rejected elsewhere), or already integral
    return {**raw, "expires": int(expires)}


def extract_raw(auth: JsonObject, provider_id: str) -> JsonObject | None:
    raw = auth.get(provider_id)
    if raw is None:
        return None
    if not isinstance(raw, dict) or "type" not in raw:
        raise SchemaError(f"{provider_id} entry is not a recognizable auth record")
    return raw


def validate_api(raw: JsonObject, provider_id: str) -> AuthRecord:
    require_str(raw, "key", provider_id, "api")
    metadata = raw.get("metadata")
    if "metadata" in raw and (
        not isinstance(metadata, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in metadata.items())
    ):
        raise SchemaError(f"{provider_id} api entry has invalid field 'metadata'")
    return AuthRecord(type="api", raw=dict(raw))


def validate_oauth(raw: JsonObject, provider_id: str) -> AuthRecord:
    require_str(raw, "refresh", provider_id, "oauth")
    require_str(raw, "access", provider_id, "oauth")
    require_expiry(raw, provider_id)
    optional_str(raw, "accountId", provider_id, "oauth")
    optional_str(raw, "enterpriseUrl", provider_id, "oauth")
    return AuthRecord(type="oauth", raw=dict(raw))


def credential_values(record: AuthRecord) -> set[str]:
    return {value for field in ("refresh", "access", "key", "token") if isinstance((value := record.raw.get(field)), str) and value}
