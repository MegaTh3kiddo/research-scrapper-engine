from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from research_engine.application.identity_review import IdentityReviewService
from research_engine.application.ingestion import IngestionService
from research_engine.application.projects import ProjectService
from research_engine.application.provider_registry import ProviderRegistry
from research_engine.domain.identity import normalize_identifiers
from research_engine.domain.identity_review import assertion_signature
from research_engine.domain.value_objects import Identifier, PartialDate
from research_engine.infrastructure.persistence import models as m
from research_engine.infrastructure.persistence import identity_models as im
from research_engine.infrastructure.persistence.base import Base
from research_engine.infrastructure.persistence.database import create_database_engine
from research_engine.infrastructure.persistence.identity_review import (
    SqlAlchemyIdentityReviewStore,
)
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


PROVIDER = "identity-review-fixture"


class ReviewProvider:
    name = PROVIDER
    parser_version = "identity-review-fixture-v1"

    def __init__(self, pages: list[SearchPage]):
        self.pages = list(pages)

    def plan(self, request: SearchRequest) -> ProviderQueryPlan:
        return ProviderQueryPlan(
            provider=self.name,
            translated_query=request.expression,
            translation_version="identity-review-test-v1",
        )

    async def search(self, plan: ProviderQueryPlan, cursor: str | None = None) -> SearchPage:
        return self.pages.pop(0)

    async def aclose(self) -> None:
        pass


@dataclass
class Harness:
    factory: sessionmaker
    project_id: str
    query_id: str
    ingestion_store: SqlAlchemyIngestionStore
    snapshot_directory: Path
    review: IdentityReviewService


