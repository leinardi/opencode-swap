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
