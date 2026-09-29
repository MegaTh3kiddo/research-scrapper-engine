"""Transactional SQLAlchemy implementation of the ingestion port."""

from __future__ import annotations

from datetime import datetime
import hashlib
import string
from typing import Callable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from research_engine.domain.classification import is_newly_published
from research_engine.domain.events import DomainEvent
from research_engine.domain.identity import (
    IdentityResolutionKind,
    decide_identity_resolution,
    normalize_identifiers,
    normalize_provider_identifier,
)
from research_engine.domain.identity_review import assertion_signature, possible_duplicate_key
from research_engine.ports.ingestion import LoadedQuery, PageOutcome, CoverageCheckpoint, CoverageContext, RetryRun
from research_engine.ports.providers import (
    ProviderQueryPlan as PortQueryPlan,
    ProviderRecord,
    SearchPage,
    SearchRequest,
)
from research_engine.ports.snapshots import StoredSnapshot

from . import models as m
from .filter_codec import decode_filters
from .record_metadata import source_assertions


_SAFE_AUDIT_CHARACTERS = frozenset(string.ascii_letters + string.digits + "_.-")


def _safe_audit_identifier(value: object, max_length: int) -> str:
    if not isinstance(value, str):
        return "unknown"
    # Bound the work as well as the stored value. Only identifier-safe ASCII
    # prefix characters are retained; delimiters commonly used by URLs and
    # key/value credentials terminate capture instead of exposing later text.
    prefix: list[str] = []
    for char in value[:max_length]:
        if char not in _SAFE_AUDIT_CHARACTERS:
            break
        prefix.append(char)
    safe = "".join(prefix)
    return safe or "unknown"


def _safe_error_code(value: object | None) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else type(value).__name__
    safe = "".join(char for char in text[:100] if char in _SAFE_AUDIT_CHARACTERS)
    return safe or "ProviderError"


