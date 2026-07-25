"""Live OpenAI/ChatGPT usage lookup, for `opencode-swap list --usage`.

Verified against opencode-balancer's implementation
(src/core/usage/providers/openai.ts), not guessed: for an OAuth account, GET
https://chatgpt.com/backend-api/wham/usage with the account's own access
token as Bearer auth (plus a ChatGPT-Account-Id header when the account id
is known). The response's rate_limit.primary_window carries used_percent
and reset_at.

No caching, no polling, no persistence, and never called unless the caller
explicitly opts in — every other opencode-swap command is intentionally
local/offline-only, and this is the one exception, kept opt-in on purpose.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

CHATGPT_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
_TIMEOUT = 5.0
_USER_AGENT = "opencode-swap"

_PLAN_NAMES = {
    "enterprise": "ChatGPT Enterprise",
    "plus": "ChatGPT Plus",
    "pro": "ChatGPT Pro",
    "team": "ChatGPT Team",
}


@dataclass(frozen=True)
class UsageSnapshot:
    available: bool
    used_percent: float | None = None
    plan_name: str | None = None
    reset_at: float | None = None  # epoch ms
    message: str = ""


def _plan_name(plan_type: object) -> str | None:
    if not isinstance(plan_type, str) or not plan_type.strip():
        return None
    key = plan_type.strip().lower()
    return _PLAN_NAMES.get(key, plan_type)


def _reset_at_millis(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        return None
    # opencode-balancer's own heuristic: values already in the millisecond
    # range are passed through, smaller ones are assumed to be seconds.
    return float(value) if value > 1_000_000_000_000 else float(value) * 1000


def fetch_openai_oauth_usage(access_token: str, account_id: str | None) -> UsageSnapshot:
    """Fetch live ChatGPT usage for an OAuth account. Never raises — network
    failures, timeouts, and unexpected response shapes all come back as an
    `available=False` snapshot instead."""
    headers = {"Authorization": f"Bearer {access_token}", "User-Agent": _USER_AGENT}
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id
    request = urllib.request.Request(CHATGPT_USAGE_URL, headers=headers)

    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            body = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return UsageSnapshot(available=False, message=f"HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return UsageSnapshot(available=False, message=str(exc))

    if not isinstance(body, dict):
        return UsageSnapshot(available=False, message="unexpected response shape")

    rate_limit = body.get("rate_limit")
    primary = rate_limit.get("primary_window") if isinstance(rate_limit, dict) else None
    used_percent = primary.get("used_percent") if isinstance(primary, dict) else None
    reset_at = _reset_at_millis(primary.get("reset_at")) if isinstance(primary, dict) else None

    return UsageSnapshot(
        available=True,
        used_percent=used_percent if isinstance(used_percent, (int, float)) else None,
        plan_name=_plan_name(body.get("plan_type")),
        reset_at=reset_at,
        message="ok",
    )
