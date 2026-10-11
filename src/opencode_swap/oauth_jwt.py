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

"""Decode claims from an OpenAI OAuth access-token JWT. No network calls.

Mirrors OpenCode's own claim extraction (opencode's
packages/opencode/src/plugin/openai/codex.ts:47-76, parseJwtClaims /
extractAccountIdFromClaims) so opencode-swap derives the same accountId
OpenCode would from the same token. Not signature-verified: we never trust
these claims for authorization, only use them to label/identify an account
that OpenCode itself already trusts.
"""

from __future__ import annotations

import base64
import json

from opencode_swap.models import JsonObject


def decode_claims(token: str) -> JsonObject:
    """Decode a JWT's payload claims. Returns {} if token isn't a valid JWT.

    Empirically (see M0 spike), a real OpenAI access token does not always
    carry an ``email`` claim — callers must treat email as optional.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    payload = parts[1]
    padded = payload + "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded)
        claims = json.loads(decoded)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def extract_account_id(claims: JsonObject) -> str | None:
    """Extract chatgpt_account_id from JWT claims, same fallback order as codex.ts."""
    direct = claims.get("chatgpt_account_id")
    if isinstance(direct, str) and direct:
        return direct
    nested = claims.get("https://api.openai.com/auth")
    if isinstance(nested, dict):
        account_id = nested.get("chatgpt_account_id")
        if isinstance(account_id, str) and account_id:
            return account_id
    orgs = claims.get("organizations")
    if isinstance(orgs, list) and orgs and isinstance(orgs[0], dict):
        account_id = orgs[0].get("id")
        if isinstance(account_id, str):
            return account_id
    return None


def extract_email(claims: JsonObject) -> str | None:
    """Extract an email claim if present (not guaranteed on OpenAI access tokens)."""
    email = claims.get("email")
    if isinstance(email, str) and email:
        return email
    nested = claims.get("https://api.openai.com/auth")
    if isinstance(nested, dict):
        email = nested.get("email")
        if isinstance(email, str) and email:
            return email
    return None
