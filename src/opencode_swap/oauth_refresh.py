"""Standalone OpenAI OAuth refresh, for accounts opencode-swap manages but
OpenCode isn't currently pointed at.

Verified against OpenCode's own implementation
(packages/opencode/src/plugin/openai/codex.ts:125-139,
refreshAccessToken), not guessed: POST {issuer}/oauth/token,
grant_type=refresh_token, against the same public PKCE client id OpenCode
itself uses (docs/opencode-auth.md#loading-and-refresh). There is no client
secret to protect; this is the same request OpenCode makes on every expired
request, just triggered by opencode-swap instead.

Refresh tokens are single-use: OpenAI issues a new one on every grant and
invalidates the old one (docs/architecture.md#why-sync-back-is-mandatory).
A caller that fails to persist a successful result here has permanently
lost access to the account -- the old token is already dead. See
switcher.py's use of this module for the locking/persistence discipline
that keeps that window as short as possible.

Unlike usage.py, this module raises on failure rather than returning an
"unavailable" sentinel: a caller deciding whether an account's refresh
token is dead (needs re-login) versus merely offline needs to be able to
tell those apart.
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from opencode_swap.exceptions import RefreshError
from opencode_swap.oauth_jwt import decode_claims, extract_account_id

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"  # codex.ts:10, public PKCE client, no secret
ISSUER = "https://auth.openai.com"  # codex.ts:11
_TOKEN_PATH = "/oauth/token"
_TIMEOUT = 10.0
_USER_AGENT = "opencode-swap"
_DEFAULT_EXPIRES_IN = 3600  # codex.ts:372, `tokens.expires_in ?? 3600`


@dataclass(frozen=True)
class RefreshedTokens:
    access: str
    refresh: str
    expires: float  # epoch ms
    account_id: str | None


def _valid_expires_in(value: object) -> float | None:
    """A JSON `expires_in` is otherwise-untrusted server input: it can carry
    a huge literal (`1e400`) that overflows to `inf`, or `NaN`/`Infinity`
    directly (Python's json module accepts both by default). Either would
    silently produce a non-finite `expires` timestamp, which then fails
    `require_expiry`'s finite check the next time this record is loaded
    (providers/common.py) -- by then the old refresh token this grant
    consumed is already dead, permanently bricking the account. Reject here
    instead, before any of that is persisted.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        if not math.isfinite(value) or value <= 0:
            return None
    except OverflowError:
        return None
    return float(value)


def _account_id_from_tokens(id_token: object, access_token: str) -> str | None:
    """Same fallback order as codex.ts's extractAccountId: id_token claims
    first, then access_token claims."""
    if isinstance(id_token, str) and id_token:
        account_id = extract_account_id(decode_claims(id_token))
        if account_id:
            return account_id
    return extract_account_id(decode_claims(access_token))


def refresh_openai_oauth(refresh_token: str, *, now_ms: float, issuer: str = ISSUER) -> RefreshedTokens:
    """Exchange a refresh token for a rotated access/refresh pair.

    Raises RefreshError on any failure: rejected grant, network error,
    timeout, or a response that doesn't match the expected shape. Never
    includes the response body or the refresh token in the error message
    (docs/security.md: CLI output must never include a secret).
    """
    body = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
        }
    ).encode()
    request = urllib.request.Request(
        f"{issuer}{_TOKEN_PATH}",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": _USER_AGENT},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 401):
            raise RefreshError(
                "refresh token was rejected (it may already have been used elsewhere, or expired); "
                "run `opencode auth login` for this account, then `opencode-swap add <provider> <name>` again"
            ) from exc
        raise RefreshError(f"token refresh failed (HTTP {exc.code})") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RefreshError(f"token refresh failed: {exc.reason if isinstance(exc, urllib.error.URLError) else exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RefreshError(f"token refresh returned an unreadable response: {exc}") from exc

    if not isinstance(payload, dict):
        raise RefreshError("token refresh returned an unexpected response shape")

    access_token = payload.get("access_token")
    new_refresh_token = payload.get("refresh_token")
    if not isinstance(access_token, str) or not access_token:
        raise RefreshError("token refresh response is missing 'access_token'")
    if not isinstance(new_refresh_token, str) or not new_refresh_token:
        raise RefreshError("token refresh response is missing 'refresh_token'")

    expires_in = _valid_expires_in(payload.get("expires_in"))
    if expires_in is None:
        expires_in = float(_DEFAULT_EXPIRES_IN)

    expires = now_ms + expires_in * 1000
    if not math.isfinite(expires):
        raise RefreshError("token refresh returned an unusable expiry")

    return RefreshedTokens(
        access=access_token,
        refresh=new_refresh_token,
        expires=expires,
        account_id=_account_id_from_tokens(payload.get("id_token"), access_token),
    )
