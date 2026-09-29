"""UI-independent execution of a single provider's literature query."""

from __future__ import annotations

from dataclasses import dataclass

from research_engine.application.provider_registry import ProviderRegistry
from research_engine.ports.providers import ProviderQueryPlan, SearchPage, SearchRequest


class UnsupportedFiltersError(ValueError):
    """A provider cannot honor all portable filters in a search request."""


class PaginationError(RuntimeError):
    """A provider repeated a cursor, risking an infinite search run."""


@dataclass(frozen=True, slots=True)
class DiscoveryRun:
    plan: ProviderQueryPlan
    pages: tuple[SearchPage, ...]

    @property
    def records_found(self) -> int:
        return sum(len(page.records) for page in self.pages)

    @property
    def next_cursor(self) -> str | None:
        return self.pages[-1].next_cursor if self.pages else None


class DiscoveryService:
    """Execute plans without depending on HTTP, storage, or a user interface."""

    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    async def execute(
        self,
        provider_name: str,
        request: SearchRequest,
        *,
        max_pages: int = 1,
        cursor: str | None = None,
    ) -> DiscoveryRun:
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        provider = self._registry.get(provider_name)
        plan = provider.plan(request)
        if plan.provider != provider.name:
            raise ValueError("provider returned a plan for another source")
        if plan.unsupported_filters:
            raise UnsupportedFiltersError(
                f"{provider_name} does not support: {', '.join(plan.unsupported_filters)}"
            )
        pages: list[SearchPage] = []
        used_cursors: set[str | None] = set()
        for _ in range(max_pages):
            if cursor in used_cursors:
                raise PaginationError(f"{provider_name} repeated a search cursor")
            used_cursors.add(cursor)
            page = await provider.search(plan, cursor=cursor)
            if page.next_cursor is not None and page.next_cursor in used_cursors:
                raise PaginationError(f"{provider_name} repeated a search cursor")
            pages.append(page)
            cursor = page.next_cursor
            if cursor is None:
                break
        return DiscoveryRun(plan=plan, pages=tuple(pages))
