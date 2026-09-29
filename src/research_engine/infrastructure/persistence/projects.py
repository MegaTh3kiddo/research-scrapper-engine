"""Transactional SQLAlchemy adapter for UI-independent project workflows."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from research_engine.domain.events import DomainEvent
from research_engine.domain.models import (
    ProjectArticle, ResearchProject, ReviewStatus, SavedQuery,
)
from research_engine.domain.value_objects import Identifier, PartialDate, SearchFilters
from research_engine.ports.projects import (
    CitationCount, PaperDetail, ProjectPaperView, QueryRunSummary, SavedQueryRevision,
    SourceMetadata,
)

from . import models as m
from .filter_codec import decode_filters, encode_filters


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _project(row: m.ResearchProject) -> ResearchProject:
    return ResearchProject(
        id=UUID(row.id), name=row.name, description=row.description,
        archived_at=row.archived_at, created_at=row.created_at, updated_at=row.updated_at,
    )


def _query(row: m.SavedQuery) -> SavedQuery:
    return SavedQuery(
        id=UUID(row.id), project_id=UUID(row.project_id), expression=row.expression,
        filters=decode_filters(row.filters), enabled_providers=tuple(row.enabled_providers),
        is_active=row.is_active, archived_at=row.archived_at,
        created_at=row.created_at, updated_at=row.updated_at,
    )


def _relation(row: m.ProjectArticle) -> ProjectArticle:
    return ProjectArticle(
        project_id=UUID(row.project_id), article_id=UUID(row.article_id),
        review_decision=row.review_decision, read_at=row.read_at,
        first_seen_at=row.first_seen_at, last_seen_at=row.last_seen_at,
        status_updated_at=row.status_updated_at,
    )


def _event(session: Session, event_type: str, project_id: str, payload: dict, at: datetime) -> None:
    event = DomainEvent(
        type=event_type, aggregate_type="ResearchProject", aggregate_id=UUID(project_id),
        payload=payload, occurred_at=at,
    )
    session.add(m.OutboxEvent(
        id=str(event.id), type=event.type, schema_version=event.schema_version,
        aggregate_type=event.aggregate_type, aggregate_id=project_id,
        payload=dict(event.payload), occurred_at=event.occurred_at,
    ))


class SqlAlchemyProjectStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _require_project(session: Session, project_id: UUID | str, *, active: bool = False) -> m.ResearchProject:
        row = session.get(m.ResearchProject, str(project_id))
        if row is None:
            raise ValueError("project does not exist")
        if active and row.archived_at is not None:
            raise ValueError("project is archived")
        return row

    @staticmethod
    def _require_query(session: Session, query_id: UUID | str) -> m.SavedQuery:
        row = session.get(m.SavedQuery, str(query_id))
        if row is None:
            raise ValueError("query does not exist")
        return row

    @staticmethod
    def _require_relation(session: Session, project_id: UUID | str, article_id: UUID | str) -> m.ProjectArticle:
        row = session.get(m.ProjectArticle, (str(project_id), str(article_id)))
        if row is None:
            raise ValueError("paper is not attached to this project")
        return row

    @staticmethod
    def _view(session: Session, relation: m.ProjectArticle) -> ProjectPaperView:
        article = session.get(m.Article, relation.article_id)
        if article is None:
            raise ValueError("canonical paper is missing")
        names = session.scalars(
            select(m.Tag.name).join(m.ProjectArticleTag, m.Tag.id == m.ProjectArticleTag.tag_id)
            .where(
                m.ProjectArticleTag.project_id == relation.project_id,
                m.ProjectArticleTag.article_id == relation.article_id,
            ).order_by(m.Tag.normalized_name)
        ).all()
        return ProjectPaperView(UUID(article.id), article.title, _relation(relation), tuple(names))

    @staticmethod
    def _revision(session: Session, query: m.SavedQuery, at: datetime) -> None:
        session.add(m.SavedQueryRevision(
            query_id=query.id, revision_number=query.revision_number,
            expression=query.expression, filters=dict(query.filters),
            enabled_providers=list(query.enabled_providers), is_active=query.is_active,
            archived_at=query.archived_at, created_at=at,
        ))

    def create_project(self, name: str, description: str | None) -> ResearchProject:
        with self._session_factory.begin() as session:
            row = m.ResearchProject(name=name, description=description)
            session.add(row)
            session.flush()
            return _project(row)

    def get_project(self, project_id: UUID | str) -> ResearchProject | None:
        with self._session_factory() as session:
            row = session.get(m.ResearchProject, str(project_id))
            return _project(row) if row else None

    def list_projects(self, *, include_archived: bool = False) -> tuple[ResearchProject, ...]:
        with self._session_factory() as session:
            stmt = select(m.ResearchProject)
            if not include_archived:
                stmt = stmt.where(m.ResearchProject.archived_at.is_(None))
            rows = session.scalars(stmt.order_by(m.ResearchProject.created_at, m.ResearchProject.id)).all()
            return tuple(map(_project, rows))

    def update_project(self, project_id: UUID | str, name: str, description: str | None) -> ResearchProject:
        with self._session_factory.begin() as session:
            row = self._require_project(session, project_id, active=True)
            row.name, row.description = name, description
            row.updated_at = _now()
            return _project(row)

    def archive_project(self, project_id: UUID | str) -> ResearchProject:
        with self._session_factory.begin() as session:
            row = self._require_project(session, project_id)
            if row.archived_at is None:
                row.archived_at = row.updated_at = _now()
            return _project(row)

    def restore_project(self, project_id: UUID | str) -> ResearchProject:
        with self._session_factory.begin() as session:
            row = self._require_project(session, project_id)
            if row.archived_at is not None:
                row.archived_at = None
                row.updated_at = _now()
            return _project(row)

    def create_query(
        self, project_id: UUID | str, expression: str, filters: SearchFilters,
        enabled_providers: tuple[str, ...],
    ) -> SavedQuery:
        with self._session_factory.begin() as session:
            self._require_project(session, project_id, active=True)
            row = m.SavedQuery(
                project_id=str(project_id), expression=expression,
                filters=encode_filters(filters), enabled_providers=list(enabled_providers),
                revision_number=1,
            )
            session.add(row)
            session.flush()
            self._revision(session, row, row.created_at)
            return _query(row)

    def get_query(self, query_id: UUID | str) -> SavedQuery | None:
        with self._session_factory() as session:
            row = session.get(m.SavedQuery, str(query_id))
            return _query(row) if row else None

    def update_query(
        self, query_id: UUID | str, *, expression: str | None = None,
        filters: SearchFilters | None = None,
        enabled_providers: tuple[str, ...] | None = None,
        is_active: bool | None = None,
    ) -> SavedQuery:
        with self._session_factory.begin() as session:
            row = self._require_query(session, query_id)
            self._require_project(session, row.project_id, active=True)
            if row.archived_at is not None:
                raise ValueError("query is archived")
            new_expression = expression if expression is not None else row.expression
            new_filters = encode_filters(filters) if filters is not None else row.filters
            new_providers = list(enabled_providers) if enabled_providers is not None else row.enabled_providers
            new_active = is_active if is_active is not None else row.is_active
            if (new_expression, new_filters, new_providers, new_active) != (
                row.expression, row.filters, row.enabled_providers, row.is_active
            ):
                row.expression, row.filters = new_expression, new_filters
                row.enabled_providers, row.is_active = new_providers, new_active
                row.revision_number += 1
                row.updated_at = _now()
                self._revision(session, row, row.updated_at)
            return _query(row)

    def archive_query(self, query_id: UUID | str) -> SavedQuery:
        with self._session_factory.begin() as session:
            row = self._require_query(session, query_id)
            if row.archived_at is None:
                row.archived_at = row.updated_at = _now()
                row.is_active = False
                row.revision_number += 1
                self._revision(session, row, row.updated_at)
            return _query(row)

    def restore_query(self, query_id: UUID | str) -> SavedQuery:
        with self._session_factory.begin() as session:
            row = self._require_query(session, query_id)
            self._require_project(session, row.project_id, active=True)
            if row.archived_at is not None:
                row.archived_at = None
                row.is_active = True
                row.updated_at = _now()
                row.revision_number += 1
                self._revision(session, row, row.updated_at)
            return _query(row)

    def list_queries(self, project_id: UUID | str, *, include_archived: bool = False) -> tuple[SavedQuery, ...]:
        with self._session_factory() as session:
            self._require_project(session, project_id)
            stmt = select(m.SavedQuery).where(m.SavedQuery.project_id == str(project_id))
            if not include_archived:
                stmt = stmt.where(m.SavedQuery.archived_at.is_(None))
            rows = session.scalars(stmt.order_by(m.SavedQuery.created_at, m.SavedQuery.id)).all()
            return tuple(map(_query, rows))

    def list_query_revisions(self, query_id: UUID | str) -> tuple[SavedQueryRevision, ...]:
        with self._session_factory() as session:
            self._require_query(session, query_id)
            rows = session.scalars(select(m.SavedQueryRevision).where(
                m.SavedQueryRevision.query_id == str(query_id)
            ).order_by(m.SavedQueryRevision.revision_number)).all()
            return tuple(SavedQueryRevision(
                id=UUID(row.id), query_id=UUID(row.query_id), revision_number=row.revision_number,
                expression=row.expression, filters=decode_filters(row.filters),
                enabled_providers=tuple(row.enabled_providers), is_active=row.is_active,
                archived_at=row.archived_at, created_at=row.created_at,
            ) for row in rows)

    def list_query_runs(self, query_id: UUID | str) -> tuple[QueryRunSummary, ...]:
        with self._session_factory() as session:
            self._require_query(session, query_id)
            rows = session.execute(select(m.SearchRun, m.SavedQueryRevision.revision_number).outerjoin(
                m.SavedQueryRevision, m.SearchRun.query_revision_id == m.SavedQueryRevision.id
            ).where(m.SearchRun.query_id == str(query_id)).order_by(m.SearchRun.started_at, m.SearchRun.id)).all()
            return tuple(QueryRunSummary(
                id=UUID(run.id), query_id=UUID(run.query_id), provider=run.provider,
                state=run.state, query_revision_number=revision_number,
                started_at=run.started_at, ended_at=run.ended_at,
                result_count=run.result_count, new_count=run.new_count,
                updated_count=run.updated_count, conflict_count=run.conflict_count,
                cursor=run.cursor, error_code=run.error,
                scheduled_execution_id=run.scheduled_execution_id,
            ) for run, revision_number in rows)

    def attach_paper(self, project_id: UUID | str, article_id: UUID | str) -> ProjectPaperView:
        with self._session_factory.begin() as session:
            self._require_project(session, project_id, active=True)
            if session.get(m.Article, str(article_id)) is None:
                raise ValueError("canonical paper does not exist")
            relation = session.get(m.ProjectArticle, (str(project_id), str(article_id)))
            if relation is None:
                at = _now()
                relation = m.ProjectArticle(
                    project_id=str(project_id), article_id=str(article_id),
                    first_seen_at=at, last_seen_at=at,
                )
                session.add(relation)
                _event(session, "PaperAddedToProject", str(project_id), {"article_id": str(article_id)}, at)
                session.flush()
            return self._view(session, relation)

    def set_review_status(
        self, project_id: UUID | str, article_id: UUID | str, status: ReviewStatus
    ) -> ProjectPaperView:
        with self._session_factory.begin() as session:
            self._require_project(session, project_id, active=True)
            relation = self._require_relation(session, project_id, article_id)
            before = _relation(relation)
            after = before.with_review_status(status)
            if after != before:
                relation.review_decision = after.review_decision.value
                relation.read_at = after.read_at
                relation.status_updated_at = after.status_updated_at
                _event(session, "ReviewStatusChanged", str(project_id), {
                    "article_id": str(article_id), "from": before.status.value,
                    "to": after.status.value,
                }, after.status_updated_at)
            return self._view(session, relation)

    def tag_paper(self, project_id: UUID | str, article_id: UUID | str, tag_name: str) -> ProjectPaperView:
        with self._session_factory.begin() as session:
            self._require_project(session, project_id, active=True)
            relation = self._require_relation(session, project_id, article_id)
            normalized = tag_name.strip().casefold()
            tag = session.scalar(select(m.Tag).where(
                m.Tag.project_id == str(project_id), m.Tag.normalized_name == normalized,
            ))
            if tag is None:
                tag = m.Tag(project_id=str(project_id), name=tag_name.strip(), normalized_name=normalized)
                session.add(tag)
                session.flush()
            if session.get(m.ProjectArticleTag, (str(project_id), str(article_id), tag.id)) is None:
                session.add(m.ProjectArticleTag(
                    project_id=str(project_id), article_id=str(article_id), tag_id=tag.id,
                ))
                session.flush()
            return self._view(session, relation)

    def untag_paper(self, project_id: UUID | str, article_id: UUID | str, tag_name: str) -> ProjectPaperView:
        with self._session_factory.begin() as session:
            self._require_project(session, project_id, active=True)
            relation = self._require_relation(session, project_id, article_id)
            tag = session.scalar(select(m.Tag).where(
                m.Tag.project_id == str(project_id),
                m.Tag.normalized_name == tag_name.strip().casefold(),
            ))
            if tag is not None:
                link = session.get(m.ProjectArticleTag, (str(project_id), str(article_id), tag.id))
                if link is not None:
                    session.delete(link)
                    session.flush()
            return self._view(session, relation)

    def list_tags(self, project_id: UUID | str) -> tuple[str, ...]:
        with self._session_factory() as session:
            self._require_project(session, project_id)
            return tuple(session.scalars(select(m.Tag.name).where(
                m.Tag.project_id == str(project_id)
            ).order_by(m.Tag.normalized_name)).all())

    def list_papers(
        self, project_id: UUID | str, *, status: ReviewStatus | None = None,
        read: bool | None = None, tags_all: tuple[str, ...] = (),
        include_excluded: bool = False,
    ) -> tuple[ProjectPaperView, ...]:
        with self._session_factory() as session:
            self._require_project(session, project_id)
            rows = session.scalars(select(m.ProjectArticle).where(
                m.ProjectArticle.project_id == str(project_id)
            ).order_by(m.ProjectArticle.first_seen_at, m.ProjectArticle.article_id)).all()
            required_tags = {tag.strip().casefold() for tag in tags_all}
            views = (self._view(session, row) for row in rows)
            return tuple(view for view in views if (
                (include_excluded or status is ReviewStatus.EXCLUDED or view.status is not ReviewStatus.EXCLUDED)
                and (status is None or view.status is status)
                and (read is None or view.is_read is read)
                and required_tags.issubset({tag.casefold() for tag in view.tags})
            ))

    def get_paper_detail(
        self, project_id: UUID | str, article_id: UUID | str
    ) -> PaperDetail | None:
        with self._session_factory() as session:
            self._require_project(session, project_id)
            relation = session.get(m.ProjectArticle, (str(project_id), str(article_id)))
            if relation is None:
                return None
            article = session.get(m.Article, str(article_id))
            if article is None:
                raise ValueError("canonical paper is missing")
            authors = tuple(session.scalars(select(m.ArticleAuthor.display_name).where(
                m.ArticleAuthor.article_id == article.id
            ).order_by(m.ArticleAuthor.position)).all())
            identifier_rows = session.scalars(select(m.ArticleIdentifier).where(
                m.ArticleIdentifier.article_id == article.id
            ).order_by(m.ArticleIdentifier.scheme, m.ArticleIdentifier.value)).all()
            identifiers = tuple(Identifier(row.scheme, row.value) for row in identifier_rows)
            sources = tuple(session.scalars(select(m.SourceRecord.provider).where(
                m.SourceRecord.article_id == article.id
            ).distinct().order_by(m.SourceRecord.provider)).all())
            source_rows = session.scalars(select(m.SourceRecord).where(
                m.SourceRecord.article_id == article.id
            ).order_by(
                m.SourceRecord.provider, m.SourceRecord.provider_record_id,
            )).all()
            source_metadata_items: list[SourceMetadata] = []
            for row in source_rows:
                normalized = row.normalized_metadata or {}
                raw_types = normalized.get("publication_types", ())
                publication_types = (
                    tuple(item for item in raw_types if isinstance(item, str))
                    if isinstance(raw_types, (list, tuple)) else ()
                )
                raw_status = normalized.get("publication_status")
                source_metadata_items.append(SourceMetadata(
                    provider=row.provider,
                    source_id=row.provider_record_id,
                    source_database=row.source_database,
                    url=row.url,
                    snapshot_id=row.snapshot_id,
                    retrieved_at=row.fetched_at,
                    parser_version=row.parser_version,
                    publication_types=publication_types,
                    publication_status=raw_status if isinstance(raw_status, str) else None,
                ))
            source_metadata = tuple(source_metadata_items)
            citation_rows = session.scalars(select(m.CitationObservation).where(
                m.CitationObservation.article_id == article.id
            ).order_by(
                m.CitationObservation.provider,
                m.CitationObservation.as_of.desc(),
                m.CitationObservation.id.desc(),
            )).all()
            latest_citations: dict[str, CitationCount] = {}
            for observation in citation_rows:
                latest_citations.setdefault(
                    observation.provider,
                    CitationCount(
                        provider=observation.provider,
                        count=observation.count,
                        as_of=observation.as_of,
                        snapshot_id=observation.snapshot_id,
                    ),
                )
            journal = session.get(m.Journal, article.journal_id) if article.journal_id else None
            publication_date = (
                PartialDate(article.publication_year, article.publication_month, article.publication_day)
                if article.publication_year is not None else None
            )
            doi = next((item.value for item in identifiers if item.scheme == "doi"), None)
            pmid = next((item.value for item in identifiers if item.scheme == "pmid"), None)
            url = article.url or (f"https://doi.org/{doi}" if doi else (
                f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None
            ))
            if url is None:
                openalex_id = session.scalar(select(m.SourceRecord.provider_record_id).where(
                    m.SourceRecord.article_id == article.id, m.SourceRecord.provider == "openalex",
                ).order_by(m.SourceRecord.provider_record_id).limit(1))
                if openalex_id and openalex_id.startswith("W") and openalex_id[1:].isdigit():
                    url = f"https://openalex.org/{openalex_id}"
            return PaperDetail(
                article_id=UUID(article.id), title=article.title, abstract=article.abstract,
                journal=journal.title if journal else None,
                publication_date=publication_date, authors=authors, identifiers=identifiers,
                source_providers=sources, project_paper=self._view(session, relation), url=url,
                canonical_metadata_provenance=dict(article.canonical_metadata_provenance or {}),
                publication_types=tuple(article.publication_types or (
                    [article.publication_type] if article.publication_type else []
                )),
                publication_status=article.publication_status,
                citations=tuple(latest_citations[key] for key in sorted(latest_citations)),
                source_metadata=source_metadata,
            )
