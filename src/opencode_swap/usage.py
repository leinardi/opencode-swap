"""Live provider usage lookup, for `opencode-swap list --usage`.

Two providers are supported, each against an endpoint its own first-party
client already uses:

**OpenAI/ChatGPT** (OAuth accounts). Verified against opencode-balancer's
implementation (src/core/usage/providers/openai.ts), not guessed: GET
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

**Z.AI GLM Coding Plan** (`zai-coding-plan` API-key accounts). GET
https://api.z.ai/api/monitor/usage/quota/limit with the account's own API
key as Bearer auth -- the undocumented endpoint z.ai's own subscription UI
calls. `data.limits[]` carries the quota windows; each `CREDIT_LIMIT` entry
(older responses: `TOKENS_LIMIT`, and the kind under a `name` key rather than
`type` -- openusage honors both) is a percentage window whose length is
`number * unit`, where `unit` is z.ai's period enum -- 3=hour, 4=day,
5=month, 6=week, read from z.ai's frontend source and cross-checked against
openusage's ZAIUsageMapper.swift (`classifyTokenWindow`). So the 5h/7d
windows are derived from the payload, not hardcoded: an unfamiliar `unit`
just yields a window with no duration label, same as an unfamiliar OpenAI
window length. `TIME_LIMIT` entries are a monthly web-search *count*, not a
rate-limit window, and are skipped. `data.level` is the plan tier.

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

import http.client
import json
import math
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import TypeGuard

CHATGPT_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
ZAI_USAGE_URL = "https://api.z.ai/api/monitor/usage/quota/limit"
_TIMEOUT = 5.0
_USER_AGENT = "opencode-swap"
_MAX_RESET_AT_MILLIS = 253_402_300_000_000  # 9999-12-31, safely datetime-compatible

_PLAN_NAMES = {
    "enterprise": "ChatGPT Enterprise",
    "plus": "ChatGPT Plus",
    "pro": "ChatGPT Pro",
    "team": "ChatGPT Team",
}

# z.ai's `unit` period enum -> seconds. From z.ai's frontend source, matched
# against openusage's ZAIUsageMapper.swift. Unknown units are left unlabeled
# rather than guessed (see module docstring).
_ZAI_UNIT_SECONDS = {3: 3600, 4: 86_400, 5: 2_592_000, 6: 604_800}
_ZAI_PERCENTAGE_TYPES = {"CREDIT_LIMIT", "TOKENS_LIMIT"}
_ZAI_PLAN_NAMES = {
    "lite": "GLM Coding Lite",
    "pro": "GLM Coding Pro",
    "max": "GLM Coding Max",
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


def _zai_plan_name(level: object) -> str | None:
    if not isinstance(level, str) or not level.strip():
        return None
    return _ZAI_PLAN_NAMES.get(level.strip().lower())


def _finite_number(value: object) -> TypeGuard[int | float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return not isinstance(value, float) or math.isfinite(value)


def _used_percent(value: object) -> float | None:
    """A `used_percent`/`percentage` value coerced to a 0-100 float, or None
    when it isn't a finite number in range -- the signal both providers use
    to tell an actual quota window from an unrelated sibling entry."""
    if not _finite_number(value) or not 0 <= value <= 100:
        return None
    return float(value)


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


def _fetch_json(url: str, headers: dict[str, str]) -> tuple[object | None, str]:
    """GET `url` and parse JSON. Returns `(body, "ok")` on success, or
    `(None, message)` on any failure -- never raises, and the message never
    contains a header value (so a Bearer token can't leak into output)."""
    try:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return json.loads(response.read()), "ok"
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except json.JSONDecodeError:
        return None, "invalid JSON in response"
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError) as exc:
        return None, str(exc)
    except http.client.HTTPException:
        # e.g. IncompleteRead from response.read() on a truncated body --
        # not an OSError, so it would otherwise escape. Fixed message.
        return None, "HTTP protocol error"
    except ValueError:
        # http.client raises ValueError for a malformed header value, and the
        # exception message embeds that value verbatim -- which here is the
        # Bearer credential. Never stringify it. (JSONDecodeError and
        # UnicodeDecodeError are ValueError subclasses handled above.)
        return None, "malformed request"