@pytest.fixture
def identity_harness(tmp_path: Path):
    # Importing these mappings here makes a missing schema registration fail
    # directly, rather than letting metadata tests silently omit the tables.
    assert im.IdentityReviewDecision.__table__.metadata is Base.metadata
    assert im.PossibleDuplicateCandidate.__table__.metadata is Base.metadata
    engine = create_database_engine(f"sqlite:///{tmp_path / 'identity-review.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)

    project_store = ProjectService(SqlAlchemyProjectStore(factory))
    project = project_store.create_project("Identity review project")
    query = project_store.create_query(
        project.id, "identity review workflow", enabled_providers=(PROVIDER,),
    )
    ingestion_store = SqlAlchemyIngestionStore(factory)
    snapshot_directory = tmp_path / "snapshots"
    review = IdentityReviewService(SqlAlchemyIdentityReviewStore(factory))
    try:
        yield Harness(
            factory, str(project.id), str(query.id), ingestion_store,
            snapshot_directory, review,
        )
    finally:
        engine.dispose()


def _record(
    source_id: str,
    title: str,
    *identifiers: Identifier,
    year: int | None = None,
) -> ProviderRecord:
    return ProviderRecord(
        source_id=source_id,
        title=title,
        source_database=PROVIDER,
        identifiers=tuple(identifiers),
        publication_date=PartialDate(year) if year is not None else None,
    )


def _page(*records: ProviderRecord) -> SearchPage:
    fetched_at = datetime.now(timezone.utc)
    body = ("identity-review:" + ",".join(item.source_id for item in records)).encode()
    return SearchPage(records=tuple(records), raw_responses=(RawResponse(
        body=body,
        media_type="application/json",
        fetched_at=fetched_at,
        safe_request_metadata={"provider": PROVIDER},
    ),))


def _run(harness: Harness, *records: ProviderRecord):
    provider = ReviewProvider([_page(*records)])
    service = IngestionService(
        ProviderRegistry((provider,)), harness.ingestion_store,
        LocalSnapshotStorage(harness.snapshot_directory),
    )
    return asyncio.run(service.run(harness.query_id, PROVIDER))


def _seed_article(factory, title: str, *identifiers: Identifier) -> str:
    tokens = normalize_identifiers(identifiers, provider=PROVIDER)
    with factory.begin() as session:
        article = m.Article(title=title)
        session.add(article)
        session.flush()
        session.add_all(m.ArticleIdentifier(
            article_id=article.id, scheme=token.scheme, value=token.value,
        ) for token in tokens)
        return article.id


def _article_ids(factory) -> set[str]:
    with factory() as session:
        return set(session.scalars(select(m.Article.id)))


def _events(factory, event_type: str | None = None):
    with factory() as session:
        statement = select(m.OutboxEvent).order_by(m.OutboxEvent.occurred_at, m.OutboxEvent.id)
        if event_type:
            statement = statement.where(m.OutboxEvent.type == event_type)
        return tuple(session.scalars(statement))


def test_select_decision_applies_only_to_exact_assertions_without_transferring_ids(
    identity_harness,
):
    harness = identity_harness
    doi = Identifier("DOI", "https://doi.org/10.5555/selection")
    pmid = Identifier("PMID", " PMID: 77123 ")
    article_a = _seed_article(harness.factory, "DOI owner", doi)
    article_b = _seed_article(harness.factory, "PMID owner", pmid)
    incoming = _record("selection-source", "Incoming record", doi, pmid)

    conflict_run = _run(harness, incoming)
    assert conflict_run.outcome.conflicts == 1
    conflicts = harness.review.list_conflicts(harness.project_id)
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert {candidate.article_id for candidate in conflict.candidates} == {article_a, article_b}

    # A partial or over-broad rejection must not become a review decision.
    with pytest.raises(ValueError, match="all and only"):
        harness.review.review_conflict(
            conflict.id, action="select_article", article_id=article_a,
            rejected_identifiers=(doi,), reason="wrong asserted identifier",
        )
    assert len(harness.review.list_conflicts(harness.project_id)) == 1

    rejected = Identifier("pmid", "77123")
    expected_signature = assertion_signature(PROVIDER, incoming.source_id, incoming.identifiers)
    harness.review.review_conflict(
        conflict.id, action="select_article", article_id=article_a,
        rejected_identifiers=(rejected,), reason="DOI verified against source record",
    )

    accepted = _run(harness, incoming)
    assert accepted.outcome.newly_discovered == 0
    assert accepted.outcome.new_to_query == 1
    assert accepted.outcome.conflicts == 0
    with harness.factory() as session:
        source = session.scalar(select(m.SourceRecord).where(
            m.SourceRecord.provider == PROVIDER,
            m.SourceRecord.provider_record_id == incoming.source_id,
        ))
        decision = session.scalar(select(im.IdentityReviewDecision).where(
            im.IdentityReviewDecision.conflict_id == conflict.id,
        ))
        doi_owner = session.scalar(select(m.ArticleIdentifier.article_id).where(
            m.ArticleIdentifier.scheme == "doi",
            m.ArticleIdentifier.value == "10.5555/selection",
        ))
        pmid_owner = session.scalar(select(m.ArticleIdentifier.article_id).where(
            m.ArticleIdentifier.scheme == "pmid",
            m.ArticleIdentifier.value == "77123",
        ))
        assert source.article_id == article_a
        assert decision.assertion_signature == expected_signature
        assert decision.rejected_identifiers == [{"scheme": "pmid", "value": "77123"}]
        assert decision.reason == "DOI verified against source record"
        assert decision.snapshot_id == conflict.snapshot_id
        assert decision.reviewed_at.tzinfo is not None
        assert doi_owner == article_a
        assert pmid_owner == article_b

    repeated = _run(harness, incoming)
    assert repeated.outcome.conflicts == 0
    assert repeated.outcome.new_to_query == 0
    assert len(harness.review.list_conflicts(harness.project_id)) == 0
    assert len(_article_ids(harness.factory)) == 2

    with harness.factory() as session:
        provenance = session.scalar(select(m.SourceRecordSnapshot).where(
            m.SourceRecordSnapshot.source_record_id == source.id,
            m.SourceRecordSnapshot.snapshot_id.in_(select(m.SourceSnapshot.id).where(
                m.SourceSnapshot.search_run_id == accepted.run_id,
            )),
        ))
        assert provenance is not None
        assert provenance.normalized_metadata["identifiers"] == [
            {"scheme": "DOI", "value": "https://doi.org/10.5555/selection"},
            {"scheme": "PMID", "value": " PMID: 77123 "},
        ]
    applied = [event for event in _events(harness.factory)
               if event.type == "SourceIdentityDecisionApplied"]
    assert any(event.payload["decision_id"] == decision.id for event in applied)

    # A changed identifier assertion cannot reuse the prior approval.
    changed = _record(
        incoming.source_id, incoming.title, doi, pmid,
        Identifier("PMCID", "PMC987654"),
    )
    changed_run = _run(harness, changed)
    assert changed_run.outcome.conflicts == 1
    unresolved = harness.review.list_conflicts(harness.project_id)
    assert len(unresolved) == 1
    assert unresolved[0].id != conflict.id
    with harness.factory() as session:
        assert session.scalar(select(m.SourceRecord.article_id).where(
            m.SourceRecord.provider_record_id == incoming.source_id,
        )) == article_a


def test_selecting_a_candidate_never_reparents_an_existing_source_record(identity_harness):
    harness = identity_harness
    doi = Identifier("DOI", "10.5555/existing-source")
    pmid = Identifier("PMID", "88221")
    article_a = _seed_article(harness.factory, "Existing owner", doi)
    article_b = _seed_article(harness.factory, "Other identifier owner", pmid)
    with harness.factory.begin() as session:
        snapshot = m.SourceSnapshot(
            provider=PROVIDER, storage_key="existing-source-snapshot",
            sha256="a" * 64, content_type="application/json", byte_size=0,
        )
        session.add(snapshot)
        session.flush()
        session.add(m.SourceRecord(
            provider=PROVIDER, provider_record_id="already-owned",
            article_id=article_a, snapshot_id=snapshot.id,
            identifiers=[{"scheme": "doi", "value": "10.5555/existing-source"}],
            parser_version="old-parser-v1",
            fetched_at=datetime.now(timezone.utc),
        ))

    _run(harness, _record("already-owned", "Changed assertion", doi, pmid))
    conflict = harness.review.list_conflicts(harness.project_id)[0]
    with pytest.raises(ValueError, match="ownership cannot be reassigned"):
        harness.review.review_conflict(
            conflict.id, action="select_article", article_id=article_b,
            rejected_identifiers=(Identifier("pmid", "88221"),),
            reason="Attempt to change source owner",
        )

    assert len(harness.review.list_conflicts(harness.project_id)) == 1
    with harness.factory() as session:
        source = session.scalar(select(m.SourceRecord).where(
            m.SourceRecord.provider_record_id == "already-owned",
        ))
        assert source.article_id == article_a
        assert session.scalar(select(func.count()).select_from(im.IdentityReviewDecision)) == 0


def test_reject_source_suppresses_only_the_matching_assertion_and_keeps_audit_evidence(
    identity_harness,
):
    harness = identity_harness
    doi = Identifier("DOI", "10.5555/reject")
    pmid = Identifier("PMID", "99331")
    article_a = _seed_article(harness.factory, "Rejected source DOI owner", doi)
    article_b = _seed_article(harness.factory, "Rejected source PMID owner", pmid)
    incoming = _record("rejected-source", "Rejected article", doi, pmid)
    initial_ids = _article_ids(harness.factory)

    _run(harness, incoming)
    conflict = harness.review.list_conflicts(harness.project_id)[0]
    harness.review.review_conflict(
        conflict.id, action="reject_source", reason="Source assertion is not trustworthy",
    )
    rejected = _run(harness, incoming)
    repeated = _run(harness, incoming)

    assert rejected.outcome.observed == repeated.outcome.observed == 1
    assert rejected.outcome.conflicts == repeated.outcome.conflicts == 0
    assert _article_ids(harness.factory) == initial_ids == {article_a, article_b}
    with harness.factory() as session:
        assert session.scalar(select(m.SourceRecord.id).where(
            m.SourceRecord.provider == PROVIDER,
            m.SourceRecord.provider_record_id == incoming.source_id,
        )) is None
        reviewed = session.get(m.IdentityConflict, conflict.id)
        decision = session.scalar(select(im.IdentityReviewDecision).where(
            im.IdentityReviewDecision.conflict_id == conflict.id,
        ))
        assert reviewed.status == "dismissed"
        assert decision.action == "reject_source"
        assert decision.reason == "Source assertion is not trustworthy"
        assert decision.snapshot_id == conflict.snapshot_id
        assert decision.reviewed_at.tzinfo is not None
        assert session.scalar(select(func.count()).select_from(m.SourceSnapshot).where(
            m.SourceSnapshot.search_run_id.in_((rejected.run_id, repeated.run_id)),
        )) == 2

    rejected_events = [event for event in _events(harness.factory)
                       if event.type == "SourceAssertionRejected"]
    assert len(rejected_events) == 2
    by_run = {event.payload["run_id"]: event for event in rejected_events}
    assert set(by_run) == {rejected.run_id, repeated.run_id}
    for run_id, event in by_run.items():
        assert event.payload["decision_id"] == decision.id
        assert event.payload["provider"] == PROVIDER
        assert event.payload["source_id"] == incoming.source_id
        assert event.payload["snapshot_id"] in {
            snapshot.id for snapshot in _snapshots_for_run(harness.factory, run_id)
        }

    changed = _record(
        incoming.source_id, incoming.title, doi, pmid,
        Identifier("PMCID", "PMC123456"),
    )
    _run(harness, changed)
    assert len(harness.review.list_conflicts(harness.project_id)) == 1


def _snapshots_for_run(factory, run_id: str):
    with factory() as session:
        return tuple(session.scalars(select(m.SourceSnapshot).where(
            m.SourceSnapshot.search_run_id == run_id,
        )))


def test_review_and_outbox_are_atomic_on_persistence_failure(identity_harness, monkeypatch):
    harness = identity_harness
    doi = Identifier("DOI", "10.5555/rollback")
    pmid = Identifier("PMID", "11223")
    article_a = _seed_article(harness.factory, "Rollback A", doi)
    _seed_article(harness.factory, "Rollback B", pmid)
    _run(harness, _record("rollback-source", "Rollback assertion", doi, pmid))
    conflict = harness.review.list_conflicts(harness.project_id)[0]

    def fail_outbox(*args, **kwargs):
        raise RuntimeError("injected outbox failure")

    from research_engine.infrastructure.persistence import identity_review as review_repository

    monkeypatch.setattr(review_repository, "_event", fail_outbox)
    with pytest.raises(RuntimeError, match="injected outbox failure"):
        harness.review.review_conflict(
            conflict.id, action="select_article", article_id=article_a,
            rejected_identifiers=(Identifier("pmid", "11223"),),
            reason="transaction rollback test",
        )

    assert len(harness.review.list_conflicts(harness.project_id)) == 1
    with harness.factory() as session:
        stored = session.get(m.IdentityConflict, conflict.id)
        assert stored.status == "unresolved"
        assert session.scalar(select(func.count()).select_from(im.IdentityReviewDecision)) == 0
        assert session.scalar(select(func.count()).select_from(m.OutboxEvent).where(
            m.OutboxEvent.type == "IdentityReviewed",
        )) == 0


def test_conflicts_are_project_scoped_and_reviewed_history_is_opt_in(identity_harness):
    harness = identity_harness
    projects = ProjectService(SqlAlchemyProjectStore(harness.factory))
    other_project = projects.create_project("Other project")
    projects.create_query(
        other_project.id, "other project query", enabled_providers=(PROVIDER,),
    )
    doi = Identifier("DOI", "10.5555/project-scope")
    pmid = Identifier("PMID", "22334")
    article_a = _seed_article(harness.factory, "Project scoped A", doi)
    _seed_article(harness.factory, "Project scoped B", pmid)

    _run(harness, _record("scoped-conflict", "Project scoped record", doi, pmid))
    conflict = harness.review.list_conflicts(harness.project_id)[0]
    assert harness.review.list_conflicts(str(other_project.id)) == ()

    harness.review.review_conflict(
        conflict.id, action="keep_separate", reason="Leave canonical records untouched",
    )
    assert harness.review.list_conflicts(harness.project_id) == ()
    reviewed = harness.review.list_conflicts(harness.project_id, include_reviewed=True)
    assert len(reviewed) == 1 and reviewed[0].status == "resolved"
    candidate_ids = {candidate.article_id for candidate in reviewed[0].candidates}
    assert article_a in candidate_ids and len(candidate_ids) == 2


def test_title_year_candidate_is_triaged_without_merging_canonical_articles(identity_harness):
    harness = identity_harness
    title = "A systematic review of precision oncology treatment response outcomes"
    first = _record("candidate-a", title, Identifier("DOI", "10.5555/candidate-a"), year=2024)
    second = _record(
        "candidate-b", title.upper() + ".", Identifier("DOI", "10.5555/candidate-b"), year=2024,
    )
    _run(harness, first, second)

    candidates = harness.review.list_candidates(harness.project_id)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.status == "unresolved"
    assert candidate.reason
    assert candidate.left.article_id != candidate.right.article_id
    assert candidate.left.title.casefold().rstrip(".") == candidate.right.title.casefold().rstrip(".")
    before = _article_ids(harness.factory)
    assert len(before) == 2

    harness.review.keep_candidate_separate(
        candidate.id, reason="Different cohorts despite matching title/year",
    )
    duplicate_rerun = _run(harness, first, second)
    assert duplicate_rerun.outcome.newly_discovered == 0
    assert len(_article_ids(harness.factory)) == 2
    assert len(harness.review.list_candidates(harness.project_id)) == 0
    reviewed = harness.review.list_candidates(harness.project_id, include_reviewed=True)
    assert len(reviewed) == 1 and reviewed[0].status == "kept_separate"
    with harness.factory() as session:
        decision = session.scalar(select(im.IdentityReviewDecision).where(
            im.IdentityReviewDecision.candidate_id == candidate.id,
        ))
        candidate_row = session.get(im.PossibleDuplicateCandidate, candidate.id)
        assert decision.action == "keep_separate"
        assert decision.reason == "Different cohorts despite matching title/year"
        assert decision.snapshot_id == candidate_row.snapshot_id
        assert decision.reviewed_at.tzinfo is not None
        source_articles = set(session.scalars(select(m.SourceRecord.article_id).where(
            m.SourceRecord.provider_record_id.in_(("candidate-a", "candidate-b")),
        )))
        doi_owners = set(session.scalars(select(m.ArticleIdentifier.article_id).where(
            m.ArticleIdentifier.scheme == "doi",
            m.ArticleIdentifier.value.in_(
                ("10.5555/candidate-a", "10.5555/candidate-b"),
            ),
        )))
        assert len(source_articles) == len(doi_owners) == 2
    assert any(event.type == "IdentityReviewed" and event.payload["candidate_id"] == candidate.id
               for event in _events(harness.factory))
