from __future__ import annotations

import json
import csv
from io import StringIO
from pathlib import Path
from datetime import datetime, timezone
import asyncio

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
import pytest

from research_engine.application.ingestion import IngestionService
from research_engine.application.exports import ExportService
from research_engine.application.projects import ProjectService
from research_engine.application.provider_registry import ProviderRegistry
from research_engine.domain.value_objects import Identifier, PartialDate
from research_engine.infrastructure.persistence import models as m
from research_engine.infrastructure.persistence.base import Base
from research_engine.infrastructure.persistence.database import create_database_engine
from research_engine.infrastructure.persistence.ingestion import SqlAlchemyIngestionStore
from research_engine.infrastructure.persistence.projects import SqlAlchemyProjectStore
from research_engine.infrastructure.persistence.record_metadata import source_assertions
from research_engine.infrastructure.snapshots import LocalSnapshotStorage
from research_engine.ports.providers import (
    ProviderQueryPlan, ProviderRecord, RawResponse, SearchPage, SearchRequest,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _MetadataProvider:
    def __init__(self, name: str, records: list[ProviderRecord]) -> None:
        self.name = name
        self.parser_version = f"{name}-metadata-v1"
        self._records = list(records)

    def plan(self, request: SearchRequest) -> ProviderQueryPlan:
        return ProviderQueryPlan(
            provider=self.name,
            translated_query=request.expression,
            translation_version="metadata-test-v1",
            parameters={},
        )

    async def search(self, plan: ProviderQueryPlan, cursor: str | None = None) -> SearchPage:
        assert cursor is None
        record = self._records.pop(0)
        fetched_at = datetime.now(timezone.utc)
        return SearchPage(
            records=(record,),
            raw_responses=(RawResponse(
                body=f"{self.name}:{fetched_at.isoformat()}".encode(),
                media_type="application/json",
                fetched_at=fetched_at,
                safe_request_metadata={"provider": self.name},
            ),),
        )

    async def aclose(self) -> None:
        pass


def test_normalized_assertions_preserve_source_specific_metadata_and_unknowns():
    record = ProviderRecord(
        source_id="123", title="Paper", source_database="MED",
        authors=("Author One",), publication_date=PartialDate(2026, 9),
        identifiers=(Identifier("pmid", "123"),), url="https://example.org/paper",
        citation_count=0, publication_types=("Journal Article", "Review"),
        publication_status="epublish",
    )
    metadata = json.loads(json.dumps(source_assertions(record)))
    assert metadata["source_database"] == "MED"
    assert metadata["citation_count"] == 0
    assert metadata["publication_types"] == ["Journal Article", "Review"]
    assert metadata["publication_date"] == {"year": 2026, "month": 9, "day": None}
    assert metadata["identifiers"] == [{"scheme": "pmid", "value": "123"}]
    missing = source_assertions(ProviderRecord("x", "Unknown", "MED"))
    assert missing["citation_count"] is None
    assert missing["publication_status"] is None


@pytest.mark.parametrize("value", [-1, True, 1.5, "4"])
def test_provider_record_rejects_invalid_citation_counts(value):
    with pytest.raises(ValueError, match="citation_count must be a non-negative integer"):
        ProviderRecord("bad-count", "Bad", "fixture", citation_count=value)


def test_metadata_migration_preserves_legacy_rows_and_links_new_citation_evidence(tmp_path):
    database_path = tmp_path / "metadata-completeness.sqlite3"
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "0007_execution_audit")

    engine = create_database_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO articles (id, title, created_at, updated_at) "
            "VALUES ('legacy-article', 'Legacy record', '2025-01-01 00:00:00', "
            "'2025-01-01 00:00:00')"
        ))
        connection.execute(text(
            "INSERT INTO source_snapshots "
            "(id, provider, search_run_id, storage_key, sha256, parser_version, "
            "content_type, compression, byte_size, captured_at, request_metadata) "
            "VALUES ('legacy-snapshot', 'openalex', NULL, 'legacy.raw', "
            "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
            "'openalex-v1', 'application/json', NULL, 10, '2025-01-02 00:00:00', '{}')"
        ))
        connection.execute(text(
            "INSERT INTO source_records "
            "(id, provider, provider_record_id, article_id, snapshot_id, identifiers, "
            "fetched_at, parser_version, rights_note) "
            "VALUES ('legacy-source', 'openalex', 'W123', 'legacy-article', "
            "'legacy-snapshot', '[]', '2025-01-02 00:00:00', 'openalex-v1', NULL)"
        ))
        connection.execute(text(
            "INSERT INTO source_record_snapshots "
            "(source_record_id, snapshot_id, provider, observed_at) "
            "VALUES ('legacy-source', 'legacy-snapshot', 'openalex', '2025-01-02 00:00:00')"
        ))
        connection.execute(text(
            "INSERT INTO citation_observations (id, article_id, provider, count, as_of) "
            "VALUES ('legacy-citation', 'legacy-article', 'openalex', 3, "
            "'2025-01-02 00:00:00')"
        ))

    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    command.upgrade(config, "head")
    with engine.begin() as connection:
        article = connection.execute(text(
            "SELECT id, title, url, publication_types FROM articles WHERE id='legacy-article'"
        )).mappings().one()
        assert (article["id"], article["title"], article["url"]) == (
            "legacy-article", "Legacy record", None,
        )
        assert json.loads(article["publication_types"]) == []
        latest = connection.execute(text(
            "SELECT source_database, url, normalized_metadata FROM source_records "
            "WHERE id='legacy-source'"
        )).mappings().one()
        assert latest["source_database"] is None
        assert latest["url"] is None
        assert json.loads(latest["normalized_metadata"]) == {}
        historical = connection.execute(text(
            "SELECT normalized_metadata FROM source_record_snapshots "
            "WHERE source_record_id='legacy-source'"
        )).scalar_one()
        assert json.loads(historical) == {}
        old_observation = connection.execute(text(
            "SELECT count, snapshot_id FROM citation_observations "
            "WHERE id='legacy-citation'"
        )).one()
        assert old_observation == (3, None)

        # Citation counts are source-specific and zero is an observed value, not
        # an absent count. Distinct snapshots preserve independent attribution.
        for suffix, provider in (("oa", "openalex"), ("pm", "pubmed")):
            connection.execute(text(
                "INSERT INTO source_snapshots "
                "(id, provider, search_run_id, storage_key, sha256, parser_version, "
                "content_type, compression, byte_size, captured_at, request_metadata) "
                "VALUES (:id, :provider, NULL, :key, :hash, 'fixture-v1', "
                "'application/json', NULL, 0, '2025-02-01 00:00:00', '{}')"
            ), {
                "id": f"snapshot-{suffix}", "provider": provider,
                "key": f"{suffix}.raw", "hash": suffix * 32,
            })
        connection.execute(text(
            "INSERT INTO citation_observations "
            "(id, article_id, provider, count, as_of, snapshot_id) "
            "VALUES ('citation-oa', 'legacy-article', 'openalex', 0, "
            "'2025-02-01 00:00:00', 'snapshot-oa')"
        ))
        connection.execute(text(
            "INSERT INTO citation_observations "
            "(id, article_id, provider, count, as_of, snapshot_id) "
            "VALUES ('citation-pm', 'legacy-article', 'pubmed', 7, "
            "'2025-02-01 00:00:00', 'snapshot-pm')"
        ))
        counts = connection.execute(text(
            "SELECT provider, count, snapshot_id FROM citation_observations "
            "WHERE snapshot_id IS NOT NULL ORDER BY provider"
        )).all()
        assert counts == [
            ("openalex", 0, "snapshot-oa"), ("pubmed", 7, "snapshot-pm"),
        ]
        assert {row["name"] for row in inspect(connection).get_columns("articles")} >= {
            "url", "publication_types",
        }

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO citation_observations "
                "(id, article_id, provider, count, as_of, snapshot_id) "
                "VALUES ('citation-duplicate', 'legacy-article', 'openalex', 1, "
                "'2025-02-02 00:00:00', 'snapshot-oa')"
            ))
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO citation_observations "
                "(id, article_id, provider, count, as_of, snapshot_id) "
                "VALUES ('citation-wrong-source', 'legacy-article', 'pubmed', 1, "
                "'2025-02-02 00:00:00', 'snapshot-oa')"
            ))

    command.downgrade(config, "0007_execution_audit")
    with engine.connect() as connection:
        assert "snapshot_id" not in {
            column["name"] for column in inspect(connection).get_columns("citation_observations")
        }
        assert connection.execute(text(
            "SELECT id, count FROM citation_observations ORDER BY id"
        )).all() == [
            ("citation-oa", 0), ("citation-pm", 7), ("legacy-citation", 3),
        ]
    engine.dispose()