class SqlAlchemyIngestionStore:
    """Persist one provider page and its outbox events in one DB transaction."""

    def __init__(self, session_factory: Callable[[], Session], *,
                 source_priority: Mapping[str, int] | None = None) -> None:
        self._session_factory = session_factory
        self._source_priority = dict(source_priority if source_priority is not None else {
            "pubmed": 4, "europe_pmc": 3, "crossref": 2, "openalex": 1,
        })

    def load_coverage(self, query_id: str, provider: str, lane: str) -> CoverageCheckpoint | None:
        with self._session_factory() as session:
            row = session.scalar(select(m.MonitoringCoverage).where(
                m.MonitoringCoverage.query_id == query_id,
                m.MonitoringCoverage.provider == provider,
                m.MonitoringCoverage.lane == lane,
                m.MonitoringCoverage.is_active.is_(True),
            ))
            return None if row is None else CoverageCheckpoint(
                row.id, row.query_id, row.provider, row.lane, row.revision_id,
                row.signature, row.cursor, row.complete, row.published_from,
                row.published_to, row.last_completed_at,
            )

    def load_retry(self, run_id: str) -> RetryRun:
        with self._session_factory() as session:
            row = session.get(m.SearchRun, run_id)
            if row is None or row.state not in {"failed", "partial"}:
                raise ValueError("only failed or partial runs can be retried")
            if row.scheduled_execution_id or row.coverage_id:
                raise ValueError("scheduled runs must retry through their monitoring coverage")
            plan = session.get(m.ProviderQueryPlan, row.plan_id)
            if plan is None or row.query_revision_id is None:
                raise ValueError("legacy or unplanned runs must start a new search")
            parser = session.scalar(select(m.SourceSnapshot.parser_version).where(
                m.SourceSnapshot.search_run_id == row.id,
            ).order_by(m.SourceSnapshot.captured_at.desc(), m.SourceSnapshot.id).limit(1))
            return RetryRun(row.id, row.query_id, row.query_revision_id, row.provider,
                            row.cursor, PortQueryPlan(row.provider, plan.translated_query,
                            plan.translation_version, tuple(plan.unsupported_filters), plan.parameters), parser)

    def begin_attempt(
        self,
        requested_query_id: str,
        requested_provider: str,
        started_at: datetime,
        *,
        request_reference: str | None = None,
    ) -> str:
        if started_at.tzinfo is None or started_at.utcoffset() is None:
            raise ValueError("execution attempt time must be timezone-aware")
        with self._session_factory.begin() as session:
            row = m.ExecutionAttempt(
                requested_query_id=_safe_audit_identifier(requested_query_id, 36),
                requested_provider=_safe_audit_identifier(requested_provider, 100),
                request_reference=(
                    _safe_audit_identifier(request_reference, 100)
                    if request_reference is not None else None
                ),
                started_at=started_at,
                state="running",
            )
            session.add(row)
            session.flush()
            return row.id

    def update_attempt_request(
        self, attempt_id: str, requested_query_id: str, requested_provider: str,
    ) -> None:
        with self._session_factory.begin() as session:
            row = session.get(m.ExecutionAttempt, str(attempt_id))
            if row is None or row.state != "running" or row.run_id is not None:
                raise ValueError("execution attempt cannot accept resolved request identifiers")
            row.requested_query_id = _safe_audit_identifier(requested_query_id, 36)
            row.requested_provider = _safe_audit_identifier(requested_provider, 100)

    def finish_attempt(
        self,
        attempt_id: str,
        *,
        state: str,
        ended_at: datetime,
        error_code: str | None = None,
    ) -> None:
        if state not in {"completed", "partial", "failed", "skipped"}:
            raise ValueError("invalid execution-attempt terminal state")
        if ended_at.tzinfo is None or ended_at.utcoffset() is None:
            raise ValueError("execution-attempt end time must be timezone-aware")
        with self._session_factory.begin() as session:
            row = session.get(m.ExecutionAttempt, str(attempt_id))
            if row is None:
                raise ValueError("execution attempt does not exist")
            # The SearchRun owns finalization after linking. This makes the
            # application wrapper safe to call after any post-run exception.
            if row.run_id is not None or row.state != "running":
                return
            row.state = state
            row.ended_at = ended_at
            row.error_code = _safe_error_code(error_code)

    def load_request(
        self, query_id: str, provider_name: str, page_size: int
    ) -> LoadedQuery:
        with self._session_factory() as session:
            legacy_pubmed = session.scalar(select(m.SourceRecord.id).where(
                m.SourceRecord.provider == "pubmed",
                m.SourceRecord.parser_version == "pubmed-parser-v1",
            ).limit(1))
            if legacy_pubmed is not None:
                raise ValueError(
                    "legacy PubMed records may contain false merges; back up this database "
                    "and rebuild in a new data directory before running more searches"
                )
            query = session.get(m.SavedQuery, query_id)
            if query is None or not query.is_active or query.archived_at is not None:
                raise ValueError("saved query is missing or inactive")
            project = session.get(m.ResearchProject, query.project_id)
            if project is None or project.archived_at is not None:
                raise ValueError("owning project is missing or archived")
            if query.enabled_providers and provider_name not in query.enabled_providers:
                raise ValueError("provider is not enabled for this saved query")
            revision = session.scalar(select(m.SavedQueryRevision).where(
                m.SavedQueryRevision.query_id == query.id,
                m.SavedQueryRevision.revision_number == query.revision_number,
            ))
            if revision is None or not _matches_revision(query, revision):
                raise ValueError("saved query has no matching immutable revision")
            return LoadedQuery(
                SearchRequest(query.expression, decode_filters(query.filters), page_size),
                revision.id,
            )

    def begin_run(
        self,
        query_id: str,
        revision_id: str,
        plan: PortQueryPlan,
        started_at: datetime,
        recent_window_days: int,
        scheduled_execution_id: str | None = None,
        coverage: CoverageContext | None = None,
        start_cursor: str | None = None,
        retry_of_run_id: str | None = None,
        attempt_id: str | None = None,
    ) -> str:
        if plan.unsupported_filters:
            raise ValueError("cannot persist a plan with unsupported filters")
        with self._session_factory() as session, session.begin():
            query = session.get(m.SavedQuery, query_id)
            revision = session.get(m.SavedQueryRevision, revision_id)
            if query is None or revision is None or revision.query_id != query_id or revision.revision_number != query.revision_number or not _matches_revision(query, revision):
                raise ValueError("saved query changed before run began")
            project = session.get(m.ResearchProject, query.project_id)
            if not query.is_active or query.archived_at is not None or project is None or project.archived_at is not None:
                raise ValueError("saved query or project is inactive")
            if session.scalar(select(m.SearchRun.id).where(
                m.SearchRun.query_id == query_id, m.SearchRun.state == "running",
            ).limit(1)) is not None:
                raise ValueError("query already has a running provider search; recover or wait for its worker")
            if scheduled_execution_id is None and session.scalar(select(m.ScheduledExecution.id).where(
                m.ScheduledExecution.query_id == query_id, m.ScheduledExecution.state == "running",
            ).limit(1)) is not None:
                raise ValueError("query has a running scheduled execution")
            if scheduled_execution_id is not None:
                execution = session.get(m.ScheduledExecution, scheduled_execution_id)
                if execution is None or execution.query_id != query_id or execution.state != "running":
                    raise ValueError("scheduled execution is missing or inactive")
            attempt = None
            if attempt_id is not None:
                attempt = session.get(m.ExecutionAttempt, str(attempt_id))
                if (
                    attempt is None or attempt.state != "running" or attempt.run_id is not None
                    or attempt.requested_query_id != _safe_audit_identifier(query_id, 36)
                    or attempt.requested_provider != _safe_audit_identifier(plan.provider, 100)
                ):
                    raise ValueError("execution attempt is missing or does not match this request")
            checkpoint = None
            if coverage is not None:
                if scheduled_execution_id is None or coverage.lane not in {"full", "recent"}:
                    raise ValueError("coverage requires a running scheduled execution and valid lane")
                checkpoint = session.scalar(select(m.MonitoringCoverage).where(
                    m.MonitoringCoverage.query_id == query_id,
                    m.MonitoringCoverage.provider == plan.provider,
                    m.MonitoringCoverage.lane == coverage.lane,
                    m.MonitoringCoverage.is_active.is_(True),
                ))
                if (checkpoint.id if checkpoint else None) != coverage.previous_id:
                    raise ValueError("monitoring coverage changed before run began")
                resume = checkpoint is not None and not checkpoint.complete and (
                    checkpoint.revision_id == revision_id and checkpoint.signature == coverage.signature
                )
                if resume:
                    if checkpoint.cursor != coverage.cursor:
                        raise ValueError("monitoring checkpoint changed before run began")
                    running = session.scalar(select(m.SearchRun.id).where(
                        m.SearchRun.coverage_id == checkpoint.id, m.SearchRun.state == "running",
                    ))
                    if running:
                        raise ValueError("coverage already has a running search; recover it before retry")
                else:
                    if coverage.cursor is not None:
                        raise ValueError("new coverage must start at the first page")
                    if checkpoint:
                        checkpoint.is_active = False
                        session.flush()
                    checkpoint = m.MonitoringCoverage(
                        query_id=query_id, provider=plan.provider, revision_id=revision_id,
                        lane=coverage.lane, signature=coverage.signature,
                        published_from=coverage.published_from, published_to=coverage.published_to,
                        created_at=started_at, updated_at=started_at,
                    )
                    session.add(checkpoint)
                    session.flush()
            db_plan = m.ProviderQueryPlan(
                query_id=query_id,
                provider=plan.provider,
                translated_query=plan.translated_query,
                parameters=dict(plan.parameters),
                unsupported_filters=list(plan.unsupported_filters),
                translation_version=plan.translation_version,
            )
            session.add(db_plan)
            session.flush()
            run = m.SearchRun(
                query_id=query_id,
                query_revision_id=revision_id,
                scheduled_execution_id=scheduled_execution_id,
                plan_id=db_plan.id,
                provider=plan.provider,
                state="running",
                started_at=started_at,
                recent_window_days=recent_window_days,
                coverage_id=checkpoint.id if checkpoint else None,
                start_cursor=start_cursor,
                cursor=start_cursor,
            )
            session.add(run)
            session.flush()
            if attempt is not None:
                attempt.run_id = run.id
            if retry_of_run_id:
                predecessor = session.get(m.SearchRun, retry_of_run_id)
                if not predecessor or predecessor.state not in {"failed", "partial"} or predecessor.query_id != query_id or predecessor.provider != plan.provider or predecessor.query_revision_id != revision_id or predecessor.scheduled_execution_id:
                    raise ValueError("retry predecessor is incompatible")
                session.add(m.OutboxEvent(
                    type="SearchRunRetryStarted", schema_version=1, aggregate_type="SavedQuery",
                    aggregate_id=query_id, payload={"previous_run_id": predecessor.id, "run_id": run.id},
                    occurred_at=started_at,
                ))
            return run.id

    def persist_page(
        self,
        run_id: str,
        plan: PortQueryPlan,
        page: SearchPage,
        stored_snapshots: tuple[StoredSnapshot, ...],
        parser_version: str,
        observed_at: datetime,
    ) -> PageOutcome:
        if len(stored_snapshots) != len(page.raw_responses):
            raise ValueError("each raw response requires one stored snapshot")
        if not parser_version.strip():
            raise ValueError("parser_version must be non-empty")
        for raw, stored in zip(page.raw_responses, stored_snapshots, strict=True):
            if stored.sha256 != hashlib.sha256(raw.body).hexdigest() or stored.byte_size != len(raw.body):
                raise ValueError("stored snapshot does not match its raw response")
        with self._session_factory() as session, session.begin():
            run = session.get(m.SearchRun, run_id)
            if run is None or run.state != "running" or run.provider != plan.provider:
                raise ValueError("run is absent, closed, or belongs to another provider")
            snapshot_rows: list[m.SourceSnapshot] = []
            for raw, stored in zip(page.raw_responses, stored_snapshots, strict=True):
                snapshot = m.SourceSnapshot(
                    provider=plan.provider,
                    search_run_id=run_id,
                    storage_key=stored.storage_key,
                    sha256=stored.sha256,
                    parser_version=parser_version,
                    content_type=raw.media_type,
                    compression=stored.compression,
                    byte_size=stored.byte_size,
                    captured_at=raw.fetched_at,
                    request_metadata=dict(raw.safe_request_metadata),
                )
                session.add(snapshot)
                snapshot_rows.append(snapshot)
            session.flush()

            tally = PageOutcome()
            for record in page.records:
                outcome = self._persist_record(
                    session,
                    run,
                    record,
                    snapshot_rows[record.response_index],
                    parser_version,
                    observed_at,
                )
                tally = PageOutcome(
                    observed=tally.observed + outcome.observed,
                    newly_discovered=tally.newly_discovered + outcome.newly_discovered,
                    new_to_query=tally.new_to_query + outcome.new_to_query,
                    newly_published=tally.newly_published + outcome.newly_published,
                    metadata_updated=tally.metadata_updated + outcome.metadata_updated,
                    conflicts=tally.conflicts + outcome.conflicts,
                )
            run.result_count += tally.observed
            run.new_count += tally.newly_discovered
            run.newly_published_count += tally.newly_published
            run.updated_count += tally.metadata_updated
            run.conflict_count += tally.conflicts
            if run.coverage_id:
                coverage_row = session.get(m.MonitoringCoverage, run.coverage_id)
                execution = session.get(m.ScheduledExecution, run.scheduled_execution_id)
                if not coverage_row or not coverage_row.is_active or coverage_row.complete or not execution or execution.state != "running":
                    raise ValueError("coverage or its owning execution is no longer active")
                seen = list(coverage_row.seen_cursors or []) + [run.cursor]
                if page.next_cursor is not None and page.next_cursor in seen:
                    raise ValueError("provider repeated a cursor across monitoring attempts")
                coverage_row.seen_cursors = seen
                coverage_row.cursor = page.next_cursor
                coverage_row.pages_completed += 1
                coverage_row.updated_at = observed_at
                if page.next_cursor is None:
                    coverage_row.complete = True
                    coverage_row.last_completed_at = observed_at
            run.cursor = page.next_cursor
            return tally

    def _persist_record(
        self,
        session: Session,
        run: m.SearchRun,
        record: ProviderRecord,
        snapshot: m.SourceSnapshot,
        parser_version: str,
        observed_at: datetime,
    ) -> PageOutcome:
        provider = run.provider
        prior_conflict = session.scalar(
            select(m.IdentityConflict.id).where(
                m.IdentityConflict.run_id == run.id,
                m.IdentityConflict.provider == provider,
                m.IdentityConflict.source_id == record.source_id,
            )
        )
        if prior_conflict is not None:
            return PageOutcome()
        tokens = list(normalize_identifiers(record.identifiers, provider=provider))
        provider_token = normalize_provider_identifier(provider, record.source_id)
        if provider_token is None:
            raise ValueError("source record ID must be non-empty")
        if provider_token not in tokens:
            tokens.append(provider_token)
        tokens = sorted(tokens)

        existing_source = session.scalar(
            select(m.SourceRecord).where(
                m.SourceRecord.provider == provider,
                m.SourceRecord.provider_record_id == record.source_id,
            )
        )
        normalized_metadata = source_assertions(record)
        if existing_source is not None:
            prior = session.scalar(
                select(m.SearchRunObservation.id).where(
                    m.SearchRunObservation.run_id == run.id,
                    m.SearchRunObservation.source_record_id == existing_source.id,
                )
            )
            if prior is not None:
                # A repeated source record in one run is evidence, not a second
                # canonical update. Preserve its normalized/raw link and latest
                # source view without changing its committed classification.
                _save_source_assertion(
                    session, existing_source, snapshot, observed_at, normalized_metadata
                )
                if snapshot.captured_at >= existing_source.fetched_at:
                    existing_source.snapshot_id = snapshot.id
                    existing_source.fetched_at = snapshot.captured_at
                    existing_source.parser_version = snapshot.parser_version
                    existing_source.identifiers = [
                        {"scheme": item.scheme, "value": item.value}
                        for item in record.identifiers
                    ]
                    existing_source.source_database = record.source_database
                    existing_source.url = record.url
                    existing_source.normalized_metadata = normalized_metadata
                _save_citation_observation(
                    session, existing_source.article_id, provider, record.citation_count,
                    snapshot, observed_at,
                )
                return PageOutcome()
        candidate_ids: set[str] = set()
        decision = session.scalar(select(m.IdentityReviewDecision).where(
            m.IdentityReviewDecision.provider == provider,
            m.IdentityReviewDecision.source_id == record.source_id,
            m.IdentityReviewDecision.assertion_signature == assertion_signature(
                provider, record.source_id, record.identifiers,
            ),
            m.IdentityReviewDecision.action.in_(("select_article", "reject_source")),
        ).order_by(m.IdentityReviewDecision.reviewed_at.desc(), m.IdentityReviewDecision.id.desc()).limit(1))
        if decision is not None and decision.action == "reject_source":
            first_observation = _record_review_application(
                session, decision, run, record, snapshot, observed_at,
                "SourceAssertionRejected",
            )
            return PageOutcome(observed=1 if first_observation else 0)
        if decision is not None:
            # A review never transfers an existing source or identifier owner.
            # Newly conflicting assertions still enter the ordinary conflict path.
            if existing_source is not None and existing_source.article_id != decision.selected_article_id:
                decision = None
            else:
                rejected = {(item["scheme"], item["value"]) for item in decision.rejected_identifiers}
                tokens = [token for token in tokens if token.key not in rejected]
                candidate_ids.add(decision.selected_article_id)
        if existing_source is not None:
            candidate_ids.add(existing_source.article_id)
        for token in tokens:
            found = session.scalar(
                select(m.ArticleIdentifier.article_id).where(
                    m.ArticleIdentifier.scheme == token.scheme,
                    m.ArticleIdentifier.value == token.value,
                )
            )
            if found is not None:
                candidate_ids.add(found)
        resolution = decide_identity_resolution(candidate_ids)
        if resolution.kind is IdentityResolutionKind.CONFLICT:
            session.add(
                m.IdentityConflict(
                    run_id=run.id,
                    provider=provider,
                    source_id=record.source_id,
                    snapshot_id=snapshot.id,
                    identifiers={"asserted": [
                        {"scheme": item.scheme, "value": item.value}
                        for item in record.identifiers
                    ]},
                    candidate_article_ids=list(resolution.candidate_article_ids),
                    status="unresolved",
                    created_at=observed_at,
                )
            )
            return PageOutcome(observed=1, conflicts=1)

        created = resolution.kind is IdentityResolutionKind.NEW
        if created:
            article = m.Article(title=record.title, created_at=observed_at, updated_at=observed_at)
            session.add(article)
            session.flush()
        else:
            article = session.get(m.Article, resolution.article_id)
            if article is None:
                raise ValueError("identity index points to a missing article")

        if decision is not None and article.id == decision.selected_article_id:
            _record_review_application(
                session, decision, run, record, snapshot, observed_at,
                "SourceIdentityDecisionApplied", article_id=article.id,
            )

        for token in tokens:
            found = session.scalar(
                select(m.ArticleIdentifier).where(
                    m.ArticleIdentifier.scheme == token.scheme,
                    m.ArticleIdentifier.value == token.value,
                )
            )
            if found is None:
                session.add(
                    m.ArticleIdentifier(
                        article_id=article.id, scheme=token.scheme, value=token.value
                    )
                )

        metadata_changed = _apply_canonical_metadata(
            session, article, record, provider, created, observed_at, self._source_priority,
            {"provider": provider, "source_id": record.source_id, "snapshot_id": snapshot.id,
             "run_id": run.id, "parser_version": parser_version},
        )
        _flag_possible_duplicates(session, article, snapshot, observed_at)
        asserted_identifiers = [
            {"scheme": identifier.scheme, "value": identifier.value}
            for identifier in record.identifiers
        ]
        if existing_source is None:
            source = m.SourceRecord(
                provider=provider,
                provider_record_id=record.source_id,
                article_id=article.id,
                snapshot_id=snapshot.id,
                fetched_at=snapshot.captured_at,
                parser_version=parser_version,
                identifiers=asserted_identifiers,
                source_database=record.source_database,
                url=record.url,
                normalized_metadata=normalized_metadata,
            )
            session.add(source)
            session.flush()
        else:
            source = existing_source
            if source.article_id != article.id:
                raise ValueError("source identity changed unexpectedly")
            source.snapshot_id = snapshot.id
            source.fetched_at = snapshot.captured_at
            source.parser_version = parser_version
            source.identifiers = asserted_identifiers
            source.source_database = record.source_database
            source.url = record.url
            source.normalized_metadata = normalized_metadata
        _save_source_assertion(
            session, source, snapshot, observed_at, normalized_metadata
        )
        _save_citation_observation(
            session, article.id, provider, record.citation_count, snapshot, observed_at
        )

        query_match = session.get(m.QueryMatch, (run.query_id, article.id))
        new_to_query = query_match is None
        if query_match is None:
            session.add(
                m.QueryMatch(
                    query_id=run.query_id,
                    article_id=article.id,
                    first_seen_at=observed_at,
                    last_seen_at=observed_at,
                    first_run_id=run.id,
                    last_run_id=run.id,
                )
            )
        else:
            query_match.last_seen_at = observed_at
            query_match.last_run_id = run.id

        project_id = session.scalar(select(m.SavedQuery.project_id).where(m.SavedQuery.id == run.query_id))
        if project_id is None:
            raise ValueError("search run has no owning project")
        relation = session.get(m.ProjectArticle, (project_id, article.id))
        if relation is None:
            session.add(m.ProjectArticle(
                project_id=project_id, article_id=article.id,
                first_seen_at=observed_at, last_seen_at=observed_at,
            ))
        else:
            relation.last_seen_at = observed_at

        recent = is_newly_published(
            record.publication_date, run.started_at, run.recent_window_days
        )
        session.add(
            m.SearchRunObservation(
                run_id=run.id,
                provider=provider,
                source_record_id=source.id,
                article_id=article.id,
                snapshot_id=snapshot.id,
                observed_at=observed_at,
                newly_discovered=created,
                new_to_query=new_to_query,
                newly_published=recent,
                metadata_updated=metadata_changed,
            )
        )
        if created:
            _add_event(session, "PaperDiscovered", article.id, run, source, snapshot, recent, observed_at)
        elif metadata_changed:
            _add_event(session, "PaperMetadataUpdated", article.id, run, source, snapshot, recent, observed_at)
        return PageOutcome(
            observed=1,
            newly_discovered=int(created),
            new_to_query=int(new_to_query),
            newly_published=int(recent is True),
            metadata_updated=int(metadata_changed),
        )

    def finish_run(
        self,
        run_id: str,
        *,
        state: str,
        cursor: str | None,
        ended_at: datetime,
        error: str | None = None,
    ) -> None:
        if state not in {"completed", "partial", "failed"}:
            raise ValueError("invalid terminal run state")
        with self._session_factory() as session, session.begin():
            run = session.get(m.SearchRun, run_id)
            if run is None or run.state != "running":
                raise ValueError("run is absent or already finished")
            run.state = state
            run.cursor = cursor
            run.ended_at = ended_at
            run.error = _safe_error_code(error)
            attempt = session.scalar(select(m.ExecutionAttempt).where(
                m.ExecutionAttempt.run_id == run.id,
            ))
            if attempt is not None and attempt.state == "running":
                attempt.state = state
                attempt.ended_at = ended_at
                attempt.error_code = _safe_error_code(error)


