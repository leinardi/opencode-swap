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

"""Provider registry: maps an OpenCode provider id to its Provider implementation."""

from __future__ import annotations

from opencode_swap.models import normalize_provider_id
from opencode_swap.providers.api import ApiProvider
from opencode_swap.providers.base import Provider
from opencode_swap.providers.github_copilot import GitHubCopilotProvider
from opencode_swap.providers.openai import OpenAiProvider
from opencode_swap.providers.poe import PoeProvider
from opencode_swap.providers.xai import XaiProvider
from opencode_swap.providers.zai import ZaiProvider

PROVIDERS: dict[str, Provider] = {
    "openai": OpenAiProvider(),
    "github-copilot": GitHubCopilotProvider(),
    "poe": PoeProvider(),
    "xai": XaiProvider(),
    "zai-coding-plan": ZaiProvider(),
}


def get_provider(provider_id: str) -> Provider:
    """Return specialized handling or canonical API-only fallback."""
    provider_id = normalize_provider_id(provider_id)
    return PROVIDERS.get(provider_id) or ApiProvider(provider_id)
