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

"""GitHub Copilot auth as implemented by OpenCode's built-in plugin."""

from __future__ import annotations

from opencode_swap.exceptions import SchemaError
from opencode_swap.models import AccountDesc, AuthRecord, JsonObject, Validity
from opencode_swap.providers.common import credential_values, extract_raw, published_raw, validate_oauth


class GitHubCopilotProvider:
    id = "github-copilot"
    usage_record_types: frozenset[str] = frozenset()  # no known usage endpoint

    def extract(self, auth: JsonObject) -> AuthRecord | None:
        raw = extract_raw(auth, self.id)
        if raw is None:
            return None
        if raw.get("type") != "oauth":
            raise SchemaError("github-copilot auth type is not supported by opencode-swap")
        return validate_oauth(raw, self.id)

    def splice(self, auth: JsonObject, record: AuthRecord) -> JsonObject:
        return {**auth, self.id: published_raw(record.raw)}

    def identity(self, record: AuthRecord) -> str:
        refresh = record.raw.get("refresh")
        enterprise = record.raw.get("enterpriseUrl")
        return f"copilot\0{enterprise if isinstance(enterprise, str) else ''}\0{refresh if isinstance(refresh, str) else ''}"

    def identity_is_stable(self, record: AuthRecord) -> bool:
        return True

    def credential_values(self, record: AuthRecord) -> set[str]:
        return credential_values(record)

    def describe(self, record: AuthRecord) -> AccountDesc:
        return AccountDesc(type="oauth", email=None, account_id=None, expires=None)

    def validate(self, record: AuthRecord) -> Validity:
        return Validity.OK if record.type == "oauth" else Validity.INVALID

    def refresh(self, record: AuthRecord) -> AuthRecord | None:
        return None  # no verified standalone refresh flow for this provider yet

    def fetch_usage(self, record: AuthRecord) -> None:
        return None  # unreachable: usage_record_types is empty