def _matches_revision(query: m.SavedQuery, revision: m.SavedQueryRevision) -> bool:
    return (
        query.expression == revision.expression
        and query.filters == revision.filters
        and query.enabled_providers == revision.enabled_providers
        and query.is_active == revision.is_active
        and query.archived_at == revision.archived_at
    )


def _apply_canonical_metadata(
    session: Session,
    article: m.Article,
    record: ProviderRecord,
    provider: str,
    created: bool,
    observed_at: datetime,
    source_priority: Mapping[str, int],
    assertion: dict[str, str],
) -> bool:
    providers = session.scalars(select(m.SourceRecord.provider).where(
        m.SourceRecord.article_id == article.id,
    )).all()
    incoming_order = (-source_priority.get(provider, 0), provider)
    provenance = dict(article.canonical_metadata_provenance or {})

    def accepts(field: str, missing: bool = False) -> bool:
        winner = provenance.get(field, {}).get("provider")
        if winner is None:
            permitted = missing or not any(
                (-source_priority.get(existing, 0), existing) < incoming_order
                for existing in providers
            )
        else:
            permitted = incoming_order <= (-source_priority.get(winner, 0), winner)
        if permitted:
            provenance[field] = dict(assertion)
        return permitted

    changed = False
    if accepts("title", created or not article.title):
        if article.title != record.title:
            article.title = record.title
            changed = True
    if record.abstract and accepts("abstract", article.abstract is None):
        if article.abstract != record.abstract:
            article.abstract = record.abstract
            changed = True
    if record.publication_date is not None and accepts("publication_date", article.publication_year is None):
        publication = record.publication_date
        old_date = (article.publication_year, article.publication_month, article.publication_day)
        new_date = (publication.year, publication.month, publication.day)
        if old_date != new_date:
            article.publication_year, article.publication_month, article.publication_day = new_date
            changed = True
    if record.journal_name and accepts("journal", article.journal_id is None):
        journal = session.scalar(select(m.Journal).where(m.Journal.title == record.journal_name))
        if journal is None:
            journal = m.Journal(title=record.journal_name)
            session.add(journal)
            session.flush()
        if article.journal_id != journal.id:
            article.journal_id = journal.id
            changed = True
    if record.authors:
        existing_authors = session.scalars(
            select(m.ArticleAuthor).where(m.ArticleAuthor.article_id == article.id)
            .order_by(m.ArticleAuthor.position)
        ).all()
        if accepts("authors", not existing_authors) and tuple(row.display_name for row in existing_authors) != record.authors:
            for row in existing_authors:
                session.delete(row)
            session.flush()
            for position, display_name in enumerate(record.authors):
                session.add(m.ArticleAuthor(
                    article_id=article.id, position=position, display_name=display_name
                ))
            changed = True
    if record.url and accepts("url", article.url is None) and article.url != record.url:
        article.url = record.url
        changed = True
    if record.publication_types and accepts(
        "publication_types",
        not article.publication_types and article.publication_type is None,
    ):
        publication_types = list(record.publication_types)
        if (
            article.publication_types != publication_types
            or article.publication_type != publication_types[0]
        ):
            article.publication_types = publication_types
            # Keep the original single-type column synchronized for older
            # clients while exposing the full source taxonomy to new clients.
            article.publication_type = publication_types[0]
            changed = True
    if record.publication_status and accepts(
        "publication_status", article.publication_status is None
    ) and article.publication_status != record.publication_status:
        article.publication_status = record.publication_status
        changed = True
    article.canonical_metadata_provenance = provenance
    for keyword in record.keywords:
        key = keyword.casefold().strip()
        if not key:
            continue
        topic = session.scalar(select(m.Topic).where(
            m.Topic.vocabulary == "provider_keyword", m.Topic.normalized_key == key
        ))
        if topic is None:
            topic = m.Topic(vocabulary="provider_keyword", normalized_key=key, label=keyword)
            session.add(topic)
            session.flush()
        existing_assignment = session.get(m.ArticleTopic, (article.id, topic.id, provider))
        if existing_assignment is None:
            session.add(m.ArticleTopic(
                article_id=article.id, topic_id=topic.id, source=provider,
                assignment_method="provider", observed_at=observed_at,
            ))
    if changed:
        article.updated_at = observed_at
    return changed and not created


