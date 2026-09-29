from __future__ import annotations

import asyncio
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from io import StringIO

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from research_engine.application.ingestion import IngestionService
from research_engine.application.exports import ExportService
from research_engine.application.projects import ProjectService
from research_engine.application.provider_registry import ProviderRegistry
from research_engine.domain.models import ProjectArticle, ReviewStatus
from research_engine.domain.value_objects import SearchFilters
from research_engine.infrastructure.persistence import models as m
from research_engine.infrastructure.persistence.base import Base
from research_engine.infrastructure.persistence.database import create_database_engine
from research_engine.infrastructure.persistence.ingestion import SqlAlchemyIngestionStore
from research_engine.infrastructure.persistence.projects import SqlAlchemyProjectStore
from research_engine.infrastructure.snapshots import LocalSnapshotStorage
from research_engine.ports.providers import (
    ProviderQueryPlan,
    ProviderRecord,
    RawResponse,
    SearchPage,
    SearchRequest,
)


@dataclass
class ProjectHarness:
    session_factory: sessionmaker
    service: ProjectService
    tmp_path: Path


class FakeProvider:
    name = "pubmed"
    parser_version = "pubmed-test-parser-v1"

    def __init__(self, pages: list[SearchPage]) -> None:
        self.pages = list(pages)

    def plan(self, request: SearchRequest) -> ProviderQueryPlan:
        return ProviderQueryPlan(
            provider=self.name,
            translated_query=request.expression,
            translation_version="test-query-v1",
            parameters={"page_size": str(request.page_size)},
        )

    async def search(
        self, plan: ProviderQueryPlan, cursor: str | None = None
    ) -> SearchPage:
        return self.pages.pop(0)


@pytest.fixture
def project_harness(tmp_path: Path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'projects.sqlite3'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(engine, expire_on_commit=False)
    service = ProjectService(SqlAlchemyProjectStore(session_factory))
    try:
        yield ProjectHarness(session_factory, service, tmp_path)
    finally:
        engine.dispose()


def _id(value) -> str:
    return str(value)


def _project(harness: ProjectHarness, name: str) -> str:
    return _id(harness.service.create_project(name).id)


def _article(harness: ProjectHarness, title: str = "Shared canonical paper") -> str:
    with harness.session_factory.begin() as session:
        article = m.Article(title=title)
        session.add(article)
        session.flush()
        return article.id


def _run_ingestion(
    harness: ProjectHarness,
    query_id: str,
    page: SearchPage,
) -> str:
    ingestion = IngestionService(
        ProviderRegistry((FakeProvider([page]),)),
        SqlAlchemyIngestionStore(harness.session_factory),
        LocalSnapshotStorage(harness.tmp_path / "snapshots"),
    )
    result = asyncio.run(ingestion.run(query_id, "pubmed"))
    return result.run_id


def _page(*records: ProviderRecord) -> SearchPage:
    raw = RawResponse(
        body=b'{"fixture":"project workflow"}',
        media_type="application/json",
        fetched_at=datetime.now(timezone.utc),
        safe_request_metadata={"query": "project workflow test"},
    )
    return SearchPage(records=tuple(records), raw_responses=(raw,))


def _listed_article_ids(papers) -> set[str]:
    return {_id(paper.article_id) for paper in papers}


def _tag_names(paper) -> set[str]:
    return {tag.name if hasattr(tag, "name") else str(tag) for tag in paper.tags}


def test_project_article_review_status_preserves_independent_read_fact():
    relation = ProjectArticle(project_id="project-a", article_id="article-1")

    assert relation.status is ReviewStatus.UNREAD
    read = relation.with_review_status(ReviewStatus.READ)
    assert read.status is ReviewStatus.READ
    assert read.read_at is not None

    important_but_unread = relation.with_review_status(ReviewStatus.IMPORTANT)
    assert important_but_unread.status is ReviewStatus.IMPORTANT
    assert important_but_unread.read_at is None

    important_and_read = read.with_review_status(ReviewStatus.IMPORTANT)
    assert important_and_read.status is ReviewStatus.IMPORTANT
    assert important_and_read.read_at == read.read_at
    assert important_and_read.with_review_status(ReviewStatus.UNREAD).status is ReviewStatus.UNREAD

    excluded_and_read = read.with_review_status(ReviewStatus.EXCLUDED)
    assert excluded_and_read.status is ReviewStatus.EXCLUDED
    assert excluded_and_read.read_at == read.read_at


def test_shared_paper_has_independent_project_review_tags_and_idempotent_attachment(
    project_harness: ProjectHarness,
):
    service = project_harness.service
    project_a = _project(project_harness, "Systematic review")
    project_b = _project(project_harness, "Guideline update")
    article_id = _article(project_harness)

    service.attach_paper(project_a, article_id)
    service.attach_paper(project_a, article_id)
    service.attach_paper(project_b, article_id)
    service.set_review_status(project_a, article_id, ReviewStatus.READ)
    service.set_review_status(project_a, article_id, ReviewStatus.IMPORTANT)
    service.tag_paper(project_a, article_id, "screening")
    service.tag_paper(project_a, article_id, "full text")
    service.tag_paper(project_a, article_id, "screening")
    service.tag_paper(project_b, article_id, "candidate")

    project_a_paper = service.list_papers(project_a)[0]
    project_b_paper = service.list_papers(project_b)[0]
    assert project_a_paper.status is ReviewStatus.IMPORTANT
    assert project_a_paper.relation.read_at is not None
    assert _tag_names(project_a_paper) == {"screening", "full text"}
    assert project_b_paper.status is ReviewStatus.UNREAD
    assert project_b_paper.relation.read_at is None
    assert _tag_names(project_b_paper) == {"candidate"}
    assert len(service.list_tags(project_a)) == 2
    assert len(service.list_tags(project_b)) == 1
    with project_harness.session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(m.ProjectArticle).where(
                m.ProjectArticle.project_id == project_a,
                m.ProjectArticle.article_id == article_id,
            )
        ) == 1

    assert _listed_article_ids(service.list_papers(project_a, status=ReviewStatus.IMPORTANT)) == {
        article_id
    }
    assert service.list_papers(project_a, status=ReviewStatus.IMPORTANT, read=False) == ()
    assert _listed_article_ids(service.list_papers(project_a, read=True)) == {article_id}
    assert service.list_papers(project_a, tags_all=("screening", "full text"))
    assert service.list_papers(project_a, tags_all=("screening", "missing")) == ()

    service.set_review_status(project_b, article_id, ReviewStatus.EXCLUDED)
    assert service.list_papers(project_b) == ()
    assert _listed_article_ids(service.list_papers(project_b, include_excluded=True)) == {
        article_id
    }


