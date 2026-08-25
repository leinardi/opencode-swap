"""Live OpenAI/ChatGPT usage lookup, for `opencode-swap list --usage`.

Verified against opencode-balancer's implementation
(src/core/usage/providers/openai.ts), not guessed: for an OAuth account, GET
https://chatgpt.com/backend-api/wham/usage with the account's own access
token as Bearer auth (plus a ChatGPT-Account-Id header when the account id
is known). The response's `rate_limit` dict carries one or more rate-limit
windows -- OpenAI added a 5-hour window alongside the pre-existing 7-day one
in 2026-08, each shaped the same: `used_percent`, `reset_at`,
`limit_window_seconds`.

Third-party clients of this same endpoint (surveyed on GitHub while adding
the 5h window here) do NOT agree on which key holds which window --
`primary_window`/`secondary_window` is observed both ways round, classified
only by `limit_window_seconds` (18000 = 5h, 604800 = 7d). So this module
never keys into `rate_limit` by name: it walks every value under
`rate_limit`, keeps whatever parses as a window (dict with a usable
`used_percent`), and sorts what it finds by `limit_window_seconds`. That
also means it survives OpenAI going back to one window, adding a third, or
renaming the keys again, with no code change here -- and it naturally skips
sibling blocks like `code_review_rate_limit` (a separate quota some clients
also parse) since those aren't inside `rate_limit`.

No caching, no polling, no persistence, and never called unless the caller
explicitly opts in -- every other opencode-swap command is intentionally
local/offline-only. `oauth_refresh.py` (a standalone OAuth token refresh,
triggered from inside `Switcher.fetch_usage`/`refresh_account` for a saved
account OpenCode doesn't currently have live) is the only other exception;
both are opt-in on purpose, one from `--usage`, the other from the explicit
`refresh` command.

The CLI itself only calls this with `--usage`. The bundled OpenCode TUI
plugin (integrations/opencode-tui-plugin) is a different caller with its own
default: it opts in automatically and polls every 60 seconds for the active
managed account, unless its own `usage` option is set to `false`. See that
plugin's README "Network access" section for what that sends where.
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from dataclasses import dataclass

CHATGPT_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
_TIMEOUT = 5.0
_USER_AGENT = "opencode-swap"
_MAX_RESET_AT_MILLIS = 253_402_300_000_000  # 9999-12-31, safely datetime-compatible

_PLAN_NAMES = {
    "enterprise": "ChatGPT Enterprise",
    "plus": "ChatGPT Plus",
    "pro": "ChatGPT Pro",
    "team": "ChatGPT Team",
}


@dataclass(frozen=True)
class UsageWindow:
    used_percent: float | None
    reset_at: float | None  # epoch ms
    window_seconds: float | None


@dataclass(frozen=True)
class UsageSnapshot:
    available: bool
    plan_name: str | None = None
    windows: tuple[UsageWindow, ...] = ()
    message: str = ""


def _plan_name(plan_type: object) -> str | None:
    if not isinstance(plan_type, str) or not plan_type.strip():
        return None
    key = plan_type.strip().lower()
    return _PLAN_NAMES.get(key)


def _reset_at_millis(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value > _MAX_RESET_AT_MILLIS:
        return None
    # opencode-balancer's own heuristic: values already in the millisecond
    # range are passed through, smaller ones are assumed to be seconds.
    millis = float(value) if value > 1_000_000_000_000 else float(value) * 1000
    return millis if millis <= _MAX_RESET_AT_MILLIS else None


def _parse_window(value: object) -> UsageWindow | None:
    """Parse one candidate rate-limit window. Returns None when `value` isn't
    dict-shaped or has no usable `used_percent` -- the two signals used to
    tell an actual window apart from an unrelated `rate_limit` entry (see
    module docstring on key-agnostic discovery)."""
    if not isinstance(value, dict):
        return None

    used_percent = value.get("used_percent")
    valid_percent = (
        isinstance(used_percent, (int, float))
        and not isinstance(used_percent, bool)
        and (not isinstance(used_percent, float) or math.isfinite(used_percent))
        and 0 <= used_percent <= 100
    )
    if not valid_percent:
        return None

    window_seconds = value.get("limit_window_seconds")
    valid_window = (
        isinstance(window_seconds, (int, float))
        and not isinstance(window_seconds, bool)
        and (not isinstance(window_seconds, float) or math.isfinite(window_seconds))
        and window_seconds > 0
    )
    return UsageWindow(
        used_percent=used_percent,
        reset_at=_reset_at_millis(value.get("reset_at")),
        window_seconds=window_seconds if valid_window else None,
    )


def _sort_key(item: tuple[str, UsageWindow]) -> tuple[float, str]:
    key, window = item
    # Windows with no usable duration sort last (inf); ties (including two
    # unknown-duration windows) break on key name so output is deterministic.
    duration = window.window_seconds if window.window_seconds is not None else math.inf
    return (duration, key)


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
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return UsageSnapshot(available=False, message=str(exc))

    if not isinstance(body, dict):
        return UsageSnapshot(available=False, message="unexpected response shape")

    rate_limit = body.get("rate_limit")
    candidates = rate_limit.items() if isinstance(rate_limit, dict) else []
    parsed = [(key, window) for key, value in candidates if (window := _parse_window(value)) is not None]
    windows = tuple(window for _key, window in sorted(parsed, key=_sort_key))

    return UsageSnapshot(
        available=True,
        plan_name=_plan_name(body.get("plan_type")),
        windows=windows,
        message="ok",
    )
