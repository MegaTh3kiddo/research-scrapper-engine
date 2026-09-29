from __future__ import annotations

import asyncio
from dataclasses import replace
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from itertools import permutations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from research_engine.application.ingestion import IngestionResult, IngestionService
from research_engine.application.monitoring import ScheduledMonitoringService
from research_engine.application.projects import ProjectService
from research_engine.application.provider_registry import ProviderRegistry
from research_engine.application.searches import ManualSearchService
from research_engine.domain.value_objects import Identifier
from research_engine.infrastructure.persistence import models as m
from research_engine.infrastructure.persistence.base import Base
from research_engine.infrastructure.persistence.database import create_database_engine
from research_engine.infrastructure.persistence.ingestion import SqlAlchemyIngestionStore
from research_engine.infrastructure.persistence.projects import SqlAlchemyProjectStore
from research_engine.infrastructure.persistence.scheduling import SqlAlchemyScheduleStore
from research_engine.infrastructure.snapshots import LocalSnapshotStorage
from research_engine.ports.ingestion import PageOutcome
from research_engine.ports.providers import (
    ProviderQueryPlan,
    ProviderRecord,
    RawResponse,
    SearchPage,
    SearchRequest,
)
from research_engine.domain.value_objects import PartialDate


class WorkflowProvider:
    parser_version = "workflow-test-v1"

    def __init__(self, name: str, *, failure_text: str | None = None) -> None:
        self.name = name
        self.failure_text = failure_text
        self.calls = 0

    def plan(self, request: SearchRequest) -> ProviderQueryPlan:
        return ProviderQueryPlan(
            provider=self.name,
            translated_query=request.expression,
            translation_version="workflow-test-v1",
            parameters={},
        )

    async def search(self, plan: ProviderQueryPlan, cursor: str | None = None) -> SearchPage:
        self.calls += 1
        if self.failure_text:
            raise RuntimeError(self.failure_text)
        return SearchPage(
            records=(ProviderRecord(
                source_id=f"{self.name}-record-1",
                title=f"Paper from {self.name}",
                source_database=self.name,
                identifiers=(Identifier("doi", "10.5555/workflow-paper"),),
            ),),
            raw_responses=(RawResponse(
                body=f"snapshot from {self.name}".encode(),
                media_type="application/json",
                fetched_at=datetime.now(timezone.utc),
                safe_request_metadata={"provider": self.name},
            ),),
        )

    async def aclose(self) -> None:
        pass


