"""Durable, transactional schedule claims and execution history."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from research_engine.ports.scheduling import QuerySchedule, ScheduledClaim, ScheduledExecution

from . import models as m


def _schedule(row: m.QuerySchedule) -> QuerySchedule:
    return QuerySchedule(
        row.id, row.query_id, row.interval_minutes, row.next_due_at, row.enabled,
    )


def _execution(session: Session, row: m.ScheduledExecution) -> ScheduledExecution:
    run_ids = tuple(session.scalars(select(m.SearchRun.id).where(
        m.SearchRun.scheduled_execution_id == row.id
    ).order_by(m.SearchRun.started_at, m.SearchRun.id)).all())
    return ScheduledExecution(
        row.id, row.query_id, row.state, row.started_at,
        row.ended_at, row.error_code, run_ids,
    )


class SqlAlchemyScheduleStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def configure(
        self, query_id: str, *, interval_minutes: int,
        enabled: bool, next_due_at: datetime,
    ) -> QuerySchedule:
        if interval_minutes < 1:
            raise ValueError("schedule interval must be at least one minute")
        if next_due_at.tzinfo is None or next_due_at.utcoffset() is None:
            raise ValueError("next_due_at must be timezone-aware")
        with self._session_factory.begin() as session:
            query = session.get(m.SavedQuery, str(query_id))
            if query is None:
                raise ValueError("saved query does not exist")
            project = session.get(m.ResearchProject, query.project_id)
            if enabled and (
                not query.is_active or query.archived_at is not None
                or project is None or project.archived_at is not None
                or not query.enabled_providers
            ):
                raise ValueError("enabled schedule requires an active project/query and providers")
            row = session.scalar(select(m.QuerySchedule).where(
                m.QuerySchedule.query_id == str(query_id)
            ))
            if row is None:
                timestamp = datetime.now(timezone.utc)
                row = m.QuerySchedule(
                    query_id=str(query_id), interval_minutes=interval_minutes,
                    next_due_at=next_due_at, enabled=enabled,
                    created_at=timestamp, updated_at=timestamp,
                )
                session.add(row)
                session.flush()
            else:
                row.interval_minutes = interval_minutes
                row.next_due_at = next_due_at
                row.enabled = enabled
                row.updated_at = datetime.now(timezone.utc)
            return _schedule(row)

    def get_schedule(self, query_id: str) -> QuerySchedule | None:
        with self._session_factory() as session:
            row = session.scalar(select(m.QuerySchedule).where(
                m.QuerySchedule.query_id == str(query_id)
            ))
            return _schedule(row) if row else None

    def claim_due(self, now: datetime) -> ScheduledClaim | None:
        try:
            with self._session_factory.begin() as session:
                running = exists(select(m.ScheduledExecution.id).where(
                    m.ScheduledExecution.query_id == m.QuerySchedule.query_id,
                    m.ScheduledExecution.state == "running",
                ))
                provider_running = exists(select(m.SearchRun.id).where(
                    m.SearchRun.query_id == m.QuerySchedule.query_id,
                    m.SearchRun.state == "running",
                ))
                row = session.scalar(
                    select(m.QuerySchedule)
                    .join(m.SavedQuery, m.QuerySchedule.query_id == m.SavedQuery.id)
                    .join(m.ResearchProject, m.SavedQuery.project_id == m.ResearchProject.id)
                    .where(
                        m.QuerySchedule.enabled.is_(True),
                        m.QuerySchedule.next_due_at <= now,
                        m.SavedQuery.is_active.is_(True),
                        m.SavedQuery.archived_at.is_(None),
                        m.ResearchProject.archived_at.is_(None),
                        ~running,
                        ~provider_running,
                    )
                    .order_by(m.QuerySchedule.next_due_at, m.QuerySchedule.id)
                    .limit(1)
                )
                if row is None:
                    return None
                query = session.get(m.SavedQuery, row.query_id)
                providers = tuple(dict.fromkeys(query.enabled_providers))
                execution = m.ScheduledExecution(
                    schedule_id=row.id, query_id=row.query_id,
                    state="running", started_at=now,
                )
                session.add(execution)
                row.next_due_at = now + timedelta(minutes=row.interval_minutes)
                row.updated_at = now
                session.flush()
                return ScheduledClaim(execution.id, row.query_id, providers)
        except IntegrityError as exc:
            # A competing process claimed the same query first. Surface other
            # integrity failures instead of hiding schema/data corruption.
            if "scheduled_executions.query_id" in str(exc).lower() or "uq_scheduled_executions_running_query" in str(exc).lower():
                return None
            raise
        except OperationalError as exc:
            if "database is locked" in str(exc).lower():
                return None
            raise

    def finish(
        self, execution_id: str, *, state: str,
        ended_at: datetime, error_code: str | None = None,
    ) -> ScheduledExecution:
        if state not in {"completed", "partial", "failed"}:
            raise ValueError("invalid scheduled execution terminal state")
        with self._session_factory.begin() as session:
            row = session.get(m.ScheduledExecution, execution_id)
            if row is None or row.state != "running":
                raise ValueError("scheduled execution is absent or not running")
            row.state = state
            row.ended_at = ended_at
            row.error_code = error_code if state == "failed" else None
            return _execution(session, row)

    def recover(self, execution_id: str, *, ended_at: datetime) -> ScheduledExecution:
        with self._session_factory.begin() as session:
            row = session.get(m.ScheduledExecution, execution_id)
            if row is None or row.state != "running":
                raise ValueError("scheduled execution is absent or not running")
            for run in session.scalars(select(m.SearchRun).where(
                m.SearchRun.scheduled_execution_id == execution_id,
                m.SearchRun.state == "running",
            )):
                run.state = "failed"
                run.ended_at = ended_at
                run.error = "InterruptedProcess"
                attempt = session.scalar(select(m.ExecutionAttempt).where(
                    m.ExecutionAttempt.run_id == run.id,
                ))
                if attempt is not None and attempt.state == "running":
                    attempt.state = "failed"
                    attempt.ended_at = ended_at
                    attempt.error_code = "InterruptedProcess"
            row.state = "failed"
            row.ended_at = ended_at
            row.error_code = "InterruptedProcess"
            session.add(m.OutboxEvent(
                type="ScheduledExecutionRecovered", schema_version=1,
                aggregate_type="SavedQuery", aggregate_id=row.query_id,
                payload={"execution_id": row.id, "action": "marked_failed"},
                occurred_at=ended_at,
            ))
            return _execution(session, row)

    def list_executions(self, query_id: str) -> tuple[ScheduledExecution, ...]:
        with self._session_factory() as session:
            rows = session.scalars(select(m.ScheduledExecution).where(
                m.ScheduledExecution.query_id == str(query_id)
            ).order_by(m.ScheduledExecution.started_at.desc(), m.ScheduledExecution.id.desc())).all()
            return tuple(_execution(session, row) for row in rows)

    def list_running(self) -> tuple[ScheduledExecution, ...]:
        with self._session_factory() as session:
            rows = session.scalars(select(m.ScheduledExecution).where(
                m.ScheduledExecution.state == "running"
            ).order_by(m.ScheduledExecution.started_at, m.ScheduledExecution.id)).all()
            return tuple(_execution(session, row) for row in rows)
