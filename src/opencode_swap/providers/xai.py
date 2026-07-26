"""xAI API/OAuth handling with guarded stable JWT subject identity."""

from __future__ import annotations

import time

from opencode_swap.exceptions import SchemaError
from opencode_swap.models import AccountDesc, AuthRecord, JsonObject, Validity
from opencode_swap.oauth_jwt import decode_claims
from opencode_swap.providers.common import credential_values, extract_raw, is_json_number, validate_api, validate_oauth


class XaiProvider:
    id = "xai"

    def extract(self, auth: JsonObject) -> AuthRecord | None:
        raw = extract_raw(auth, self.id)
        if raw is None:
            return None
        if raw.get("type") == "api":
            return validate_api(raw, self.id)
        if raw.get("type") == "oauth":
            return validate_oauth(raw, self.id)
        raise SchemaError("xai auth type is not supported by opencode-swap")

    def splice(self, auth: JsonObject, record: AuthRecord) -> JsonObject:
        return {**auth, self.id: dict(record.raw)}

    def identity(self, record: AuthRecord) -> str:
        if record.type == "api":
            key = record.raw.get("key")
            return f"api-key\0{key if isinstance(key, str) else ''}"
        access = record.raw.get("access")
        claims = decode_claims(access if isinstance(access, str) else "")
        subject = claims.get("sub")
        issuer = claims.get("iss", "")
        if not isinstance(subject, str) or not subject or not isinstance(issuer, str) or not issuer:
            raise SchemaError("xai oauth access token has no stable JWT subject; refusing unsafe account switching")
        return f"oauth-subject\0{issuer}\0{subject}"

    def identity_is_stable(self, record: AuthRecord) -> bool:
        return True

    def credential_values(self, record: AuthRecord) -> set[str]:
        return credential_values(record)

    def describe(self, record: AuthRecord) -> AccountDesc:
        expires = record.raw.get("expires")
        return AccountDesc(type=record.type, email=None, account_id=None, expires=expires if is_json_number(expires) else None)

    def validate(self, record: AuthRecord) -> Validity:
        if record.type == "api":
            return Validity.OK
        expires = record.raw.get("expires")
        if not is_json_number(expires):
            return Validity.INVALID
        return Validity.EXPIRED if expires < time.time() * 1000 else Validity.OK