def test_tags_cannot_be_assigned_across_project_paper_relations(
    project_harness: ProjectHarness,
):
    service = project_harness.service
    project_a = _project(project_harness, "First project")
    project_b = _project(project_harness, "Second project")
    article_id = _article(project_harness)
    service.attach_paper(project_a, article_id)

    with pytest.raises(ValueError):
        service.tag_paper(project_b, article_id, "not attached here")

    service.tag_paper(project_a, article_id, "shared name")
    tag_a = service.list_tags(project_a)[0]
    with project_harness.session_factory() as session:
        tag_b = m.Tag(project_id=project_b, name="shared name", normalized_name="shared name")
        session.add(tag_b)
        session.flush()
        invalid_link = m.ProjectArticleTag(
            project_id=project_a,
            article_id=article_id,
            tag_id=tag_b.id,
        )
        session.add(invalid_link)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
    assert tag_a == "shared name"
    assert service.list_tags(project_b) == ()


def test_projects_and_queries_archive_restore_and_preserve_revision_run_history(
    project_harness: ProjectHarness,
):
    service = project_harness.service
    project_id = _project(project_harness, "Evidence synthesis")
    first_project = service.list_projects()
    assert _id(first_project[0].id) == project_id

    query = service.create_query(
        project_id,
        "heart failure",
        filters=SearchFilters(languages=("eng",)),
        enabled_providers=("pubmed",),
    )
    query_id = _id(query.id)
    original_revision = service.list_query_revisions(query_id)[0]
    assert original_revision.revision_number == 1
    assert original_revision.expression == "heart failure"

    first_run_id = _run_ingestion(project_harness, query_id, _page())
    service.update_query(
        query_id,
        expression="heart failure mortality",
        filters=SearchFilters(languages=("eng",), humans_only=True),
        enabled_providers=("pubmed", "europe_pmc"),
    )
    current_revisions = service.list_query_revisions(query_id)
    assert [revision.revision_number for revision in current_revisions] == [1, 2]
    assert current_revisions[0].expression == "heart failure"
    assert current_revisions[1].expression == "heart failure mortality"

    second_run_id = _run_ingestion(project_harness, query_id, _page())
    runs = service.list_query_runs(query_id)
    runs_by_id = {_id(run.id): run for run in runs}
    assert runs_by_id[first_run_id].query_revision_number == 1
    assert runs_by_id[second_run_id].query_revision_number == 2

    service.archive_query(query_id)
    assert service.list_queries(project_id) == ()
    archived_queries = service.list_queries(project_id, include_archived=True)
    assert [_id(item.id) for item in archived_queries] == [query_id]
    service.restore_query(query_id)
    assert [_id(item.id) for item in service.list_queries(project_id)] == [query_id]
    assert len(service.list_query_runs(query_id)) == 2

    service.archive_project(project_id)
    assert service.list_projects() == ()
    assert [_id(item.id) for item in service.list_projects(include_archived=True)] == [
        project_id
    ]
    service.restore_project(project_id)
    assert [_id(item.id) for item in service.list_projects()] == [project_id]


