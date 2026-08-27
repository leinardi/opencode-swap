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

from opencode_swap import usage
from opencode_swap.models import AccountDesc, AuthRecord, JsonObject, Validity


class Provider(Protocol):
    id: str

    usage_record_types: frozenset[str]
    """Record types (``"api"``, ``"oauth"``, ...) this provider can look up
    live usage for via ``fetch_usage``. Empty (the default for every provider
    without a known usage endpoint) tells ``Switcher.fetch_usage`` to skip all
    lock/refresh work and report "not applicable" rather than "unavailable"."""

    def extract(self, auth: JsonObject) -> AuthRecord | None:
        """Pull this provider's entry out of a parsed auth.json.

        Returns None if the provider has no entry. Raises SchemaError if an
        entry exists but doesn't match a known shape (fail-safe: never guess).
        """
        ...

    def splice(self, auth: JsonObject, record: AuthRecord) -> JsonObject:
        """Return a new auth.json dict with this provider's entry replaced.

        Does not mutate ``auth``; all other providers' keys are preserved.

        Implementations must route the record through
        ``providers.common.published_raw`` -- this is the only path by which
        opencode-swap content reaches OpenCode, so it is where read-side
        tolerance has to stop. ``test_every_provider_splice_publishes_an
        _integer_expiry`` enforces this for every registered provider.
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

    def refresh(self, record: AuthRecord) -> AuthRecord | None:
        """Rotated-token copy of `record` from a standalone OAuth refresh.

        Returns None if this provider/record type has no standalone refresh
        (the default for every provider but OpenAI oauth records). Raises
        RefreshError if a refresh was attempted and the grant was rejected
        or the request otherwise failed -- callers must not treat that the
        same as "no refresh available".
        """
        ...

    def fetch_usage(self, record: AuthRecord) -> usage.UsageSnapshot | None:
        """Live usage/quota for `record`, fetched over the network.

        Only called for record types in ``usage_record_types``. Returns None
        when this particular record can't be looked up (missing field, secret
        store out of sync) -- distinct from ``UsageSnapshot(available=False)``
        ("looked it up, the request failed"). Never raises: network and shape
        failures come back as ``available=False``.
        """
        ...
