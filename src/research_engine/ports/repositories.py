"""Persistence contracts expressed only in domain types."""

from __future__ import annotations

from types import TracebackType
from typing import Protocol
from uuid import UUID

from research_engine.domain.events import DomainEvent
from research_engine.domain.models import Article, ResearchProject, SavedQuery
from research_engine.domain.value_objects import Identifier


class ProjectRepository(Protocol):
    def add(self, project: ResearchProject) -> None: ...

    def get(self, project_id: UUID) -> ResearchProject | None: ...

    def list_all(self) -> list[ResearchProject]: ...


class QueryRepository(Protocol):
    def add(self, query: SavedQuery) -> None: ...

    def get(self, query_id: UUID) -> SavedQuery | None: ...

    def list_for_project(self, project_id: UUID) -> list[SavedQuery]: ...


class ArticleRepository(Protocol):
    def add(self, article: Article) -> None: ...

    def get(self, article_id: UUID) -> Article | None: ...

    def find_by_identifier(self, identifier: Identifier) -> Article | None: ...


class EventOutbox(Protocol):
    def add(self, event: DomainEvent) -> None: ...


class UnitOfWork(Protocol):
    """Implementations commit state and events in one database transaction."""

    projects: ProjectRepository
    queries: QueryRepository
    articles: ArticleRepository
    outbox: EventOutbox

    def __enter__(self) -> UnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
