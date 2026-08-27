"""Z.AI GLM Coding Plan: a canonical API-key record plus a live usage lookup.

Record handling is exactly the generic API path (`ApiProvider`) -- z.ai stores
`{"type":"api","key":"..."}` like any other static-key provider. The only
reason this provider exists as its own class is the GLM Coding Plan quota
endpoint (see `usage.fetch_zai_usage`), which is keyed to the same API key.

Only `zai-coding-plan` is registered, not a bare `zai`: the quota endpoint is
coding-plan-specific, and a pay-as-you-go `zai` key would only ever report an
inactive plan.
"""

from __future__ import annotations

from opencode_swap import usage
from opencode_swap.models import AuthRecord
from opencode_swap.providers.api import ApiProvider


class ZaiProvider(ApiProvider):
    id = "zai-coding-plan"
    usage_record_types = frozenset({"api"})

    def __init__(self) -> None:
        super().__init__(self.id)

    def fetch_usage(self, record: AuthRecord) -> usage.UsageSnapshot | None:
        key = record.raw.get("key")
        if not isinstance(key, str) or not key:
            return None
        return usage.fetch_zai_usage(key)
