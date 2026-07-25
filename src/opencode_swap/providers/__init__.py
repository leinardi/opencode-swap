"""Provider registry: maps an OpenCode provider id to its Provider implementation."""

from __future__ import annotations

from opencode_swap.providers.base import Provider
from opencode_swap.providers.openai import OpenAiProvider

PROVIDERS: dict[str, Provider] = {
    "openai": OpenAiProvider(),
}
