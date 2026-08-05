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

import math
import time

from opencode_swap import oauth_refresh
from opencode_swap.exceptions import SchemaError
from opencode_swap.models import AccountDesc, AuthRecord, JsonObject, Validity
from opencode_swap.oauth_jwt import decode_claims, extract_account_id, extract_email
from opencode_swap.providers.common import credential_values, is_json_number, require_expiry

PROVIDER_ID = "openai"


def _require_str(raw: JsonObject, field: str, entry_type: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value:
        raise SchemaError(f"openai {entry_type} entry missing/invalid field {field!r}")
    return value


def _optional_str(raw: JsonObject, field: str, entry_type: str) -> str | None:
    if field not in raw:
        return None
    value = raw[field]
    if not isinstance(value, str):
        raise SchemaError(f"openai {entry_type} entry has invalid field {field!r}")
    return value


def _json_value(value: object) -> bool:
    if value is None or isinstance(value, (str, bool)):
        return True
    if is_json_number(value):
        return True
    if isinstance(value, list):
        return all(_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _json_value(item) for key, item in value.items())
    return False


def _safe_display(value: object, record: JsonObject) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if any(ord(char) < 32 or 127 <= ord(char) < 160 for char in value):
        return None
    if any(
        secret and secret in value for secret in (record.get(field) for field in ("refresh", "access", "key", "token")) if isinstance(secret, str)
    ):
        return None
    return value


class OpenAiProvider:
    id = PROVIDER_ID

    def extract(self, auth: JsonObject) -> AuthRecord | None:
        raw = auth.get(PROVIDER_ID)
        if raw is None:
            return None
        if not isinstance(raw, dict) or "type" not in raw:
            raise SchemaError("openai entry is not a recognizable auth record")
        if not _json_value(raw):
            raise SchemaError("openai entry contains values not representable as JSON")

        entry_type = raw["type"]
        if entry_type == "oauth":
            _require_str(raw, "refresh", "oauth")
            _require_str(raw, "access", "oauth")
            require_expiry(raw, PROVIDER_ID)
            _optional_str(raw, "accountId", "oauth")
            _optional_str(raw, "enterpriseUrl", "oauth")
        elif entry_type == "api":
            _require_str(raw, "key", "api")
            metadata = raw.get("metadata")
            if "metadata" in raw and (not isinstance(metadata, dict) or not _json_value(metadata)):
                raise SchemaError("openai api entry has invalid field 'metadata'")
        elif entry_type == "wellknown":
            _require_str(raw, "key", "wellknown")
            _require_str(raw, "token", "wellknown")
        else:
            raise SchemaError("unknown openai auth type")

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
        raise SchemaError("unknown openai auth type")

    def identity_is_stable(self, record: AuthRecord) -> bool:
        if record.type != "oauth":
            return True
        return self.identity(record).startswith("oauth-account\0")

    def credential_values(self, record: AuthRecord) -> set[str]:
        return credential_values(record)

    def describe(self, record: AuthRecord) -> AccountDesc:
        if record.type == "oauth":
            access = record.raw.get("access")
            claims = decode_claims(access if isinstance(access, str) else "")
            account_id = record.raw.get("accountId")
            expires = record.raw.get("expires")
            return AccountDesc(
                type="oauth",
                email=_safe_display(extract_email(claims), record.raw),
                account_id=_safe_display(account_id if isinstance(account_id, str) else extract_account_id(claims), record.raw),
                expires=expires if isinstance(expires, (int, float)) else None,
            )
        if record.type == "api":
            return AccountDesc(type="api", email=None, account_id=None, expires=None)
        return AccountDesc(type="wellknown", email=None, account_id=None, expires=None)

    def validate(self, record: AuthRecord) -> Validity:
        if record.type == "oauth":
            expires = record.raw.get("expires")
            if isinstance(expires, bool) or not isinstance(expires, (int, float)) or not math.isfinite(expires):
                return Validity.INVALID
            if expires < time.time() * 1000:
                return Validity.EXPIRED
            return Validity.OK
        if record.type in ("api", "wellknown"):
            return Validity.OK
        return Validity.INVALID

    def refresh(self, record: AuthRecord) -> AuthRecord | None:
        if record.type != "oauth":
            return None
        refresh_token = record.raw.get("refresh")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise SchemaError("openai oauth entry missing/invalid field 'refresh'")
        tokens = oauth_refresh.refresh_openai_oauth(refresh_token, now_ms=time.time() * 1000)
        new_raw = dict(record.raw)
        new_raw["access"] = tokens.access
        new_raw["refresh"] = tokens.refresh
        new_raw["expires"] = tokens.expires
        account_id = tokens.account_id or record.raw.get("accountId")
        if account_id:
            new_raw["accountId"] = account_id
        return AuthRecord(type="oauth", raw=new_raw)
