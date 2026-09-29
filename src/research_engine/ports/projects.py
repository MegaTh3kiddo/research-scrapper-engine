"""UI-independent contracts and read models for Research Project workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from research_engine.domain.models import (
    ProjectArticle,
    ResearchProject,
    ReviewStatus,
    SavedQuery,
)
from research_engine.domain.value_objects import SearchFilters
from research_engine.domain.value_objects import Identifier, PartialDate


@dataclass(frozen=True, slots=True)
class SavedQueryRevision:
    id: UUID
    query_id: UUID
    revision_number: int
    expression: str
    filters: SearchFilters
    enabled_providers: tuple[str, ...]
    is_active: bool
    archived_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class QueryRunSummary:
    id: UUID
    query_id: UUID
    provider: str
    state: str
    query_revision_number: int | None
    started_at: datetime
    ended_at: datetime | None
    result_count: int
    new_count: int
    updated_count: int
    conflict_count: int
    cursor: str | None
    error_code: str | None = None
    scheduled_execution_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectPaperView:
    article_id: UUID
    title: str
    relation: ProjectArticle
    tags: tuple[str, ...]

    @property
    def status(self) -> ReviewStatus:
        return self.relation.status

    @property
    def is_read(self) -> bool:
        return self.relation.is_read


@dataclass(frozen=True, slots=True)
class CitationCount:
    provider: str
    count: int
    as_of: datetime
    snapshot_id: str | None = None


@dataclass(frozen=True, slots=True)
class SourceMetadata:
    provider: str
    source_id: str
    source_database: str | None
    url: str | None
    snapshot_id: str
    retrieved_at: datetime
    parser_version: str
    publication_types: tuple[str, ...] = ()
    publication_status: str | None = None


@dataclass(frozen=True, slots=True)
class PaperDetail:
    article_id: UUID
    title: str
    abstract: str | None
    journal: str | None
    publication_date: PartialDate | None
    authors: tuple[str, ...]
    identifiers: tuple[Identifier, ...]
    source_providers: tuple[str, ...]
    project_paper: ProjectPaperView
    url: str | None
    canonical_metadata_provenance: dict[str, dict[str, str]] = field(default_factory=dict)
    publication_types: tuple[str, ...] = ()
    publication_status: str | None = None
    citations: tuple[CitationCount, ...] = ()
    source_metadata: tuple[SourceMetadata, ...] = ()


class ProjectWorkflowStore(Protocol):
    def create_project(self, name: str, description: str | None) -> ResearchProject: ...

    def get_project(self, project_id: UUID) -> ResearchProject | None: ...

    def list_projects(self, *, include_archived: bool = False) -> tuple[ResearchProject, ...]: ...

    def update_project(self, project_id: UUID, name: str, description: str | None) -> ResearchProject: ...

    def archive_project(self, project_id: UUID) -> ResearchProject: ...

    def restore_project(self, project_id: UUID) -> ResearchProject: ...

    def create_query(
        self, project_id: UUID, expression: str, filters: SearchFilters,
        enabled_providers: tuple[str, ...],
    ) -> SavedQuery: ...

    def get_query(self, query_id: UUID) -> SavedQuery | None: ...

    def update_query(
        self, query_id: UUID, *, expression: str | None = None,
        filters: SearchFilters | None = None,
        enabled_providers: tuple[str, ...] | None = None,
        is_active: bool | None = None,
    ) -> SavedQuery: ...

    def archive_query(self, query_id: UUID) -> SavedQuery: ...

    def restore_query(self, query_id: UUID) -> SavedQuery: ...

    def list_queries(
        self, project_id: UUID, *, include_archived: bool = False
    ) -> tuple[SavedQuery, ...]: ...

    def list_query_revisions(self, query_id: UUID) -> tuple[SavedQueryRevision, ...]: ...

    def list_query_runs(self, query_id: UUID) -> tuple[QueryRunSummary, ...]: ...

    def attach_paper(self, project_id: UUID, article_id: UUID) -> ProjectPaperView: ...

    def set_review_status(
        self, project_id: UUID, article_id: UUID, status: ReviewStatus
    ) -> ProjectPaperView: ...

    def tag_paper(
        self, project_id: UUID, article_id: UUID, tag_name: str
    ) -> ProjectPaperView: ...

    def untag_paper(
        self, project_id: UUID, article_id: UUID, tag_name: str
    ) -> ProjectPaperView: ...

    def list_tags(self, project_id: UUID) -> tuple[str, ...]: ...

    def list_papers(
        self, project_id: UUID, *, status: ReviewStatus | None = None,
        read: bool | None = None, tags_all: tuple[str, ...] = (),
        include_excluded: bool = False,
    ) -> tuple[ProjectPaperView, ...]: ...

    def get_paper_detail(
        self, project_id: UUID, article_id: UUID
    ) -> PaperDetail | None: ...
