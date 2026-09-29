"""Research Project workflows independent of desktop or server clients."""

from __future__ import annotations

from uuid import UUID

from research_engine.domain.models import ResearchProject, ReviewStatus, SavedQuery
from research_engine.domain.value_objects import SearchFilters
from research_engine.domain.search_expression import parse_expression
from research_engine.ports.projects import (
    PaperDetail, ProjectPaperView, ProjectWorkflowStore, QueryRunSummary,
    SavedQueryRevision,
)


class ProjectService:
    def __init__(self, store: ProjectWorkflowStore) -> None:
        self._store = store

    def create_project(self, name: str, description: str | None = None) -> ResearchProject:
        ResearchProject(name=name, description=description)
        return self._store.create_project(name.strip(), description)

    def get_project(self, project_id: UUID) -> ResearchProject | None:
        return self._store.get_project(project_id)

    def list_projects(self, *, include_archived: bool = False) -> tuple[ResearchProject, ...]:
        return self._store.list_projects(include_archived=include_archived)

    def update_project(
        self, project_id: UUID, *, name: str, description: str | None = None
    ) -> ResearchProject:
        ResearchProject(name=name, description=description)
        return self._store.update_project(project_id, name.strip(), description)

    def archive_project(self, project_id: UUID) -> ResearchProject:
        return self._store.archive_project(project_id)

    def restore_project(self, project_id: UUID) -> ResearchProject:
        return self._store.restore_project(project_id)

    def create_query(
        self, project_id: UUID, expression: str,
        filters: SearchFilters | None = None,
        enabled_providers: tuple[str, ...] = (),
    ) -> SavedQuery:
        parse_expression(expression)
        SavedQuery(project_id=project_id, expression=expression, filters=filters or SearchFilters())
        return self._store.create_query(
            project_id, expression.strip(), filters or SearchFilters(), enabled_providers
        )

    def get_query(self, query_id: UUID) -> SavedQuery | None:
        return self._store.get_query(query_id)

    def update_query(
        self, query_id: UUID, *, expression: str | None = None,
        filters: SearchFilters | None = None,
        enabled_providers: tuple[str, ...] | None = None,
        is_active: bool | None = None,
    ) -> SavedQuery:
        if expression is not None and not expression.strip():
            raise ValueError("query expression must be non-empty")
        if expression is not None:
            parse_expression(expression)
        return self._store.update_query(
            query_id, expression=expression.strip() if expression is not None else None,
            filters=filters, enabled_providers=enabled_providers, is_active=is_active,
        )

    def archive_query(self, query_id: UUID) -> SavedQuery:
        return self._store.archive_query(query_id)

    def restore_query(self, query_id: UUID) -> SavedQuery:
        return self._store.restore_query(query_id)

    def list_queries(
        self, project_id: UUID, *, include_archived: bool = False
    ) -> tuple[SavedQuery, ...]:
        return self._store.list_queries(project_id, include_archived=include_archived)

    def list_query_revisions(self, query_id: UUID) -> tuple[SavedQueryRevision, ...]:
        return self._store.list_query_revisions(query_id)

    def list_query_runs(self, query_id: UUID) -> tuple[QueryRunSummary, ...]:
        return self._store.list_query_runs(query_id)

    def attach_paper(self, project_id: UUID, article_id: UUID) -> ProjectPaperView:
        return self._store.attach_paper(project_id, article_id)

    def set_review_status(
        self, project_id: UUID, article_id: UUID, status: ReviewStatus
    ) -> ProjectPaperView:
        return self._store.set_review_status(project_id, article_id, ReviewStatus(status))

    def tag_paper(
        self, project_id: UUID, article_id: UUID, tag_name: str
    ) -> ProjectPaperView:
        if not tag_name.strip():
            raise ValueError("tag name must be non-empty")
        return self._store.tag_paper(project_id, article_id, tag_name.strip())

    def untag_paper(
        self, project_id: UUID, article_id: UUID, tag_name: str
    ) -> ProjectPaperView:
        if not tag_name.strip():
            raise ValueError("tag name must be non-empty")
        return self._store.untag_paper(project_id, article_id, tag_name.strip())

    def list_tags(self, project_id: UUID) -> tuple[str, ...]:
        return self._store.list_tags(project_id)

    def list_papers(
        self, project_id: UUID, *, status: ReviewStatus | None = None,
        read: bool | None = None, tags_all: tuple[str, ...] = (),
        include_excluded: bool = False,
    ) -> tuple[ProjectPaperView, ...]:
        return self._store.list_papers(
            project_id,
            status=ReviewStatus(status) if status is not None else None,
            read=read,
            tags_all=tags_all,
            include_excluded=include_excluded,
        )

    def get_paper_detail(
        self, project_id: UUID, article_id: UUID
    ) -> PaperDetail | None:
        return self._store.get_paper_detail(project_id, article_id)
