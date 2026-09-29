from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from research_engine.application.ingestion import IngestionService
from research_engine.application.provider_registry import ProviderRegistry
from research_engine.domain.value_objects import Identifier, PartialDate
from research_engine.infrastructure.persistence import models as m
from research_engine.infrastructure.persistence.base import Base
from research_engine.infrastructure.persistence.database import create_database_engine
from research_engine.infrastructure.persistence.ingestion import SqlAlchemyIngestionStore
from research_engine.infrastructure.logging_config import configure_local_logging
from research_engine.infrastructure.providers.pubmed import _parse_efetch
from research_engine.infrastructure.snapshots import LocalSnapshotStorage
from research_engine.ports.providers import (
    ProviderQueryPlan,
    ProviderRecord,
    RawResponse,
    SearchPage,
    SearchRequest,
)
from research_engine.ports.snapshots import StoredSnapshot


@dataclass
class IngestionHarness:
    session_factory: sessionmaker
    store: SqlAlchemyIngestionStore
    snapshots: LocalSnapshotStorage
    project_id: str


class FakeProvider:
    def __init__(self, name: str, pages: list[SearchPage | BaseException]) -> None:
        self.name = name
        self.parser_version = f"{name}-parser-test-v1"
        self.pages = list(pages)

    def plan(self, request: SearchRequest) -> ProviderQueryPlan:
        return ProviderQueryPlan(
            provider=self.name,
            translated_query=request.expression,
            translation_version="fake-query-v1",
            parameters={"page_size": str(request.page_size)},
        )

    async def search(
        self, plan: ProviderQueryPlan, cursor: str | None = None
    ) -> SearchPage:
        result = self.pages.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


