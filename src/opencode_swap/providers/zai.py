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