def _parse_window(value: object) -> UsageWindow | None:
    """Parse one candidate rate-limit window. Returns None when `value` isn't
    dict-shaped or has no usable `used_percent` -- the two signals used to
    tell an actual window apart from an unrelated `rate_limit` entry (see
    module docstring on key-agnostic discovery)."""
    if not isinstance(value, dict):
        return None

    used_percent = _used_percent(value.get("used_percent"))
    if used_percent is None:
        return None

    window_seconds = value.get("limit_window_seconds")
    valid_window = _finite_number(window_seconds) and window_seconds > 0
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

    body, message = _fetch_json(CHATGPT_USAGE_URL, headers)
    if body is None:
        return UsageSnapshot(available=False, message=message)
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


class _ZaiResponseError(Exception):
    """Internal: a recognized z.ai quota record that can't be parsed. Caught
    inside `fetch_zai_usage` and turned into an unavailable snapshot -- never
    escapes the module."""


def _zai_window(entry: object) -> tuple[float, UsageWindow] | None:
    """Parse one `data.limits[]` percentage entry into `(sort_seconds,
    window)`. Returns None when `entry` isn't a recognized percentage-quota
    entry at all. Raises `_ZaiResponseError` when it *is* one (by `type` or
    the legacy `name` key) but its `percentage` can't be read -- a broken
    quota record makes the whole snapshot untrustworthy rather than silently
    short.

    An unfamiliar `unit` is not an error: the window is kept with no
    duration label (see module docstring), same as an unfamiliar OpenAI
    window length."""
    if not isinstance(entry, dict):
        return None
    kind = entry.get("type") or entry.get("name")  # openusage honors both keys
    if kind not in _ZAI_PERCENTAGE_TYPES:
        return None

    used_percent = _used_percent(entry.get("percentage"))
    if used_percent is None:
        raise _ZaiResponseError

    unit, number = entry.get("unit"), entry.get("number")
    window_seconds: float | None = None
    if isinstance(unit, int) and not isinstance(unit, bool) and unit in _ZAI_UNIT_SECONDS and _finite_number(number) and number > 0:
        window_seconds = _ZAI_UNIT_SECONDS[unit] * float(number)

    window = UsageWindow(
        used_percent=used_percent,
        reset_at=_reset_at_millis(entry.get("nextResetTime")),
        window_seconds=window_seconds,
    )
    return (window_seconds if window_seconds is not None else math.inf, window)


def fetch_zai_usage(api_key: str) -> UsageSnapshot:
    """Fetch live Z.AI GLM Coding Plan quota for an API-key account. Never
    raises -- network failures, an inactive coding plan, and unexpected
    response shapes all come back as an `available=False` snapshot."""
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json", "User-Agent": _USER_AGENT}

    body, message = _fetch_json(ZAI_USAGE_URL, headers)
    if body is None:
        return UsageSnapshot(available=False, message=message)
    if not isinstance(body, dict):
        return UsageSnapshot(available=False, message="unexpected response shape")

    # z.ai answers 2xx with `success: false` for real errors, not just the
    # no-coding-plan case (observed: "service unavailable"). Every one of
    # those is a failed lookup, not an empty-but-valid snapshot. The message
    # is fixed text, never the server's `msg` -- that string is not trusted
    # to be credential-free.
    if body.get("success") is False:
        note = body.get("msg")
        plan_gone = isinstance(note, str) and "coding plan" in note.lower()
        return UsageSnapshot(available=False, message="no active GLM coding plan" if plan_gone else "z.ai rejected the request")

    data = body.get("data")
    try:
        limits = data.get("limits") if isinstance(data, dict) else None
        if limits is None:  # openusage also tolerates the limits array at the root
            limits = body.get("limits")
        if not isinstance(limits, list):
            raise _ZaiResponseError
        parsed = [result for entry in limits if (result := _zai_window(entry)) is not None]
    except _ZaiResponseError:
        return UsageSnapshot(available=False, message="unexpected response shape")
    windows = tuple(window for _seconds, window in sorted(parsed, key=lambda item: item[0]))
    level = data.get("level") if isinstance(data, dict) else None

    return UsageSnapshot(available=True, plan_name=_zai_plan_name(level), windows=windows, message="ok")
