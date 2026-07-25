"""OpenAI provider: reads/writes the ``"openai"`` key of OpenCode's auth.json.

Record shapes verified against OpenCode source
(packages/opencode/src/auth/index.ts:14-33, the Oauth/Api/WellKnown union):

    oauth:     {type:"oauth", refresh:str, access:str, expires:number,
                accountId?:str, enterpriseUrl?:str}
    api:       {type:"api", key:str, metadata?:dict}
    wellknown: {type:"wellknown", key:str, token:str}

Identity derivation mirrors opencode-balancer's authIdentityKey
(src/core/pending.ts:116-123): prefer the stable account id, fall back to
the refresh token itself so two records for the same account are still
recognized as the same identity even before an accountId claim is known.
"""

from __future__ import annotations

import time

from opencode_swap.exceptions import SchemaError
from opencode_swap.models import AccountDesc, AuthRecord, JsonObject, Validity
from opencode_swap.oauth_jwt import decode_claims, extract_account_id, extract_email

PROVIDER_ID = "openai"


def _require_str(raw: JsonObject, field: str, entry_type: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value:
        raise SchemaError(f"openai {entry_type} entry missing/invalid field {field!r}")
    return value


class OpenAiProvider:
    id = PROVIDER_ID

    def keys(self) -> list[str]:
        return [PROVIDER_ID]

    def extract(self, auth: JsonObject) -> AuthRecord | None:
        raw = auth.get(PROVIDER_ID)
        if raw is None:
            return None
        if not isinstance(raw, dict) or "type" not in raw:
            raise SchemaError("openai entry is not a recognizable auth record")

        entry_type = raw["type"]
        if entry_type == "oauth":
            _require_str(raw, "refresh", "oauth")
            _require_str(raw, "access", "oauth")
            expires = raw.get("expires")
            if not isinstance(expires, (int, float)) or expires < 0:
                raise SchemaError("openai oauth entry has invalid 'expires'")
        elif entry_type == "api":
            _require_str(raw, "key", "api")
        elif entry_type == "wellknown":
            _require_str(raw, "key", "wellknown")
            _require_str(raw, "token", "wellknown")
        else:
            raise SchemaError(f"unknown openai auth type: {entry_type!r}")

        return AuthRecord(type=entry_type, raw=dict(raw))

    def splice(self, auth: JsonObject, record: AuthRecord) -> JsonObject:
        new_auth = dict(auth)
        new_auth[PROVIDER_ID] = dict(record.raw)
        return new_auth

    def identity(self, record: AuthRecord) -> str:
        if record.type == "oauth":
            account_id = record.raw.get("accountId")
            if not isinstance(account_id, str) or not account_id:
                access = record.raw.get("access")
                claims = decode_claims(access if isinstance(access, str) else "")
                account_id = extract_account_id(claims)
            if account_id:
                return f"oauth-account\0{account_id}"
            refresh = record.raw.get("refresh")
            return f"oauth-refresh\0{refresh if isinstance(refresh, str) else ''}"
        if record.type == "api":
            key = record.raw.get("key")
            return f"api-key\0{key if isinstance(key, str) else ''}"
        if record.type == "wellknown":
            key = record.raw.get("key")
            token = record.raw.get("token")
            return f"wellknown\0{key if isinstance(key, str) else ''}\0{token if isinstance(token, str) else ''}"
        raise SchemaError(f"unknown openai auth type: {record.type!r}")

    def describe(self, record: AuthRecord) -> AccountDesc:
        if record.type == "oauth":
            access = record.raw.get("access")
            claims = decode_claims(access if isinstance(access, str) else "")
            account_id = record.raw.get("accountId")
            expires = record.raw.get("expires")
            return AccountDesc(
                type="oauth",
                email=extract_email(claims),
                account_id=account_id if isinstance(account_id, str) else extract_account_id(claims),
                expires=expires if isinstance(expires, (int, float)) else None,
            )
        if record.type == "api":
            return AccountDesc(type="api", email=None, account_id=None, expires=None)
        return AccountDesc(type="wellknown", email=None, account_id=None, expires=None)

    def validate(self, record: AuthRecord) -> Validity:
        if record.type == "oauth":
            expires = record.raw.get("expires")
            if not isinstance(expires, (int, float)):
                return Validity.INVALID
            if expires < time.time() * 1000:
                return Validity.EXPIRED
            return Validity.OK
        if record.type in ("api", "wellknown"):
            return Validity.OK
        return Validity.INVALID
