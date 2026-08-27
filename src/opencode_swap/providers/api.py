"""Generic static API-key provider supported by OpenCode's common path."""

from __future__ import annotations

from opencode_swap import usage
from opencode_swap.exceptions import SchemaError
from opencode_swap.models import AccountDesc, AuthRecord, JsonObject, Validity
from opencode_swap.providers.common import credential_values, extract_raw, key_account_hint, published_raw, validate_api


class ApiProvider:
    usage_record_types: frozenset[str] = frozenset()  # no known usage endpoint for an arbitrary API provider

    def __init__(self, provider_id: str):
        self.id = provider_id

    def extract(self, auth: JsonObject) -> AuthRecord | None:
        raw = extract_raw(auth, self.id)
        if raw is None:
            return None
        if raw.get("type") != "api":
            raise SchemaError(f"{self.id} auth type is not supported by opencode-swap")
        return validate_api(raw, self.id)

    def splice(self, auth: JsonObject, record: AuthRecord) -> JsonObject:
        if record.type != "api":
            raise SchemaError(f"{self.id} auth type is not supported by opencode-swap")
        return {**auth, self.id: published_raw(record.raw)}

    def identity(self, record: AuthRecord) -> str:
        key = record.raw.get("key")
        return f"api-key\0{key if isinstance(key, str) else ''}"

    def identity_is_stable(self, record: AuthRecord) -> bool:
        return True

    def credential_values(self, record: AuthRecord) -> set[str]:
        return credential_values(record)

    def describe(self, record: AuthRecord) -> AccountDesc:
        return AccountDesc(type="api", email=None, account_id=key_account_hint(record), expires=None)

    def validate(self, record: AuthRecord) -> Validity:
        return Validity.OK if record.type == "api" else Validity.INVALID

    def refresh(self, record: AuthRecord) -> AuthRecord | None:
        return None  # API keys don't expire/rotate; nothing to refresh

    def fetch_usage(self, record: AuthRecord) -> usage.UsageSnapshot | None:
        return None  # no usage endpoint for an arbitrary API provider (usage_record_types is empty)