def test_ingested_query_match_attaches_canonical_paper_to_owning_project_idempotently(
    project_harness: ProjectHarness,
):
    service = project_harness.service
    project_id = _project(project_harness, "Query owned project")
    other_project_id = _project(project_harness, "Unrelated project")
    query = service.create_query(
        project_id,
        "diabetes outcomes",
        enabled_providers=("pubmed",),
    )
    record = ProviderRecord(
        source_id="pmid-9001",
        title="Diabetes outcomes paper",
        source_database="pubmed",
    )

    first_run_id = _run_ingestion(project_harness, _id(query.id), _page(record))
    with project_harness.session_factory() as session:
        article_id = session.scalar(select(m.QueryMatch.article_id))
        relation = session.get(m.ProjectArticle, (project_id, article_id))
        assert relation is not None
        first_seen_at = relation.first_seen_at
    assert _listed_article_ids(service.list_papers(project_id)) == {article_id}
    assert service.list_papers(other_project_id) == ()

    second_run_id = _run_ingestion(project_harness, _id(query.id), _page(record))
    with project_harness.session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(m.ProjectArticle).where(
                m.ProjectArticle.project_id == project_id,
                m.ProjectArticle.article_id == article_id,
            )
        ) == 1
        relation = session.get(m.ProjectArticle, (project_id, article_id))
        assert relation.first_seen_at == first_seen_at
        assert relation.last_seen_at >= first_seen_at
        assert session.scalar(
            select(func.count()).select_from(m.QueryMatch).where(
                m.QueryMatch.query_id == _id(query.id),
                m.QueryMatch.article_id == article_id,
            )
        ) == 1
    assert {_id(run.id) for run in service.list_query_runs(_id(query.id))} == {
        first_run_id,
        second_run_id,
    }


def test_query_revision_is_pinned_and_stale_run_cannot_start(project_harness: ProjectHarness):
    service = project_harness.service
    project_id = _project(project_harness, "Versioned query")
    query = service.create_query(project_id, "asthma", enabled_providers=("pubmed",))
    query_id = _id(query.id)
    store = SqlAlchemyIngestionStore(project_harness.session_factory)
    loaded = store.load_request(query_id, "pubmed", 10)
    service.update_query(query_id, expression="severe asthma")
    with pytest.raises(ValueError, match="changed"):
        store.begin_run(
            query_id, loaded.revision_id,
            FakeProvider([]).plan(loaded.request), datetime.now(timezone.utc), 30,
        )
    assert len(service.list_query_revisions(query_id)) == 2
    service.update_query(query_id, expression="severe asthma")
    assert len(service.list_query_revisions(query_id)) == 2
    service.archive_project(project_id)
    with pytest.raises(ValueError, match="archived"):
        store.load_request(query_id, "pubmed", 10)


