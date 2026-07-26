"""The Provider seam: the only part of opencode-swap that varies per OpenCode
provider/account type.

auth.json's whole-file handling (read/validate/atomic-write) is generic and
lives in opencode_auth.py. A Provider owns only what's provider-specific:
which key(s) it occupies in auth.json, and how to read identity/validity out
of its record. Adding a second provider means adding a second Provider
implementation — no changes to the switch/store/lock machinery.
"""

from __future__ import annotations

from typing import Protocol

from opencode_swap.models import AccountDesc, AuthRecord, JsonObject, Validity


class Provider(Protocol):
    id: str

    def extract(self, auth: JsonObject) -> AuthRecord | None:
        """Pull this provider's entry out of a parsed auth.json.

        Returns None if the provider has no entry. Raises SchemaError if an
        entry exists but doesn't match a known shape (fail-safe: never guess).
        """
        ...

    def splice(self, auth: JsonObject, record: AuthRecord) -> JsonObject:
        """Return a new auth.json dict with this provider's entry replaced.

        Does not mutate ``auth``; all other providers' keys are preserved.
        """
        ...

    def identity(self, record: AuthRecord) -> str:
        """A stable string identifying which real-world account this record
        belongs to, independent of token rotation (e.g. account id, or the
        refresh token itself as a fallback)."""
        ...

    def identity_is_stable(self, record: AuthRecord) -> bool:
        """Whether identity remains unchanged when OpenCode refreshes it."""
        ...

    def credential_values(self, record: AuthRecord) -> set[str]:
        """Secret strings which must never enter metadata or output."""
        ...

    def describe(self, record: AuthRecord) -> AccountDesc:
        """Human-facing, secret-free description of the account."""
        ...

    def validate(self, record: AuthRecord) -> Validity:
        """Whether the record looks usable, expired, or malformed."""
        ...