def test_ingestion_keeps_source_assertion_history_and_latest_counts_per_provider(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'metadata-ingestion.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    projects = ProjectService(SqlAlchemyProjectStore(factory))
    project = projects.create_project("Metadata review")
    query = projects.create_query(
        project.id, "cancer", enabled_providers=("openalex", "pubmed"),
    )
    doi = Identifier("doi", "10.5555/shared-paper")
    openalex = _MetadataProvider("openalex", [
        ProviderRecord(
            source_id="W123", title="Index title", source_database="OpenAlex",
            identifiers=(doi,), url="https://openalex.example/W123",
            citation_count=0, publication_types=("article",),
            publication_status="retracted",
        ),
        ProviderRecord(
            source_id="W123", title="Index title", source_database="OpenAlex",
            identifiers=(doi,), url="https://openalex.example/W123",
            citation_count=2, publication_types=("article",),
            publication_status="retracted",
        ),
    ])
    pubmed = _MetadataProvider("pubmed", [
        ProviderRecord(
            source_id="12345", title="PubMed title", source_database="NLM",
            identifiers=(doi, Identifier("pmid", "12345")),
            url="https://pubmed.ncbi.nlm.nih.gov/12345/",
            publication_types=("Journal Article", "Review"),
            publication_status="aheadofprint",
        ),
        ProviderRecord(
            source_id="12345", title="PubMed title", source_database="NLM",
            identifiers=(doi, Identifier("pmid", "12345")),
            url="https://pubmed.ncbi.nlm.nih.gov/12345/",
            citation_count=7, publication_types=("Journal Article", "Review"),
            publication_status="aheadofprint",
        ),
    ])
    storage = LocalSnapshotStorage(tmp_path / "metadata-snapshots")
    ingestion = IngestionService(
        ProviderRegistry((openalex, pubmed)),
        SqlAlchemyIngestionStore(factory),
        storage,
    )
    for provider in ("openalex", "pubmed", "openalex", "pubmed"):
        asyncio.run(ingestion.run(str(query.id), provider))

    with factory() as session:
        article = session.scalar(select(m.Article))
        assert article is not None
        assert article.url == "https://pubmed.ncbi.nlm.nih.gov/12345/"
        assert article.publication_types == ["Journal Article", "Review"]
        assert article.publication_type == "Journal Article"
        assert article.publication_status == "aheadofprint"
        assert article.canonical_metadata_provenance["url"]["provider"] == "pubmed"
        assert article.canonical_metadata_provenance["publication_types"]["provider"] == "pubmed"
        assert article.canonical_metadata_provenance["publication_status"]["provider"] == "pubmed"

        source_records = session.scalars(select(m.SourceRecord)).all()
        assert {(row.provider, row.source_database, row.url) for row in source_records} == {
            ("openalex", "OpenAlex", "https://openalex.example/W123"),
            ("pubmed", "NLM", "https://pubmed.ncbi.nlm.nih.gov/12345/"),
        }
        assert {row.normalized_metadata["citation_count"] for row in source_records} == {2, 7}
        history = session.scalars(select(m.SourceRecordSnapshot)).all()
        assert len(history) == 4
        assert {row.normalized_metadata["source_database"] for row in history} == {
            "OpenAlex", "NLM",
        }
        assert {row.normalized_metadata["publication_status"] for row in history} == {
            "retracted", "aheadofprint",
        }
        citations = session.scalars(select(m.CitationObservation)).all()
        assert len(citations) == 3  # the first missing PubMed count is not fabricated
        assert all(row.snapshot_id is not None for row in citations)
        assert {(row.provider, row.count) for row in citations} == {
            ("openalex", 0), ("openalex", 2), ("pubmed", 7),
        }

        detail = SqlAlchemyProjectStore(factory).get_paper_detail(project.id, article.id)
        assert detail is not None
        assert detail.url == article.url
        assert detail.publication_types == ("Journal Article", "Review")
        assert detail.publication_status == "aheadofprint"
        assert {item.provider: item.count for item in detail.citations} == {
            "openalex": 2, "pubmed": 7,
        }
        assert all(item.snapshot_id for item in detail.citations)
        assert {(item.provider, item.source_database, item.url) for item in detail.source_metadata} == {
            ("openalex", "OpenAlex", "https://openalex.example/W123"),
            ("pubmed", "NLM", "https://pubmed.ncbi.nlm.nih.gov/12345/"),
        }
        csv_document = ExportService(projects).export_project(project.id, "csv")
        csv_row = next(csv.DictReader(StringIO(csv_document.content.decode("utf-8"))))
        assert csv_row["publication_types"] == "Journal Article; Review"
        assert csv_row["publication_status"] == "aheadofprint"
        assert csv_row["source_databases"] == "NLM; OpenAlex"
        exported_citations = json.loads(csv_row["citation_observations"])
        assert {item["provider"]: item["count"] for item in exported_citations} == {
            "openalex": 2, "pubmed": 7,
        }
        ris = ExportService(projects).export_project(project.id, "ris").content.decode("utf-8")
        assert "N1  - Publication type: Journal Article\r\n" in ris
        assert "N1  - Publication status: aheadofprint\r\n" in ris
        assert "N1  - Source database: pubmed/NLM\r\n" in ris
        assert "N1  - Citation count: openalex=2; as of " in ris
        events = session.scalars(select(m.OutboxEvent).where(
            m.OutboxEvent.aggregate_id == article.id,
        )).all()
        assert any(event.type == "PaperMetadataUpdated" for event in events)

    engine.dispose()
