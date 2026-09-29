"""Transactional SQLAlchemy adapter for recovery operations."""

from __future__ import annotations

from datetime import datetime
from typing import Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from research_engine.domain.events import DomainEvent
from research_engine.ports.ingestion import CoverageCheckpoint
from research_engine.ports.recovery import ExecutionAttemptSummary, RunningExecutionAttempt, RunningProviderRun
from research_engine.ports.scheduling import QuerySchedule

from . import models as m


def _coverage(row: m.MonitoringCoverage) -> CoverageCheckpoint:
    return CoverageCheckpoint(
        row.id, row.query_id, row.provider, row.lane, row.revision_id,
        row.signature, row.cursor, row.complete, row.published_from,
        row.published_to, row.last_completed_at,
    )


def _running_run(row: m.SearchRun) -> RunningProviderRun:
    return RunningProviderRun(
        row.id, row.query_id, row.provider, row.started_at,
        row.scheduled_execution_id, row.cursor, row.result_count,
    )


def _running_attempt(row: m.ExecutionAttempt) -> RunningExecutionAttempt:
    return RunningExecutionAttempt(
        row.id, row.requested_query_id, row.requested_provider,
        row.request_reference, row.started_at, row.run_id,
    )


def _schedule(row: m.QuerySchedule) -> QuerySchedule:
    return QuerySchedule(
        row.id, row.query_id, row.interval_minutes, row.next_due_at, row.enabled,
    )


def _event(
    session: Session, event_type: str, query_id: str, payload: dict[str, str], at: datetime,
) -> None:
    event = DomainEvent(
        type=event_type,
        aggregate_type="SavedQuery",
        aggregate_id=UUID(query_id),
        payload=payload,
        occurred_at=at,
    )
    session.add(m.OutboxEvent(
        id=str(event.id), type=event.type, schema_version=event.schema_version,
        aggregate_type=event.aggregate_type, aggregate_id=query_id,
        payload=dict(event.payload), occurred_at=event.occurred_at,
    ))


class SqlAlchemyRecoveryStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def list_attempts(self, query_id: str | None = None, *, limit: int = 200, offset: int = 0):
        with self._session_factory() as session:
            statement = select(m.ExecutionAttempt).order_by(
                m.ExecutionAttempt.started_at.desc(), m.ExecutionAttempt.id.desc(),
            )
            if query_id is not None:
                statement = statement.where(m.ExecutionAttempt.requested_query_id == query_id)
            rows = session.scalars(statement.limit(limit).offset(offset)).all()
            return tuple(ExecutionAttemptSummary(
                row.id, row.requested_query_id, row.requested_provider, row.request_reference,
                row.started_at, row.ended_at, row.state, row.error_code, row.run_id,
            ) for row in rows)

    def list_running_runs(self) -> tuple[RunningProviderRun, ...]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(m.SearchRun)
                .where(m.SearchRun.state == "running")
                .order_by(m.SearchRun.started_at, m.SearchRun.id)
            ).all()
            return tuple(_running_run(row) for row in rows)

    def list_running_attempts(self) -> tuple[RunningExecutionAttempt, ...]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(m.ExecutionAttempt)
                .where(m.ExecutionAttempt.state == "running")
                .order_by(m.ExecutionAttempt.started_at, m.ExecutionAttempt.id)
            ).all()
            return tuple(_running_attempt(row) for row in rows)

    def list_coverage(self, query_id: str) -> tuple[CoverageCheckpoint, ...]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(m.MonitoringCoverage)
                .where(
                    m.MonitoringCoverage.query_id == str(query_id),
                    m.MonitoringCoverage.is_active.is_(True),
                )
                .order_by(m.MonitoringCoverage.provider, m.MonitoringCoverage.lane)
            ).all()
            return tuple(_coverage(row) for row in rows)

    def recover_run(self, run_id: str, *, ended_at: datetime) -> None:
        with self._session_factory.begin() as session:
            row = session.get(m.SearchRun, str(run_id))
            if row is None or row.state != "running":
                raise ValueError("provider run is missing or no longer running")
            if row.scheduled_execution_id is not None:
                raise ValueError(
                    "provider run belongs to a scheduled execution; recover that execution instead"
                )
            row.state = "failed"
            row.ended_at = ended_at
            row.error = "InterruptedProcess"
            attempt = session.scalar(select(m.ExecutionAttempt).where(
                m.ExecutionAttempt.run_id == row.id,
            ))
            if attempt is not None and attempt.state == "running":
                attempt.state = "failed"
                attempt.ended_at = ended_at
                attempt.error_code = "InterruptedProcess"
            _event(
                session,
                "SearchRunRecovered",
                row.query_id,
                {"run_id": row.id, "action": "marked_failed"},
                ended_at,
            )

    def recover_attempt(self, attempt_id: str, *, ended_at: datetime) -> None:
        with self._session_factory.begin() as session:
            row = session.get(m.ExecutionAttempt, str(attempt_id))
            if row is None or row.state != "running":
                raise ValueError("execution attempt is missing or no longer running")
            if row.run_id is not None:
                raise ValueError(
                    "execution attempt has a provider run; recover that run or its scheduled execution instead"
                )
            row.state = "failed"
            row.ended_at = ended_at
            row.error_code = "InterruptedProcess"
            event = DomainEvent(
                type="ExecutionAttemptRecovered",
                aggregate_type="ExecutionAttempt",
                aggregate_id=UUID(row.id),
                payload={"attempt_id": row.id, "action": "marked_failed"},
                occurred_at=ended_at,
            )
            session.add(m.OutboxEvent(
                id=str(event.id), type=event.type, schema_version=event.schema_version,
                aggregate_type=event.aggregate_type, aggregate_id=row.id,
                payload=dict(event.payload), occurred_at=event.occurred_at,
            ))

    def restart_coverage(
        self, query_id: str, provider: str, lane: str, *, requested_at: datetime,
    ) -> None:
        query_id = str(query_id)
        with self._session_factory.begin() as session:
            query = session.get(m.SavedQuery, query_id)
            if query is None:
                raise ValueError("saved query does not exist")
            if session.scalar(
                select(m.SearchRun.id)
                .where(
                    m.SearchRun.query_id == query_id,
                    m.SearchRun.state == "running",
                )
                .limit(1)
            ) is not None or session.scalar(
                select(m.ScheduledExecution.id)
                .where(
                    m.ScheduledExecution.query_id == query_id,
                    m.ScheduledExecution.state == "running",
                )
                .limit(1)
            ) is not None:
                raise ValueError("coverage cannot be restarted while a query worker is running")

            row = session.scalar(
                select(m.MonitoringCoverage).where(
                    m.MonitoringCoverage.query_id == query_id,
                    m.MonitoringCoverage.provider == provider,
                    m.MonitoringCoverage.lane == lane,
                    m.MonitoringCoverage.is_active.is_(True),
                )
            )
            if row is None:
                raise ValueError("active monitoring coverage does not exist")
            row.is_active = False
            row.updated_at = requested_at
            _event(
                session,
                "CoverageRestartRequested",
                query_id,
                {
                    "coverage_id": row.id,
                    "provider": provider,
                    "lane": lane,
                    "action": "restart_requested",
                },
                requested_at,
            )

    def queue_retry(self, query_id: str, *, requested_at: datetime) -> QuerySchedule:
        query_id = str(query_id)
        with self._session_factory.begin() as session:
            query = session.get(m.SavedQuery, query_id)
            if query is None or not query.is_active or query.archived_at is not None:
                raise ValueError("saved query is missing or inactive")
            project = session.get(m.ResearchProject, query.project_id)
            if project is None or project.archived_at is not None:
                raise ValueError("owning project is missing or archived")
            schedule = session.scalar(
                select(m.QuerySchedule).where(m.QuerySchedule.query_id == query_id)
            )
            if schedule is None or not schedule.enabled:
                raise ValueError("query has no enabled schedule")
            if session.scalar(
                select(m.SearchRun.id)
                .where(
                    m.SearchRun.query_id == query_id,
                    m.SearchRun.state == "running",
                )
                .limit(1)
            ) is not None or session.scalar(
                select(m.ScheduledExecution.id)
                .where(
                    m.ScheduledExecution.query_id == query_id,
                    m.ScheduledExecution.state == "running",
                )
                .limit(1)
            ) is not None:
                raise ValueError("retry cannot be queued while a query worker is running")

            schedule.next_due_at = requested_at
            schedule.updated_at = requested_at
            _event(
                session,
                "ScheduledRetryRequested",
                query_id,
                {"schedule_id": schedule.id, "action": "retry_requested"},
                requested_at,
            )
            return _schedule(schedule)