@pytest.fixture
def ingestion_harness(tmp_path: Path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'ingestion.sqlite3'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(engine, expire_on_commit=False)
    snapshots = LocalSnapshotStorage(tmp_path / "raw-snapshots")
    store = SqlAlchemyIngestionStore(session_factory)
    with session_factory.begin() as session:
        project = m.ResearchProject(name="Ingestion integration tests")
        session.add(project)
        session.flush()
        project_id = project.id
    try:
        yield IngestionHarness(session_factory, store, snapshots, project_id)
    finally:
        engine.dispose()


def _query(
    harness: IngestionHarness,
    name: str,
    providers: tuple[str, ...] = ("pubmed", "europe_pmc"),
) -> str:
    with harness.session_factory.begin() as session:
        query = m.SavedQuery(
            project_id=harness.project_id,
            expression=name,
            enabled_providers=list(providers),
        )
        session.add(query)
        session.flush()
        session.add(m.SavedQueryRevision(
            query_id=query.id, revision_number=1, expression=query.expression,
            filters=dict(query.filters), enabled_providers=list(query.enabled_providers),
            is_active=query.is_active, archived_at=query.archived_at,
            created_at=query.created_at,
        ))
        return query.id


def _record(
    source_id: str,
    title: str,
    *,
    provider: str,
    identifiers: tuple[Identifier, ...] = (),
    publication_date: PartialDate | None = None,
    abstract: str | None = None,
) -> ProviderRecord:
    return ProviderRecord(
        source_id=source_id,
        title=title,
        source_database=provider,
        identifiers=identifiers,
        publication_date=publication_date,
        abstract=abstract,
    )


def _page(
    provider: str,
    records: tuple[ProviderRecord, ...],
    *,
    body: bytes | None = None,
    next_cursor: str | None = None,
) -> SearchPage:
    fetched_at = datetime.now(timezone.utc)
    raw = RawResponse(
        body=body if body is not None else f"{provider}:{fetched_at.isoformat()}".encode(),
        media_type="application/json",
        fetched_at=fetched_at,
        safe_request_metadata={"query": "test search", "provider": provider},
    )
    return SearchPage(records=records, raw_responses=(raw,), next_cursor=next_cursor)


def _run(
    harness: IngestionHarness,
    providers: tuple[FakeProvider, ...],
    query_id: str,
    provider_name: str,
    *,
    max_pages: int = 1,
    recent_window_days: int = 30,
):
    service = IngestionService(
        ProviderRegistry(providers), harness.store, harness.snapshots
    )
    return asyncio.run(
        service.run(
            query_id,
            provider_name,
            max_pages=max_pages,
            recent_window_days=recent_window_days,
        )
    )


def test_pubmed_reference_ids_do_not_merge_distinct_papers(ingestion_harness):
    body = b"""<PubmedArticleSet>
      <PubmedArticle><MedlineCitation><PMID>111</PMID><Article><ArticleTitle>First paper</ArticleTitle></Article></MedlineCitation>
        <PubmedData><ArticleIdList><ArticleId IdType="pubmed">111</ArticleId></ArticleIdList>
          <ReferenceList><Reference><ArticleIdList><ArticleId IdType="pubmed">222</ArticleId>
            <ArticleId IdType="doi">10.1000/second</ArticleId></ArticleIdList></Reference></ReferenceList>
        </PubmedData></PubmedArticle>
      <PubmedArticle><MedlineCitation><PMID>222</PMID><Article><ArticleTitle>Second paper</ArticleTitle></Article></MedlineCitation>
        <PubmedData><ArticleIdList><ArticleId IdType="pubmed">222</ArticleId>
          <ArticleId IdType="doi">10.1000/second</ArticleId></ArticleIdList></PubmedData></PubmedArticle>
    </PubmedArticleSet>"""
    records = _parse_efetch(body, response_index=0)
    query_id = _query(ingestion_harness, "referenced papers", providers=("pubmed",))
    result = _run(
        ingestion_harness,
        (FakeProvider("pubmed", [_page("pubmed", records, body=body)]),),
        query_id, "pubmed",
    )

    assert result.outcome.newly_discovered == 2
    assert result.outcome.metadata_updated == 0
    assert result.outcome.conflicts == 0
    with ingestion_harness.session_factory() as session:
        sources = session.scalars(select(m.SourceRecord).order_by(m.SourceRecord.provider_record_id)).all()
        assert len(sources) == 2
        assert sources[0].article_id != sources[1].article_id


def test_legacy_pubmed_source_blocks_new_ingestion_without_mutating_data(ingestion_harness):
    query_id = _query(ingestion_harness, "new query", providers=("pubmed",))
    with ingestion_harness.session_factory.begin() as session:
        article = m.Article(title="Old paper")
        snapshot = m.SourceSnapshot(
            provider="pubmed", storage_key="legacy", sha256="0" * 64,
            parser_version="pubmed-parser-v1", content_type="text/xml",
            compression="none", byte_size=0,
        )
        session.add_all((article, snapshot))
        session.flush()
        session.add(m.SourceRecord(
            provider="pubmed", provider_record_id="12345",
            article_id=article.id, snapshot_id=snapshot.id,
            parser_version="pubmed-parser-v1",
        ))

    with pytest.raises(ValueError, match="legacy PubMed records may contain false merges"):
        _run(
            ingestion_harness,
            (FakeProvider("pubmed", [_page("pubmed", ())]),),
            query_id, "pubmed",
        )
    with ingestion_harness.session_factory() as session:
        assert session.scalar(select(m.SearchRun.id)) is None


def test_repeat_run_is_idempotent_and_retains_snapshot_and_observation_provenance(
    ingestion_harness,
):
    harness = ingestion_harness
    query_id = _query(harness, "repeat run")
    record = _record(
        "12345",
        "A repeated paper",
        provider="pubmed",
        identifiers=(Identifier("PMID", " PMID: 12345 "),),
    )
    page_body = b'{"result":"same raw response"}'
    provider = FakeProvider(
        "pubmed",
        [_page("pubmed", (record,), body=page_body), _page("pubmed", (record,), body=page_body)],
    )

    first = _run(harness, (provider,), query_id, "pubmed")
    second = _run(harness, (provider,), query_id, "pubmed")

    assert first.outcome.newly_discovered == 1
    assert first.outcome.new_to_query == 1
    assert second.outcome.observed == 1
    assert second.outcome.newly_discovered == 0
    assert second.outcome.new_to_query == 0
    with harness.session_factory() as session:
        runs = session.scalars(select(m.SearchRun).order_by(m.SearchRun.started_at)).all()
        snapshots = session.scalars(
            select(m.SourceSnapshot).order_by(m.SourceSnapshot.captured_at)
        ).all()
        source = session.scalar(select(m.SourceRecord))
        links = session.scalars(select(m.SourceRecordSnapshot)).all()
        observations = session.scalars(
            select(m.SearchRunObservation).order_by(m.SearchRunObservation.observed_at)
        ).all()
        events = session.scalars(select(m.OutboxEvent)).all()

        assert len(runs) == len(snapshots) == len(links) == len(observations) == 2
        assert len(events) == 1
        assert events[0].type == "PaperDiscovered"
        assert snapshots[0].sha256 == snapshots[1].sha256
        assert snapshots[0].id != snapshots[1].id
        assert {snapshot.provider for snapshot in snapshots} == {"pubmed"}
        assert {snapshot.search_run_id for snapshot in snapshots} == {run.id for run in runs}
        assert {snapshot.parser_version for snapshot in snapshots} == {
            "pubmed-parser-test-v1"
        }
        assert source.provider == "pubmed"
        assert source.provider_record_id == "12345"
        assert source.snapshot_id == snapshots[-1].id
        assert {link.snapshot_id for link in links} == {snapshot.id for snapshot in snapshots}
        assert {observation.run_id for observation in observations} == {run.id for run in runs}
        assert {observation.article_id for observation in observations} == {source.article_id}
        assert all(observation.snapshot_id in {snapshot.id for snapshot in snapshots} for observation in observations)
        for snapshot in snapshots:
            stored = StoredSnapshot(
                storage_key=snapshot.storage_key,
                sha256=snapshot.sha256,
                compression=snapshot.compression,
                byte_size=snapshot.byte_size,
            )
            assert harness.snapshots.load(stored) == page_body


def test_pubmed_page_links_search_and_fetch_snapshots_to_run_and_fetch_record(
    ingestion_harness,
):
    """A record parsed from EFetch keeps both raw responses and its exact origin."""
    harness = ingestion_harness
    query_id = _query(harness, "pubmed page", providers=("pubmed",))
    esearch_body = b"<eSearchResult><Id>12345</Id></eSearchResult>"
    efetch_body = b"<PubmedArticle><PMID>12345</PMID></PubmedArticle>"
    esearch_time = datetime.now(timezone.utc)
    efetch_time = datetime.now(timezone.utc)
    page = SearchPage(
        records=(
            ProviderRecord(
                source_id="12345",
                title="PubMed article",
                source_database="pubmed",
                response_index=1,
                identifiers=(
                    Identifier("PMID", "12345"),
                    Identifier("DOI", " DOI: 10.5555/Raw.Case "),
                ),
            ),
        ),
        raw_responses=(
            RawResponse(
                body=esearch_body,
                media_type="application/xml",
                fetched_at=esearch_time,
                safe_request_metadata={"endpoint": "esearch.fcgi"},
            ),
            RawResponse(
                body=efetch_body,
                media_type="application/xml",
                fetched_at=efetch_time,
                safe_request_metadata={"endpoint": "efetch.fcgi"},
            ),
        ),
    )
    provider = FakeProvider("pubmed", [page])

    result = _run(harness, (provider,), query_id, "pubmed")

    assert result.outcome.newly_discovered == 1
    with harness.session_factory() as session:
        run_snapshots = session.scalars(
            select(m.SourceSnapshot)
            .where(m.SourceSnapshot.search_run_id == result.run_id)
            .order_by(m.SourceSnapshot.captured_at)
        ).all()
        source = session.scalar(select(m.SourceRecord))
        source_snapshot_links = session.scalars(select(m.SourceRecordSnapshot)).all()
        observation = session.scalar(select(m.SearchRunObservation))

        assert len(run_snapshots) == 2
        assert {snapshot.provider for snapshot in run_snapshots} == {"pubmed"}
        assert {snapshot.search_run_id for snapshot in run_snapshots} == {result.run_id}
        esearch_snapshot, efetch_snapshot = sorted(
            run_snapshots, key=lambda snapshot: snapshot.captured_at
        )
        assert esearch_snapshot.sha256 != efetch_snapshot.sha256
        assert esearch_snapshot.request_metadata == {"endpoint": "esearch.fcgi"}
        assert efetch_snapshot.request_metadata == {"endpoint": "efetch.fcgi"}
        assert source.snapshot_id == efetch_snapshot.id
        assert len(source_snapshot_links) == 1
        assert source_snapshot_links[0].snapshot_id == efetch_snapshot.id
        assert observation.snapshot_id == efetch_snapshot.id
        assert source.identifiers == [
            {"scheme": "PMID", "value": "12345"},
            {"scheme": "DOI", "value": " DOI: 10.5555/Raw.Case "},
        ]
        for snapshot, expected_body in zip(
            (esearch_snapshot, efetch_snapshot),
            (esearch_body, efetch_body),
            strict=True,
        ):
            stored = StoredSnapshot(
                storage_key=snapshot.storage_key,
                sha256=snapshot.sha256,
                compression=snapshot.compression,
                byte_size=snapshot.byte_size,
            )
            assert harness.snapshots.load(stored) == expected_body


def test_discovery_query_novelty_and_publication_recency_are_separate_facts(
    ingestion_harness,
):
    harness = ingestion_harness
    first_query = _query(harness, "first search", providers=("pubmed",))
    second_query = _query(harness, "second search", providers=("pubmed",))
    today = datetime.now(timezone.utc).date()
    recent_record = _record(
        "first-id",
        "Recently published article",
        provider="pubmed",
        identifiers=(Identifier("doi", "10.5555/recent"),),
        publication_date=PartialDate(today.year, today.month, today.day),
    )
    same_article_new_source_id = _record(
        "second-id",
        "Recently published article",
        provider="pubmed",
        identifiers=(Identifier("doi", "10.5555/recent"),),
        publication_date=PartialDate(today.year, today.month, today.day),
    )
    provider = FakeProvider(
        "pubmed",
        [
            _page("pubmed", (recent_record,)),
            _page("pubmed", (same_article_new_source_id,)),
        ],
    )

    first = _run(harness, (provider,), first_query, "pubmed")
    second = _run(harness, (provider,), second_query, "pubmed")

    assert first.outcome.newly_discovered == 1
    assert first.outcome.new_to_query == 1
    assert first.outcome.newly_published == 1
    assert second.outcome.newly_discovered == 0
    assert second.outcome.new_to_query == 1
    assert second.outcome.newly_published == 1
    with harness.session_factory() as session:
        observations = session.scalars(
            select(m.SearchRunObservation).order_by(m.SearchRunObservation.observed_at)
        ).all()
        assert len(observations) == 2
        assert [(item.newly_discovered, item.new_to_query, item.newly_published) for item in observations] == [
            (True, True, True),
            (False, True, True),
        ]


def test_canonical_metadata_change_records_updated_observation_and_outbox_event(
    ingestion_harness,
):
    harness = ingestion_harness
    query_id = _query(harness, "metadata revisions", providers=("pubmed",))
    identifiers = (Identifier("doi", "10.5555/metadata"),)
    provider = FakeProvider(
        "pubmed",
        [
            _page(
                "pubmed",
                (_record("record-1", "Original title", provider="pubmed", identifiers=identifiers),),
            ),
            _page(
                "pubmed",
                (
                    _record(
                        "record-1",
                        "Corrected title",
                        provider="pubmed",
                        identifiers=identifiers,
                        abstract="New abstract",
                    ),
                ),
            ),
        ],
    )

    first = _run(harness, (provider,), query_id, "pubmed")
    second = _run(harness, (provider,), query_id, "pubmed")

    assert first.outcome.metadata_updated == 0
    assert second.outcome.metadata_updated == 1
    with harness.session_factory() as session:
        article = session.scalar(select(m.Article))
        observations = session.scalars(
            select(m.SearchRunObservation).order_by(m.SearchRunObservation.observed_at)
        ).all()
        events = session.scalars(
            select(m.OutboxEvent).order_by(m.OutboxEvent.occurred_at)
        ).all()
        assert article.title == "Corrected title"
        assert article.abstract == "New abstract"
        assert [item.metadata_updated for item in observations] == [False, True]
        assert [event.type for event in events] == [
            "PaperDiscovered",
            "PaperMetadataUpdated",
        ]
        assert events[1].aggregate_id == article.id
        assert events[1].payload["run_id"] == second.run_id
        assert events[1].payload["provider"] == "pubmed"
        assert events[1].payload["snapshot_id"] == observations[1].snapshot_id


def test_cross_provider_doi_and_pmid_identifiers_resolve_to_one_canonical_article(
    ingestion_harness,
):
    harness = ingestion_harness
    query_id = _query(harness, "cross-provider identity")
    pubmed = FakeProvider(
        "pubmed",
        [
            _page(
                "pubmed",
                (
                    _record(
                        "12345",
                        "PubMed title",
                        provider="pubmed",
                        identifiers=(
                            Identifier("PMID", "12345"),
                            Identifier("DOI", "10.5555/MIXED.CASE"),
                        ),
                    ),
                ),
            )
        ],
    )
    europe_pmc = FakeProvider(
        "europe_pmc",
        [
            _page(
                "europe_pmc",
                (
                    _record(
                        "MED:12345",
                        "Europe PMC title",
                        provider="europe_pmc",
                        identifiers=(
                            Identifier("pmid", "PMID: 12345"),
                            Identifier("doi", "https://doi.org/10.5555/mixed.case"),
                        ),
                    ),
                ),
            )
        ],
    )

    first = _run(harness, (pubmed,), query_id, "pubmed")
    second = _run(harness, (europe_pmc,), query_id, "europe_pmc")

    assert first.outcome.newly_discovered == 1
    assert second.outcome.newly_discovered == 0
    assert second.outcome.new_to_query == 0
    with harness.session_factory() as session:
        articles = session.scalars(select(m.Article)).all()
        sources = session.scalars(select(m.SourceRecord).order_by(m.SourceRecord.provider)).all()
        observations = session.scalars(select(m.SearchRunObservation)).all()
        tokens = session.scalars(select(m.ArticleIdentifier)).all()
        assert len(articles) == 1
        assert {source.provider for source in sources} == {"pubmed", "europe_pmc"}
        assert {source.provider_record_id for source in sources} == {"12345", "MED:12345"}
        assert {observation.article_id for observation in observations} == {articles[0].id}
        assert {token.scheme for token in tokens} >= {
            "doi",
            "pmid",
            "provider:pubmed:id",
            "provider:europe_pmc:id",
        }
        assert {source.snapshot_id for source in sources} <= {
            snapshot.id for snapshot in session.scalars(select(m.SourceSnapshot)).all()
        }


def test_title_only_similarity_does_not_merge_across_providers(ingestion_harness):
    harness = ingestion_harness
    query_id = _query(harness, "no title merge")
    pubmed = FakeProvider(
        "pubmed",
        [_page("pubmed", (_record("one", "Identical title", provider="pubmed"),))],
    )
    europe_pmc = FakeProvider(
        "europe_pmc",
        [_page("europe_pmc", (_record("one", "Identical title", provider="europe_pmc"),))],
    )

    _run(harness, (pubmed,), query_id, "pubmed")
    result = _run(harness, (europe_pmc,), query_id, "europe_pmc")

    assert result.outcome.newly_discovered == 1
    assert result.outcome.new_to_query == 1
    with harness.session_factory() as session:
        assert len(session.scalars(select(m.Article)).all()) == 2
        assert len(session.scalars(select(m.IdentityConflict)).all()) == 0


def test_conflicting_exact_identifiers_are_audited_without_merging(ingestion_harness):
    harness = ingestion_harness
    query_id = _query(harness, "exact identity conflict", providers=("pubmed",))
    provider = FakeProvider(
        "pubmed",
        [
            _page(
                "pubmed",
                (
                    _record(
                        "doi-only",
                        "Article identified by DOI",
                        provider="pubmed",
                        identifiers=(Identifier("doi", "10.5555/conflicting"),),
                    ),
                ),
            ),
            _page(
                "pubmed",
                (
                    _record(
                        "pmid-only",
                        "Article identified by PMID",
                        provider="pubmed",
                        identifiers=(Identifier("pmid", "777"),),
                    ),
                ),
            ),
            _page(
                "pubmed",
                (
                    _record(
                        "asserts-both",
                        "Ambiguous record",
                        provider="pubmed",
                        identifiers=(
                            Identifier("doi", "10.5555/conflicting"),
                            Identifier("pmid", "777"),
                        ),
                    ),
                ),
            ),
        ],
    )

    _run(harness, (provider,), query_id, "pubmed")
    _run(harness, (provider,), query_id, "pubmed")
    conflict_result = _run(harness, (provider,), query_id, "pubmed")

    assert conflict_result.outcome.conflicts == 1
    assert conflict_result.outcome.observed == 1
    with harness.session_factory() as session:
        articles = session.scalars(select(m.Article)).all()
        conflict = session.scalar(select(m.IdentityConflict))
        assert len(articles) == 2
        assert conflict is not None
        assert conflict.provider == "pubmed"
        assert conflict.source_id == "asserts-both"
        assert conflict.run_id == conflict_result.run_id
        assert conflict.status == "unresolved"
        assert len(conflict.candidate_article_ids) == 2
        assert conflict.snapshot_id in {
            snapshot.id for snapshot in session.scalars(select(m.SourceSnapshot)).all()
        }
        run = session.get(m.SearchRun, conflict_result.run_id)
        assert run is not None
        assert run.result_count == 1
        assert run.conflict_count == 1
        assert len(session.scalars(select(m.SourceRecord)).all()) == 2
        assert len(session.scalars(select(m.OutboxEvent)).all()) == 2

    repeated = _record(
        "asserts-both",
        "Ambiguous record",
        provider="pubmed",
        identifiers=(Identifier("doi", "10.5555/conflicting"), Identifier("pmid", "777")),
    )
    duplicate_provider = FakeProvider("pubmed", [
        _page("pubmed", (repeated,), next_cursor="second"),
        _page("pubmed", (repeated,)),
    ])
    duplicate_result = _run(
        harness, (duplicate_provider,), query_id, "pubmed", max_pages=2
    )
    assert duplicate_result.outcome.conflicts == 1
    assert duplicate_result.outcome.observed == 1
    with harness.session_factory() as session:
        assert len(session.scalars(select(m.IdentityConflict)).all()) == 2


def test_partial_failure_keeps_processed_page_checkpoint_and_audit_rows(ingestion_harness, tmp_path):
    harness = ingestion_harness
    log_path = configure_local_logging(tmp_path)
    query_id = _query(harness, "partial failure", providers=("pubmed",))
    provider = FakeProvider(
        "pubmed",
        [
            _page(
                "pubmed",
                (
                    _record(
                        "processed",
                        "Processed before failure",
                        provider="pubmed",
                        identifiers=(Identifier("pmid", "900"),),
                    ),
                ),
                next_cursor="cursor-after-page-one",
            ),
            RuntimeError("provider failure includes a secret-like message"),
        ],
    )

    with pytest.raises(RuntimeError, match="secret-like"):
        _run(harness, (provider,), query_id, "pubmed", max_pages=2)

    with harness.session_factory() as session:
        run = session.scalar(select(m.SearchRun))
        assert run is not None
        assert run.state == "failed"
        assert run.cursor == "cursor-after-page-one"
        assert run.error == "RuntimeError"
        assert run.result_count == 1
        assert run.new_count == 1
        assert len(session.scalars(select(m.SourceSnapshot)).all()) == 1
        assert len(session.scalars(select(m.SearchRunObservation)).all()) == 1
        assert len(session.scalars(select(m.OutboxEvent)).all()) == 1
    log_text = log_path.read_text(encoding="utf-8")
    assert "ingestion_failed" in log_text
    assert "error_code=RuntimeError" in log_text
    assert "secret-like" not in log_text


def test_duplicate_source_within_run_keeps_first_observation_and_links_later_raw_response(
    ingestion_harness,
):
    harness = ingestion_harness
    query_id = _query(harness, "duplicate page", providers=("pubmed",))
    first_record = ProviderRecord(
        source_id="777", title="First title", source_database="PubMed",
        url="https://pubmed.example/first", citation_count=0,
    )
    later_record = ProviderRecord(
        source_id="777", title="Later title", source_database="NLM",
        url="https://pubmed.example/later", citation_count=5,
    )
    provider = FakeProvider("pubmed", [
        _page("pubmed", (first_record,), next_cursor="next"),
        _page("pubmed", (later_record,)),
    ])

    result = _run(harness, (provider,), query_id, "pubmed", max_pages=2)

    assert result.outcome.observed == 1
    with harness.session_factory() as session:
        article = session.scalar(select(m.Article))
        source = session.scalar(select(m.SourceRecord))
        observation = session.scalar(select(m.SearchRunObservation))
        links = session.scalars(select(m.SourceRecordSnapshot)).all()
        links_by_count = {
            row.normalized_metadata["citation_count"]: row for row in links
        }
        assert article.title == "First title"
        assert article.url == "https://pubmed.example/first"
        assert source.snapshot_id == links_by_count[5].snapshot_id
        assert source.snapshot_id != observation.snapshot_id
        assert source.url == "https://pubmed.example/later"
        assert source.source_database == "NLM"
        assert source.normalized_metadata["citation_count"] == 5
        assert set(links_by_count) == {0, 5}
        citations = session.scalars(select(m.CitationObservation)).all()
        assert {(row.count, row.snapshot_id) for row in citations} == {
            (0, links_by_count[0].snapshot_id), (5, links_by_count[5].snapshot_id),
        }
        assert len(session.scalars(select(m.OutboxEvent)).all()) == 1


def test_invalid_citation_count_rolls_back_the_provider_page(ingestion_harness):
    harness = ingestion_harness
    query_id = _query(harness, "invalid citation count", providers=("pubmed",))
    record = _record("888", "Invalid count", provider="pubmed")
    # Simulate a buggy/custom provider that bypassed ProviderRecord validation.
    object.__setattr__(record, "citation_count", True)
    provider = FakeProvider("pubmed", [_page("pubmed", (record,))])

    with pytest.raises(ValueError, match="citation_count must be a non-negative integer"):
        _run(harness, (provider,), query_id, "pubmed")

    with harness.session_factory() as session:
        run = session.scalar(select(m.SearchRun))
        assert run is not None and run.state == "failed"
        assert session.scalars(select(m.Article)).all() == []
        assert session.scalars(select(m.SourceRecord)).all() == []
        assert session.scalars(select(m.SourceSnapshot)).all() == []
        assert session.scalars(select(m.CitationObservation)).all() == []
        assert session.scalars(select(m.SearchRunObservation)).all() == []


def test_cancelled_run_is_marked_failed(ingestion_harness):
    harness = ingestion_harness
    query_id = _query(harness, "cancelled", providers=("pubmed",))
    provider = FakeProvider("pubmed", [asyncio.CancelledError()])

    with pytest.raises(asyncio.CancelledError):
        _run(harness, (provider,), query_id, "pubmed")

    with harness.session_factory() as session:
        run = session.scalar(select(m.SearchRun))
        assert run.state == "failed"
        assert run.error == "CancelledError"
