"""Provider contract; source-specific syntax never enters the domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Protocol

from research_engine.domain.models import _require_aware
from research_engine.domain.value_objects import Identifier, PartialDate, SearchFilters


@dataclass(frozen=True, slots=True)
class SearchRequest:
    expression: str
    filters: SearchFilters = field(default_factory=SearchFilters)
    page_size: int = 100

    def __post_init__(self) -> None:
        if not self.expression.strip():
            raise ValueError("search expression must be non-empty")
        if self.page_size < 1:
            raise ValueError("page_size must be positive")


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    supported_filters: frozenset[str]
    supports_boolean_queries: bool


@dataclass(frozen=True, slots=True)
class ProviderQueryPlan:
    provider: str
    translated_query: str
    translation_version: str
    unsupported_filters: tuple[str, ...] = ()
    parameters: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderRecord:
    source_id: str
    title: str
    source_database: str
    response_index: int = 0
    identifiers: tuple[Identifier, ...] = ()
    authors: tuple[str, ...] = ()
    journal_name: str | None = None
    publication_date: PartialDate | None = None
    abstract: str | None = None
    url: str | None = None
    keywords: tuple[str, ...] = ()
    citation_count: int | None = None
    publication_types: tuple[str, ...] = ()
    publication_status: str | None = None

    def __post_init__(self) -> None:
        if self.citation_count is not None and (
            isinstance(self.citation_count, bool)
            or not isinstance(self.citation_count, int)
            or self.citation_count < 0
        ):
            raise ValueError("citation_count must be a non-negative integer or None")


@dataclass(frozen=True, slots=True)
class RawResponse:
    body: bytes
    media_type: str
    fetched_at: datetime
    safe_request_metadata: Mapping[str, str]

    def __post_init__(self) -> None:
        _require_aware(self.fetched_at, "fetched_at")


@dataclass(frozen=True, slots=True)
class SearchPage:
    records: tuple[ProviderRecord, ...]
    raw_responses: tuple[RawResponse, ...]
    next_cursor: str | None = None
    total_count: int | None = None

    def __post_init__(self) -> None:
        if not self.raw_responses:
            raise ValueError("search page must retain at least one raw response")
        if self.total_count is not None and self.total_count < 0:
            raise ValueError("total_count must be non-negative")
        for record in self.records:
            if not 0 <= record.response_index < len(self.raw_responses):
                raise ValueError("record response_index must point to a raw response")


@dataclass(frozen=True, slots=True)
class RecordResult:
    record: ProviderRecord
    raw_response: RawResponse


class LiteratureProvider(Protocol):
    """A discoverable source module with explicit query translation."""

    @property
    def name(self) -> str: ...

    @property
    def capabilities(self) -> ProviderCapabilities: ...

    @property
    def parser_version(self) -> str: ...

    def plan(self, request: SearchRequest) -> ProviderQueryPlan: ...

    async def search(
        self, plan: ProviderQueryPlan, cursor: str | None = None
    ) -> SearchPage: ...

    async def fetch(self, source_id: str) -> RecordResult: ...

    async def aclose(self) -> None:
        """Release provider-owned transport resources at the service boundary."""
        ...
