"""Exception hierarchy for opencode-swap."""

from __future__ import annotations


class OpenCodeSwapError(Exception):
    """Base class for all opencode-swap errors."""


class AuthFileError(OpenCodeSwapError):
    """auth.json is missing, unreadable, or not valid JSON."""


class BackupError(OpenCodeSwapError):
    """A recovery snapshot is unreadable or has an invalid top-level shape."""


class SchemaError(OpenCodeSwapError):
    """A provider's entry in auth.json doesn't match a known shape.

    Raised instead of silently dropping or guessing at the data (fail-safe:
    refuse to operate on state we don't understand rather than risk
    corrupting it). OpenCode itself is lenient here — its own Auth.all()
    silently drops entries that fail schema decode (auth/index.ts:66) — but
    opencode-swap is about to overwrite that state, so it holds itself to a
    stricter standard.
    """


class LockError(OpenCodeSwapError):
    """Could not acquire opencode-swap's own cross-process file lock."""


class RegistryError(OpenCodeSwapError):
    """opencode-swap's own account registry is missing, corrupt, or the
    requested operation doesn't make sense against its current state."""


class SecretStoreError(OpenCodeSwapError):
    """A secret-store operation failed on every backend available to it."""


class AccountExistsError(OpenCodeSwapError):
    """`add`/`rename` would create a name or identity collision."""
