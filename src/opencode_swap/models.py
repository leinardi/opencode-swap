"""Data models for opencode-swap."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import cast

#: Account name validation: lowercase letters/digits/-/_/., non-empty, not
#: leading '-' (argparse would read it as a flag) or '.' (keeps the file-
#: fallback secret-store filename derived from this name free of anything
#: that could resemble a relative path component).
_NAME_RE = re.compile(r"^[a-z0-9_.-]+$")

type JsonObject = dict[str, object]


def normalize_account_name(name: str) -> str:
    """Lowercase and validate a proposed account name; raise ValueError if invalid."""
    normalized = name.strip().lower()
    if not normalized:
        raise ValueError("account name cannot be empty")
    if normalized.startswith("-"):
        raise ValueError(f"account name '{name}' cannot start with '-' (would be read as a command flag)")
    if normalized.startswith("."):
        raise ValueError(f"account name '{name}' cannot start with '.'")
    if not _NAME_RE.match(normalized):
        raise ValueError(f"account name '{name}' may only contain letters, digits, '-', '_', and '.'")
    return normalized


class Validity(Enum):
    """Result of validating an auth record."""

    OK = auto()
    EXPIRED = auto()
    INVALID = auto()


@dataclass(frozen=True)
class AuthRecord:
    """A single provider's entry from OpenCode's auth.json.

    ``raw`` is the full original dict, preserved verbatim so a round-trip
    (read -> store -> splice back in) never drops a field opencode-swap
    doesn't know about — important since the schema is undocumented and can
    gain fields across OpenCode versions (see SchemaError).
    """

    type: str
    raw: JsonObject


@dataclass(frozen=True)
class AccountDesc:
    """Human-facing description of an account, safe to print (no secrets)."""

    type: str
    email: str | None
    account_id: str | None
    expires: float | None


@dataclass(frozen=True)
class AccountMeta:
    """Non-secret registry metadata for one saved account.

    Deliberately excludes the identity string used for switch-time matching
    (see providers/base.py Provider.identity): when no accountId claim is
    available, identity falls back to the raw refresh token, which must
    never land in the non-secret registry. Identity is recomputed on demand
    from the secret-store-held record instead.
    """

    name: str
    provider: str
    type: str
    account_id: str | None
    email: str | None
    added: str

    def to_dict(self) -> JsonObject:
        return {
            "provider": self.provider,
            "type": self.type,
            "accountId": self.account_id,
            "email": self.email,
            "added": self.added,
        }

    @classmethod
    def from_dict(cls, name: str, data: JsonObject) -> AccountMeta:
        return cls(
            name=name,
            provider=cast(str, data["provider"]),
            type=cast(str, data.get("type", "oauth")),
            account_id=cast(str | None, data.get("accountId")),
            email=cast(str | None, data.get("email")),
            added=cast(str, data.get("added", "")),
        )


@dataclass
class SwitchTransaction:
    """Tracks completed steps of an `use_account` switch so switcher.py can
    roll back the one step that actually mutates OpenCode's live state
    (auth.json) if a later step fails. See switcher.py's use_account/
    _rollback for why only "auth_written" needs an active rollback action:
    every step before it either hasn't touched live state yet, or (for the
    atomic auth.json write itself) is guaranteed by atomic_write_auth to
    leave the original file untouched on failure.
    """

    original_auth: JsonObject
    original_active: str | None
    completed_steps: list[str] = field(default_factory=list)

    def record_step(self, step: str) -> None:
        self.completed_steps.append(step)


class Platform(Enum):
    """Supported platforms."""

    MACOS = auto()
    LINUX = auto()
    UNKNOWN = auto()

    @classmethod
    def detect(cls) -> Platform:
        """Detect current platform.

        Uses sys.platform rather than platform.system(), which can shell out
        to external tools depending on OS (see claude-swap's Platform.detect
        docstring for the Windows WMI-hang precedent this avoids).
        """
        platform_name = sys.platform.lower()
        if platform_name == "darwin":
            return cls.MACOS
        if platform_name.startswith("linux"):
            return cls.LINUX
        return cls.UNKNOWN
