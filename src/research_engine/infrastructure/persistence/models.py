"""SQLAlchemy mappings for the research engine's initial relational schema."""

from __future__ import annotations

from datetime import datetime, date
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    JSON,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, new_id, utc_now
from .types import UTCDateTime


class ResearchProject(Base):
    __tablename__ = "research_projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )


class GoogleSheetLink(Base):
    __tablename__ = "google_sheet_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"], ["research_projects.id"],
            name="fk_google_sheet_links_project_id_research_projects", ondelete="RESTRICT",
        ),
    )

    project_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    spreadsheet_id: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_synced_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(100))


class SavedQuery(Base):
    __tablename__ = "saved_queries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    filters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    enabled_providers: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True, server_default=text("true"))
    revision_number: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"], ["research_projects.id"],
            name="fk_saved_queries_project_id_research_projects", ondelete="RESTRICT",
        ),
        Index("ix_saved_queries_project_active", "project_id", "is_active"),
    )


class SavedQueryRevision(Base):
    __tablename__ = "saved_query_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["query_id"], ["saved_queries.id"],
            name="fk_saved_query_revisions_query_id_saved_queries", ondelete="RESTRICT",
        ),
        CheckConstraint("revision_number >= 1", name="revision_number_positive"),
        UniqueConstraint(
            "query_id", "revision_number", name="uq_saved_query_revisions_query_revision"
        ),
        UniqueConstraint(
            "id", "query_id", name="uq_saved_query_revisions_id_query_id"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    query_id: Mapped[str] = mapped_column(String(36), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    filters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    enabled_providers: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class ProviderQueryPlan(Base):
    __tablename__ = "provider_query_plans"
    __table_args__ = (
        ForeignKeyConstraint(
            ["query_id"], ["saved_queries.id"],
            name="fk_provider_query_plans_query_id_saved_queries", ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "id", "query_id", "provider", name="uq_provider_query_plans_id_query_provider"
        ),
        Index("ix_provider_query_plans_query_provider", "query_id", "provider"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    query_id: Mapped[str] = mapped_column(String(36), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    translated_query: Mapped[str] = mapped_column(Text, nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    unsupported_filters: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    translation_version: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class SearchRun(Base):
    __tablename__ = "search_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["query_id"], ["saved_queries.id"],
            name="fk_search_runs_query_id_saved_queries", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["query_revision_id", "query_id"],
            ["saved_query_revisions.id", "saved_query_revisions.query_id"],
            name="fk_search_runs_query_revision_query_saved_query_revisions",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scheduled_execution_id", "query_id"],
            ["scheduled_executions.id", "scheduled_executions.query_id"],
            name="fk_search_runs_scheduled_execution_query_scheduled_executions",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["plan_id", "query_id", "provider"],
            ["provider_query_plans.id", "provider_query_plans.query_id", "provider_query_plans.provider"],
            name="fk_search_runs_plan_query_provider_provider_query_plans", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["coverage_id", "query_id", "provider"],
            ["monitoring_coverage.id", "monitoring_coverage.query_id", "monitoring_coverage.provider"],
            name="fk_search_runs_coverage_query_provider_monitoring_coverage", ondelete="RESTRICT",
        ),
        CheckConstraint("result_count >= 0", name="result_count_nonnegative"),
        CheckConstraint("new_count >= 0", name="new_count_nonnegative"),
        CheckConstraint("recent_window_days >= 0", name="recent_window_days_nonnegative"),
        CheckConstraint("updated_count >= 0", name="updated_count_nonnegative"),
        CheckConstraint(
            "newly_published_count >= 0", name="newly_published_count_nonnegative"
        ),
        CheckConstraint("conflict_count >= 0", name="conflict_count_nonnegative"),
        Index("ix_search_runs_query_started", "query_id", "started_at"),
        Index("ix_search_runs_plan_id", "plan_id"),
        Index("ix_search_runs_query_revision_id", "query_revision_id"),
        Index("ix_search_runs_scheduled_execution_id", "scheduled_execution_id"),
        Index("ix_search_runs_provider_started", "provider", "started_at"),
        Index("ix_search_runs_coverage_id", "coverage_id"),
        UniqueConstraint("id", "query_id", name="uq_search_runs_id_query_id"),
        UniqueConstraint("id", "provider", name="uq_search_runs_id_provider"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    query_id: Mapped[str] = mapped_column(String(36), nullable=False)
    query_revision_id: Mapped[str | None] = mapped_column(String(36))
    scheduled_execution_id: Mapped[str | None] = mapped_column(String(36))
    plan_id: Mapped[str | None] = mapped_column(String(36))
    coverage_id: Mapped[str | None] = mapped_column(String(36))
    start_cursor: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    cursor: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    new_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    recent_window_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30, server_default=text("30")
    )
    updated_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    newly_published_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    conflict_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    error: Mapped[str | None] = mapped_column(Text)


class ExecutionAttempt(Base):
    """Auditable request envelope, including attempts rejected before a SearchRun."""

    __tablename__ = "execution_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"], ["search_runs.id"],
            name="fk_execution_attempts_run_id_search_runs", ondelete="RESTRICT",
        ),
        CheckConstraint(
            "state IN ('running', 'completed', 'partial', 'failed', 'skipped')",
            name="state_valid",
        ),
        UniqueConstraint("run_id", name="uq_execution_attempts_run_id"),
        Index("ix_execution_attempts_state_started", "state", "started_at"),
        Index(
            "ix_execution_attempts_requested_query_provider_started",
            "requested_query_id", "requested_provider", "started_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    requested_query_id: Mapped[str] = mapped_column(String(36), nullable=False)
    requested_provider: Mapped[str] = mapped_column(String(100), nullable=False)
    request_reference: Mapped[str | None] = mapped_column(String(100))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    run_id: Mapped[str | None] = mapped_column(String(36))


class QuerySchedule(Base):
    __tablename__ = "query_schedules"
    __table_args__ = (
        ForeignKeyConstraint(
            ["query_id"], ["saved_queries.id"],
            name="fk_query_schedules_query_id_saved_queries", ondelete="RESTRICT",
        ),
        UniqueConstraint("query_id", name="uq_query_schedules_query_id"),
        UniqueConstraint("id", "query_id", name="uq_query_schedules_id_query_id"),
        CheckConstraint("interval_minutes >= 1", name="interval_minutes_positive"),
        Index("ix_query_schedules_due", "enabled", "next_due_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    query_id: Mapped[str] = mapped_column(String(36), nullable=False)
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    next_due_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class MonitoringCoverage(Base):
    """Frozen campaign context with transactional progress; old campaigns remain."""
    __tablename__ = "monitoring_coverage"
    __table_args__ = (
        ForeignKeyConstraint(["query_id"], ["saved_queries.id"],
            name="fk_monitoring_coverage_query_id_saved_queries", ondelete="RESTRICT"),
        ForeignKeyConstraint(["revision_id", "query_id"],
            ["saved_query_revisions.id", "saved_query_revisions.query_id"],
            name="fk_monitoring_coverage_revision_query_saved_query_revisions", ondelete="RESTRICT"),
        CheckConstraint("lane IN ('full', 'recent')", name="lane_valid"),
        CheckConstraint("pages_completed >= 0", name="pages_completed_nonnegative"),
        UniqueConstraint("id", "query_id", "provider", name="uq_monitoring_coverage_id_query_provider"),
        Index("ix_monitoring_coverage_lookup", "query_id", "provider", "lane", "created_at"),
        Index("uq_monitoring_coverage_active", "query_id", "provider", "lane", unique=True,
              sqlite_where=text("is_active = true"), postgresql_where=text("is_active = true")),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    query_id: Mapped[str] = mapped_column(String(36), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    lane: Mapped[str] = mapped_column(String(20), nullable=False)
    revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    signature: Mapped[str] = mapped_column(String(64), nullable=False)
    cursor: Mapped[str | None] = mapped_column(Text)
    complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    published_from: Mapped[date | None] = mapped_column(Date)
    published_to: Mapped[date | None] = mapped_column(Date)
    last_completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    pages_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    seen_cursors: Mapped[list[str | None]] = mapped_column(JSON, nullable=False, default=list, server_default=text("'[]'"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class ScheduledExecution(Base):
    __tablename__ = "scheduled_executions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["schedule_id", "query_id"], ["query_schedules.id", "query_schedules.query_id"],
            name="fk_scheduled_executions_schedule_query_query_schedules", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["query_id"], ["saved_queries.id"],
            name="fk_scheduled_executions_query_id_saved_queries", ondelete="RESTRICT",
        ),
        CheckConstraint(
            "state IN ('running', 'completed', 'partial', 'failed')", name="state_valid"
        ),
        Index("ix_scheduled_executions_query_started", "query_id", "started_at"),
        UniqueConstraint("id", "query_id", name="uq_scheduled_executions_id_query_id"),
        Index(
            "uq_scheduled_executions_running_query", "query_id", unique=True,
            sqlite_where=text("state = 'running'"),
            postgresql_where=text("state = 'running'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_id: Mapped[str] = mapped_column(String(36), nullable=False)
    query_id: Mapped[str] = mapped_column(String(36), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error_code: Mapped[str | None] = mapped_column(String(100))


class Journal(Base):
    __tablename__ = "journals"
    __table_args__ = (Index("ix_journals_nlm_id", "nlm_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    issn_print: Mapped[str | None] = mapped_column(String(16))
    issn_electronic: Mapped[str | None] = mapped_column(String(16))
    nlm_id: Mapped[str | None] = mapped_column(String(32))


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["journal_id"], ["journals.id"],
            name="fk_articles_journal_id_journals", ondelete="SET NULL",
        ),
        CheckConstraint(
            "publication_year IS NULL OR publication_year BETWEEN 1 AND 9999",
            name="publication_year_range",
        ),
        CheckConstraint(
            "publication_month IS NULL OR publication_month BETWEEN 1 AND 12",
            name="publication_month_range",
        ),
        CheckConstraint(
            "publication_day IS NULL OR publication_day BETWEEN 1 AND 31",
            name="publication_day_range",
        ),
        CheckConstraint(
            "publication_month IS NULL OR publication_year IS NOT NULL",
            name="publication_month_requires_year",
        ),
        CheckConstraint(
            "publication_day IS NULL OR publication_month IS NOT NULL",
            name="publication_day_requires_month",
        ),
        Index("ix_articles_publication_year", "publication_year"),
        Index("ix_articles_journal_id", "journal_id"),
        Index("ix_articles_possible_duplicate_key", "possible_duplicate_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    abstract: Mapped[str | None] = mapped_column(Text)
    journal_id: Mapped[str | None] = mapped_column(String(36))
    publication_type: Mapped[str | None] = mapped_column(String(100))
    publication_status: Mapped[str | None] = mapped_column(String(100))
    possible_duplicate_key: Mapped[str | None] = mapped_column(String(64))
    url: Mapped[str | None] = mapped_column(Text)
    publication_types: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, server_default=text("'[]'")
    )
    canonical_metadata_provenance: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    publication_year: Mapped[int | None] = mapped_column(Integer)
    publication_month: Mapped[int | None] = mapped_column(Integer)
    publication_day: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )


class ArticleIdentifier(Base):
    __tablename__ = "article_identifiers"
    __table_args__ = (
        ForeignKeyConstraint(
            ["article_id"], ["articles.id"],
            name="fk_article_identifiers_article_id_articles", ondelete="CASCADE",
        ),
        UniqueConstraint("scheme", "value", name="uq_article_identifiers_scheme_value"),
        Index("ix_article_identifiers_article_id", "article_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    article_id: Mapped[str] = mapped_column(String(36), nullable=False)
    scheme: Mapped[str] = mapped_column(String(160), nullable=False)
    value: Mapped[str] = mapped_column(String(255), nullable=False)


class SourceSnapshot(Base):
    __tablename__ = "source_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["search_run_id", "provider"], ["search_runs.id", "search_runs.provider"],
            name="fk_source_snapshots_search_run_provider_search_runs", ondelete="RESTRICT",
        ),
        CheckConstraint("byte_size >= 0", name="byte_size_nonnegative"),
        Index("ix_source_snapshots_provider_captured", "provider", "captured_at"),
        Index("ix_source_snapshots_search_run_id", "search_run_id"),
        Index("ix_source_snapshots_sha256", "sha256"),
        UniqueConstraint("id", "provider", name="uq_source_snapshots_id_provider"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    search_run_id: Mapped[str | None] = mapped_column(String(36))
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(
        String(100), nullable=False, default="unknown", server_default=text("'unknown'")
    )
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    compression: Mapped[str | None] = mapped_column(String(40))
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    request_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class SourceRecord(Base):
    __tablename__ = "source_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["article_id"], ["articles.id"],
            name="fk_source_records_article_id_articles", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "provider"], ["source_snapshots.id", "source_snapshots.provider"],
            name="fk_source_records_snapshot_provider_source_snapshots", ondelete="RESTRICT",
        ),
        UniqueConstraint("provider", "provider_record_id", name="uq_source_records_provider_record_id"),
        UniqueConstraint("id", "provider", name="uq_source_records_id_provider"),
        Index("ix_source_records_article_id", "article_id"),
        Index("ix_source_records_snapshot_id", "snapshot_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_record_id: Mapped[str] = mapped_column(String(255), nullable=False)
    article_id: Mapped[str] = mapped_column(String(36), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    identifiers: Mapped[list[dict[str, str]]] = mapped_column(
        JSON, nullable=False, default=list, server_default=text("'[]'")
    )
    source_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    parser_version: Mapped[str] = mapped_column(String(100), nullable=False)
    rights_note: Mapped[str | None] = mapped_column(Text)
    source_database: Mapped[str | None] = mapped_column(String(100))
    url: Mapped[str | None] = mapped_column(Text)
    normalized_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )


class SourceRecordSnapshot(Base):
    """Immutable association history between provider records and raw snapshots."""

    __tablename__ = "source_record_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_record_id", "provider"], ["source_records.id", "source_records.provider"],
            name="fk_source_record_snapshots_record_provider_source_records", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "provider"], ["source_snapshots.id", "source_snapshots.provider"],
            name="fk_source_record_snapshots_snapshot_provider_source_snapshots", ondelete="RESTRICT",
        ),
        Index("ix_source_record_snapshots_snapshot_provider", "snapshot_id", "provider"),
    )

    source_record_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    normalized_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )


class SearchRunObservation(Base):
    """Classification and provenance for a source record observed in one run."""

    __tablename__ = "search_run_observations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "provider"], ["search_runs.id", "search_runs.provider"],
            name="fk_search_run_observations_run_provider_search_runs", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_record_id", "provider"], ["source_records.id", "source_records.provider"],
            name="fk_search_run_observations_source_provider_source_records", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "provider"], ["source_snapshots.id", "source_snapshots.provider"],
            name="fk_search_run_observations_snapshot_provider_source_snapshots", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["article_id"], ["articles.id"],
            name="fk_search_run_observations_article_id_articles", ondelete="RESTRICT",
        ),
        UniqueConstraint("run_id", "source_record_id", name="uq_search_run_observations_run_source"),
        Index("ix_search_run_observations_article_observed", "article_id", "observed_at"),
        Index("ix_search_run_observations_snapshot_provider", "snapshot_id", "provider"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(36), nullable=False)
    article_id: Mapped[str] = mapped_column(String(36), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    newly_discovered: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    new_to_query: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    newly_published: Mapped[bool | None] = mapped_column(Boolean)
    metadata_updated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class IdentityConflict(Base):
    """An unresolved exact-identifier match that points at multiple articles."""

    __tablename__ = "identity_conflicts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "provider"], ["search_runs.id", "search_runs.provider"],
            name="fk_identity_conflicts_run_provider_search_runs", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "provider"], ["source_snapshots.id", "source_snapshots.provider"],
            name="fk_identity_conflicts_snapshot_provider_source_snapshots", ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('unresolved', 'resolved', 'dismissed')", name="status_valid"
        ),
        Index("ix_identity_conflicts_run_id", "run_id"),
        Index("ix_identity_conflicts_provider_source", "provider", "source_id"),
        Index("ix_identity_conflicts_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    identifiers: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    candidate_article_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="unresolved", server_default=text("'unresolved'")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class QueryMatch(Base):
    __tablename__ = "query_matches"
    __table_args__ = (
        ForeignKeyConstraint(
            ["query_id"], ["saved_queries.id"],
            name="fk_query_matches_query_id_saved_queries", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["article_id"], ["articles.id"],
            name="fk_query_matches_article_id_articles", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["first_run_id", "query_id"], ["search_runs.id", "search_runs.query_id"],
            name="fk_query_matches_first_run_query_search_runs", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["last_run_id", "query_id"], ["search_runs.id", "search_runs.query_id"],
            name="fk_query_matches_last_run_query_search_runs", ondelete="RESTRICT",
        ),
        Index("ix_query_matches_article_id", "article_id"),
        Index("ix_query_matches_first_seen", "first_seen_at"),
    )

    query_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    article_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    first_run_id: Mapped[str | None] = mapped_column(String(36))
    last_run_id: Mapped[str | None] = mapped_column(String(36))


class ProjectArticle(Base):
    __tablename__ = "project_articles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"], ["research_projects.id"],
            name="fk_project_articles_project_id_research_projects", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["article_id"], ["articles.id"],
            name="fk_project_articles_article_id_articles", ondelete="CASCADE",
        ),
        CheckConstraint(
            "review_decision IN ('none', 'important', 'excluded')", name="review_decision_valid"
        ),
        Index("ix_project_articles_review_decision", "project_id", "review_decision", "last_seen_at"),
    )

    project_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    article_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    review_decision: Mapped[str] = mapped_column(
        String(20), nullable=False, default="none", server_default=text("'none'")
    )
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    status_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"], ["research_projects.id"],
            name="fk_tags_project_id_research_projects", ondelete="CASCADE",
        ),
        UniqueConstraint("project_id", "normalized_name", name="uq_tags_project_normalized_name"),
        UniqueConstraint("project_id", "id", name="uq_tags_project_id_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)


class ProjectArticleTag(Base):
    __tablename__ = "project_article_tags"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "article_id"], ["project_articles.project_id", "project_articles.article_id"],
            name="fk_project_article_tags_project_article_project_articles", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "tag_id"], ["tags.project_id", "tags.id"],
            name="fk_project_article_tags_project_tag_tags", ondelete="CASCADE",
        ),
        Index("ix_project_article_tags_tag_id", "project_id", "tag_id"),
    )

    project_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    article_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tag_id: Mapped[str] = mapped_column(String(36), primary_key=True)