def _record_review_application(
    session: Session, decision: m.IdentityReviewDecision, run: m.SearchRun,
    record: ProviderRecord, snapshot: m.SourceSnapshot, observed_at: datetime,
    event_type: str, *, article_id: str | None = None,
) -> bool:
    from uuid import UUID, uuid5
    prior = session.scalar(select(m.OutboxEvent.id).where(
        m.OutboxEvent.aggregate_id == decision.id, m.OutboxEvent.type == event_type,
        m.OutboxEvent.payload["run_id"].as_string() == run.id,
    ).limit(1))
    event_id = str(uuid5(UUID(decision.id), f"{event_type}:{run.id}:{snapshot.id}"))
    if session.get(m.OutboxEvent, event_id) is None:
        payload = {"decision_id": decision.id, "run_id": run.id, "provider": run.provider,
                   "source_id": record.source_id, "snapshot_id": snapshot.id}
        if article_id is not None:
            payload["article_id"] = article_id
        session.add(m.OutboxEvent(id=event_id, type=event_type, schema_version=1,
                                 aggregate_type="IdentityReviewDecision", aggregate_id=decision.id,
                                 payload=payload, occurred_at=observed_at))
    return prior is None


def _flag_possible_duplicates(session: Session, article: m.Article, snapshot: m.SourceSnapshot,
                              observed_at: datetime) -> None:
    key = possible_duplicate_key(article.title, article.publication_year)
    article.possible_duplicate_key = key
    if key is None:
        return
    session.flush()
    others = session.scalars(select(m.Article.id).where(
        m.Article.possible_duplicate_key == key, m.Article.id != article.id,
    )).all()
    for other_id in others:
        left, right = sorted((article.id, other_id))
        existing = session.scalar(select(m.PossibleDuplicateCandidate.id).where(
            m.PossibleDuplicateCandidate.left_article_id == left,
            m.PossibleDuplicateCandidate.right_article_id == right,
        ))
        if existing is None:
            candidate = m.PossibleDuplicateCandidate(
                left_article_id=left, right_article_id=right, snapshot_id=snapshot.id,
                reason="same_normalized_title_and_publication_year", created_at=observed_at,
            )
            session.add(candidate)
            session.flush()
            session.add(m.OutboxEvent(
                type="PossibleDuplicateFlagged", schema_version=1,
                aggregate_type="PossibleDuplicateCandidate", aggregate_id=candidate.id,
                payload={"candidate_id": candidate.id, "left_article_id": left,
                         "right_article_id": right, "snapshot_id": snapshot.id}, occurred_at=observed_at,
            ))


