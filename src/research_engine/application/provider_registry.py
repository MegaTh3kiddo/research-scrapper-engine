"""Explicit provider registration at the application boundary."""

from __future__ import annotations

from collections.abc import Iterable

from research_engine.ports.providers import LiteratureProvider


class ProviderRegistry:
    """Collect provider modules without hard-coding them in the core engine."""

    def __init__(self, providers: Iterable[LiteratureProvider] = ()) -> None:
        self._providers: dict[str, LiteratureProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: LiteratureProvider) -> None:
        name = provider.name.strip()
        if not name:
            raise ValueError("provider name must be non-empty")
        if name in self._providers:
            raise ValueError(f"provider already registered: {name}")
        self._providers[name] = provider

    def get(self, name: str) -> LiteratureProvider:
        return self._providers[name]

    def names(self) -> tuple[str, ...]:
        return tuple(self._providers)