class Author(Base):
    __tablename__ = "authors"
    __table_args__ = (Index("ix_authors_orcid", "orcid"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    display_name: Mapped[str] = mapped_column(String(500), nullable=False)
    orcid: Mapped[str | None] = mapped_column(String(19))


class ArticleAuthor(Base):
    __tablename__ = "article_authors"
    __table_args__ = (
        ForeignKeyConstraint(
            ["article_id"], ["articles.id"],
            name="fk_article_authors_article_id_articles", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["author_id"], ["authors.id"],
            name="fk_article_authors_author_id_authors", ondelete="SET NULL",
        ),
        CheckConstraint("position >= 0", name="position_nonnegative"),
        Index("ix_article_authors_author_id", "author_id"),
    )

    article_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    author_id: Mapped[str | None] = mapped_column(String(36))
    display_name: Mapped[str] = mapped_column(String(500), nullable=False)


class Topic(Base):
    __tablename__ = "topics"
    __table_args__ = (
        UniqueConstraint("vocabulary", "normalized_key", name="uq_topics_vocabulary_normalized_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    vocabulary: Mapped[str] = mapped_column(String(100), nullable=False)
    normalized_key: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[str] = mapped_column(String(500), nullable=False)


class ArticleTopic(Base):
    __tablename__ = "article_topics"
    __table_args__ = (
        ForeignKeyConstraint(
            ["article_id"], ["articles.id"],
            name="fk_article_topics_article_id_articles", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["topic_id"], ["topics.id"],
            name="fk_article_topics_topic_id_topics", ondelete="CASCADE",
        ),
        Index("ix_article_topics_topic_source", "topic_id", "source"),
    )

    article_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    topic_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source: Mapped[str] = mapped_column(String(100), primary_key=True)
    assignment_method: Mapped[str] = mapped_column(String(100), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class ArticleRelation(Base):
    __tablename__ = "article_relations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_article_id"], ["articles.id"],
            name="fk_article_relations_source_article_id_articles", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["target_article_id"], ["articles.id"],
            name="fk_article_relations_target_article_id_articles", ondelete="CASCADE",
        ),
        UniqueConstraint(
            "source_article_id", "target_article_id", "relation_type",
            name="uq_article_relations_source_target_type",
        ),
        Index("ix_article_relations_target_article_id", "target_article_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_article_id: Mapped[str] = mapped_column(String(36), nullable=False)
    target_article_id: Mapped[str] = mapped_column(String(36), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(100), nullable=False)
    source_provider: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class CitationObservation(Base):
    __tablename__ = "citation_observations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["article_id"], ["articles.id"],
            name="fk_citation_observations_article_id_articles", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "provider"], ["source_snapshots.id", "source_snapshots.provider"],
            name="fk_citation_observations_snapshot_provider_source_snapshots",
            ondelete="RESTRICT",
        ),
        CheckConstraint("count >= 0", name="count_nonnegative"),
        UniqueConstraint(
            "article_id", "snapshot_id", name="uq_citation_observations_article_snapshot"
        ),
        Index("ix_citation_observations_article_as_of", "article_id", "as_of"),
        Index("ix_citation_observations_snapshot_provider", "snapshot_id", "provider"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    article_id: Mapped[str] = mapped_column(String(36), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    as_of: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    snapshot_id: Mapped[str | None] = mapped_column(String(36))


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        Index("ix_outbox_events_pending", "processed_at", "occurred_at"),
        Index("ix_outbox_events_aggregate", "aggregate_type", "aggregate_id", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    type: Mapped[str] = mapped_column(String(150), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))


# Register additive review mappings for migration metadata and repository access.
from .identity_models import IdentityReviewDecision, PossibleDuplicateCandidate  # noqa: E402,F401