def _save_source_assertion(
    session: Session,
    source: m.SourceRecord,
    snapshot: m.SourceSnapshot,
    observed_at: datetime,
    normalized_metadata: dict,
) -> None:
    association = session.get(
        m.SourceRecordSnapshot, (source.id, snapshot.id)
    )
    if association is None:
        session.add(m.SourceRecordSnapshot(
            source_record_id=source.id,
            snapshot_id=snapshot.id,
            provider=source.provider,
            observed_at=observed_at,
            normalized_metadata=normalized_metadata,
        ))
    elif (association.normalized_metadata or {}) != normalized_metadata:
        raise ValueError("one source snapshot contains conflicting normalized assertions")


def _save_citation_observation(
    session: Session,
    article_id: str,
    provider: str,
    citation_count: int | None,
    snapshot: m.SourceSnapshot,
    observed_at: datetime,
) -> None:
    if citation_count is not None and (
        isinstance(citation_count, bool)
        or not isinstance(citation_count, int)
        or citation_count < 0
    ):
        raise ValueError("citation_count must be a non-negative integer or None")
    if citation_count is None:
        return
    observation = session.scalar(select(m.CitationObservation).where(
        m.CitationObservation.article_id == article_id,
        m.CitationObservation.snapshot_id == snapshot.id,
    ))
    if observation is not None:
        if observation.provider != provider or observation.count != citation_count:
            raise ValueError("one source snapshot contains conflicting citation counts")
        return
    session.add(m.CitationObservation(
        article_id=article_id,
        provider=provider,
        count=citation_count,
        as_of=snapshot.captured_at or observed_at,
        snapshot_id=snapshot.id,
    ))


def _add_event(
    session: Session,
    event_type: str,
    article_id: str,
    run: m.SearchRun,
    source: m.SourceRecord,
    snapshot: m.SourceSnapshot,
    newly_published: bool | None,
    observed_at: datetime,
) -> None:
    from uuid import UUID

    event = DomainEvent(
        type=event_type,
        aggregate_type="Article",
        aggregate_id=UUID(article_id),
        payload={
            "run_id": run.id,
            "query_id": run.query_id,
            "provider": run.provider,
            "source_record_id": source.id,
            "snapshot_id": snapshot.id,
            "newly_published": newly_published,
        },
        occurred_at=observed_at,
    )
    session.add(m.OutboxEvent(
        id=str(event.id), type=event.type, schema_version=event.schema_version,
        aggregate_type=event.aggregate_type, aggregate_id=str(event.aggregate_id),
        payload=dict(event.payload), occurred_at=event.occurred_at,
    ))