@pytest.fixture
def monitor_harness(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'source-workflow.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    projects = ProjectService(SqlAlchemyProjectStore(factory))
    project = projects.create_project("Source selection")
    query = projects.create_query(
        project.id,
        "medical research",
        enabled_providers=("pubmed", "europe_pmc", "openalex"),
    )
    store = SqlAlchemyScheduleStore(factory)
    try:
        yield factory, projects, str(query.id), store, tmp_path
    finally:
        engine.dispose()


def test_monitoring_intersects_query_sources_and_continues_after_provider_failure(monitor_harness):
    factory, projects, query_id, schedule_store, tmp_path = monitor_harness
    secret_error = "request contained api_key=do-not-store-or-display"
    providers = (
        WorkflowProvider("pubmed", failure_text=secret_error),
        WorkflowProvider("europe_pmc"),
    )

    @asynccontextmanager
    async def searches():
        search = ManualSearchService(
            IngestionService(
                ProviderRegistry(providers),
                SqlAlchemyIngestionStore(factory),
                LocalSnapshotStorage(tmp_path / "snapshots"),
            ),
            allowed_sources=("pubmed", "europe_pmc"),
        )
        yield search

    monitoring = ScheduledMonitoringService(schedule_store, searches)
    now = datetime.now(timezone.utc)
    monitoring.configure(query_id, next_due_at=now - timedelta(seconds=1))

    executions = asyncio.run(monitoring.run_due_once(now=now))

    assert len(executions) == 1
    assert executions[0].state == "failed"
    assert executions[0].error_code == "RuntimeError"
    assert "do-not-store-or-display" not in str(executions[0])
    assert providers[0].calls == 1
    assert providers[1].calls == 1
    assert len(executions[0].search_run_ids) == 2

    with factory() as session:
        run_data = session.execute(select(m.SearchRun.provider, m.SearchRun.state)).all()
        assert set(run_data) == {("pubmed", "failed"), ("europe_pmc", "completed")}
        # OpenAlex was on the saved query, but the workflow allow-list excluded it.
        assert "openalex" not in {provider for provider, _state in run_data}
        failed_run = session.scalar(select(m.SearchRun).where(m.SearchRun.provider == "pubmed"))
        assert failed_run.error == "RuntimeError"
        records = session.scalars(select(m.SourceRecord)).all()
        assert [(record.provider, record.provider_record_id) for record in records] == [
            ("europe_pmc", "europe_pmc-record-1"),
        ]
        observations = session.scalars(select(m.SearchRunObservation)).all()
        assert len(observations) == 1
        assert observations[0].run_id == executions[0].search_run_ids[1]
        assert observations[0].provider == "europe_pmc"

    papers = projects.list_papers(projects.get_query(query_id).project_id)
    assert len(papers) == 1
    assert papers[0].title == "Paper from europe_pmc"


def test_manual_batch_continues_after_error_and_returns_only_safe_error_code():
    class IngestionStub:
        def __init__(self):
            self.calls = []

        async def run(self, query_id, provider_name, **kwargs):
            self.calls.append(provider_name)
            if provider_name == "pubmed":
                raise RuntimeError("api_key=private-value")
            return IngestionResult("run-europe", "completed", None, 1, PageOutcome(observed=1))

    ingestion = IngestionStub()
    search = ManualSearchService(ingestion)

    outcomes = asyncio.run(search.run_sources(
        "query-1", ("pubmed", "europe_pmc", "pubmed"), max_pages=3,
    ))

    assert ingestion.calls == ["pubmed", "europe_pmc"]
    assert outcomes[0] == ("pubmed", None, "RuntimeError")
    assert outcomes[1][0] == "europe_pmc"
    assert outcomes[1][1].run_id == "run-europe"
    assert outcomes[1][2] is None
    assert "private-value" not in str(outcomes)


def test_openalex_work_without_doi_retains_clickable_detail_and_export_url(monitor_harness):
    factory, projects, query_id, _, tmp_path = monitor_harness

    class DOIlessProvider(WorkflowProvider):
        async def search(self, plan, cursor=None):
            page = await super().search(plan, cursor)
            record = replace(page.records[0], source_id="W1234",
                             identifiers=(Identifier("openalex", "W1234"),))
            return replace(page, records=(record,))

    provider = DOIlessProvider("openalex")
    search = ManualSearchService(IngestionService(
        ProviderRegistry((provider,)), SqlAlchemyIngestionStore(factory),
        LocalSnapshotStorage(tmp_path / "doi-less-snapshots"),
    ))
    asyncio.run(search.run_query(query_id, "openalex"))
    project_id = projects.get_query(query_id).project_id
    paper = projects.list_papers(project_id)[0]
    detail = projects.get_paper_detail(project_id, paper.article_id)
    assert detail.url == "https://openalex.org/W1234"
    assert detail.canonical_metadata_provenance["title"]["provider"] == "openalex"
    from research_engine.application.exports import ExportService
    for format in ("csv", "ris"):
        assert b"https://openalex.org/W1234" in ExportService(projects).export_project(project_id, format).content


@pytest.mark.parametrize("arrival_order", tuple(permutations(("pubmed", "europe_pmc", "crossref"))))
def test_cross_source_doi_deduplication_and_authority_are_stable_for_every_arrival_order(
    tmp_path, arrival_order,
):
    class MetadataProvider(WorkflowProvider):
        async def search(self, plan: ProviderQueryPlan, cursor: str | None = None) -> SearchPage:
            self.calls += 1
            data = {
                "pubmed": {
                    "title": "PubMed preferred title", "authors": (),
                    "journal_name": None, "abstract": None, "publication_date": None,
                },
                "europe_pmc": {
                    "title": "Europe PMC alternate title", "authors": (),
                    "journal_name": "Europe PMC Journal", "abstract": "Enriched abstract",
                    "publication_date": PartialDate(2022, 6),
                },
                "crossref": {
                    "title": "Crossref alternate title", "authors": ("Crossref Author",),
                    "journal_name": "Crossref Journal", "abstract": None,
                    "publication_date": PartialDate(2020),
                },
            }[self.name]
            return SearchPage(
                records=(ProviderRecord(
                    source_id=f"{self.name}-same-work",
                    source_database=self.name,
                    identifiers=(Identifier("doi", "10.5555/shared-source-record"),),
                    **data,
                ),),
                raw_responses=(RawResponse(
                    body=f"{self.name} original response".encode(),
                    media_type="application/json",
                    fetched_at=datetime.now(timezone.utc),
                    safe_request_metadata={"provider": self.name},
                ),),
            )

    engine = create_database_engine(f"sqlite:///{tmp_path / 'source-priority.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    projects = ProjectService(SqlAlchemyProjectStore(factory))
    project = projects.create_project("Cross-source identity")
    query = projects.create_query(
        project.id, "shared article", enabled_providers=arrival_order,
    )
    providers = tuple(MetadataProvider(name) for name in arrival_order)
    service = ManualSearchService(IngestionService(
        ProviderRegistry(providers),
        SqlAlchemyIngestionStore(factory),
        LocalSnapshotStorage(tmp_path / "source-priority-snapshots"),
    ))

    async def ingest_in_order():
        outcomes = []
        for provider_name in arrival_order:
            outcomes.append(await service.run_query(str(query.id), provider_name))
        return outcomes

    try:
        outcomes = asyncio.run(ingest_in_order())
        assert len(outcomes) == 3
        assert all(outcome.state == "completed" for outcome in outcomes)

        with factory() as session:
            articles = session.scalars(select(m.Article)).all()
            assert len(articles) == 1
            article = articles[0]
            assert article.title == "PubMed preferred title"
            assert article.abstract == "Enriched abstract"
            assert (article.publication_year, article.publication_month) == (2022, 6)
            journal = session.get(m.Journal, article.journal_id)
            assert journal.title == "Europe PMC Journal"
            authors = session.scalars(
                select(m.ArticleAuthor).where(m.ArticleAuthor.article_id == article.id)
                .order_by(m.ArticleAuthor.position)
            ).all()
            # The higher-ranked records omit authors, so Crossref supplies the missing field.
            assert tuple(author.display_name for author in authors) == ("Crossref Author",)
            provenance = article.canonical_metadata_provenance
            assert {name: entry["provider"] for name, entry in provenance.items()} == {
                "title": "pubmed", "abstract": "europe_pmc", "publication_date": "europe_pmc",
                "journal": "europe_pmc", "authors": "crossref",
            }
            for entry in provenance.values():
                snapshot = session.get(m.SourceSnapshot, entry["snapshot_id"])
                assert snapshot.provider == entry["provider"]
                assert snapshot.search_run_id == entry["run_id"]

            sources = session.scalars(select(m.SourceRecord)).all()
            assert {source.provider for source in sources} == {"pubmed", "europe_pmc", "crossref"}
            assert all(source.article_id == article.id for source in sources)
            assert all(source.identifiers == [{"scheme": "doi", "value": "10.5555/shared-source-record"}]
                       for source in sources)
            observations = session.scalars(select(m.SearchRunObservation)).all()
            assert len(observations) == 3
            assert {observation.article_id for observation in observations} == {article.id}

        project_papers = projects.list_papers(project.id)
        assert len(project_papers) == 1
        assert project_papers[0].title == "PubMed preferred title"
    finally:
        engine.dispose()