def test_manual_attachment_and_review_events_are_idempotent(project_harness: ProjectHarness):
    service = project_harness.service
    project_id = _project(project_harness, "Manual collection")
    article_id = _article(project_harness)
    service.attach_paper(project_id, article_id)
    service.attach_paper(project_id, article_id)
    service.set_review_status(project_id, article_id, ReviewStatus.READ)
    service.set_review_status(project_id, article_id, ReviewStatus.READ)
    service.set_review_status(project_id, article_id, ReviewStatus.IMPORTANT)
    with project_harness.session_factory() as session:
        events = session.scalars(select(m.OutboxEvent).order_by(m.OutboxEvent.occurred_at)).all()
        assert [event.type for event in events] == [
            "PaperAddedToProject", "ReviewStatusChanged", "ReviewStatusChanged",
        ]
        assert all(event.aggregate_id == project_id for event in events)


def test_paper_detail_is_scoped_to_its_project(project_harness: ProjectHarness):
    service = project_harness.service
    project_a = _project(project_harness, "Private collection A")
    project_b = _project(project_harness, "Private collection B")
    article_id = _article(project_harness, "Scoped paper")
    service.attach_paper(project_a, article_id)

    assert service.get_paper_detail(project_b, article_id) is None
    detail = service.get_paper_detail(project_a, article_id)
    assert detail.title == "Scoped paper"
    assert detail.project_paper.status is ReviewStatus.UNREAD


def test_csv_and_ris_export_preserve_metadata_and_project_review(project_harness: ProjectHarness):
    service = project_harness.service
    project_id = _project(project_harness, "Evidence synthesis")
    with project_harness.session_factory.begin() as session:
        journal = m.Journal(title="Medical Journal")
        session.add(journal)
        session.flush()
        article = m.Article(
            title="=A formula-looking title", abstract="Line one\nLine two",
            journal_id=journal.id, publication_year=2025,
            publication_month=9, publication_day=25,
        )
        session.add(article)
        session.flush()
        article_id = article.id
        session.add_all([
            m.ArticleAuthor(article_id=article_id, position=0, display_name="A. Researcher"),
            m.ArticleAuthor(article_id=article_id, position=1, display_name="B. Clinician"),
            m.ArticleIdentifier(article_id=article_id, scheme="doi", value="10.1234/example"),
            m.ArticleIdentifier(article_id=article_id, scheme="pmid", value="123456"),
        ])
    service.attach_paper(project_id, article_id)
    service.set_review_status(project_id, article_id, ReviewStatus.READ)
    service.set_review_status(project_id, article_id, ReviewStatus.EXCLUDED)
    service.tag_paper(project_id, article_id, "screening")

    exporter = ExportService(service)
    document = exporter.export_project(project_id, "csv")
    rows = list(csv.DictReader(StringIO(document.content.decode("utf-8"))))
    assert document.paper_count == 1
    assert document.filename.endswith(".csv")
    assert rows[0]["project_name"] == "Evidence synthesis"
    assert rows[0]["title"] == "'=A formula-looking title"
    assert rows[0]["authors"] == "A. Researcher; B. Clinician"
    assert rows[0]["journal"] == "Medical Journal"
    assert rows[0]["publication_date"] == "2025-09-25"
    assert rows[0]["doi"] == "10.1234/example"
    assert rows[0]["pmid"] == "123456"
    assert rows[0]["url"] == "https://doi.org/10.1234/example"
    assert rows[0]["review_status"] == "excluded"
    assert rows[0]["is_read"] == "yes"
    assert rows[0]["tags"] == "screening"
    assert rows[0]["abstract"] == "Line one\nLine two"

    ris = exporter.export_project(project_id, "ris").content.decode("utf-8")
    assert ris.startswith("TY  - JOUR\r\n")
    assert "TI  - =A formula-looking title\r\n" in ris
    assert ris.count("AU  - ") == 2
    assert "JO  - Medical Journal\r\n" in ris
    assert "PY  - 2025\r\n" in ris
    assert "DA  - 2025-09-25\r\n" in ris
    assert "DO  - 10.1234/example\r\n" in ris
    assert "AN  - PMID:123456\r\n" in ris
    assert "N1  - Review status: excluded\r\n" in ris
    assert "N1  - Project tag: screening\r\n" in ris
    assert "AB  - Line one Line two\r\n" in ris
    assert ris.endswith("ER  - \r\n\r\n")
    assert exporter.export_project(project_id, "csv", include_excluded=False).paper_count == 0
