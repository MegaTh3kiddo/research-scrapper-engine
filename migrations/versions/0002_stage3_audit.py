"""Add Stage 3 snapshot, observation, and identity audit schema.

Revision ID: 0002_stage3_audit
Revises: 0001_initial_schema
Create Date: 2026-09-25
"""

from functools import wraps
from typing import Callable, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_stage3_audit"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _with_sqlite_fk_checks_disabled(operation: Callable[[], None]) -> Callable[[], None]:
    """Allow SQLite batch table swaps, restoring checks even after a failure."""

    @wraps(operation)
    def wrapped() -> None:
        bind = op.get_bind()
        sqlite_fk_checks_were_enabled = False
        if bind.dialect.name == "sqlite":
            sqlite_fk_checks_were_enabled = bool(
                bind.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
            )
            if sqlite_fk_checks_were_enabled:
                bind.exec_driver_sql("PRAGMA foreign_keys=OFF")

        try:
            operation()
        finally:
            if sqlite_fk_checks_were_enabled:
                bind.exec_driver_sql("PRAGMA foreign_keys=ON")

        if sqlite_fk_checks_were_enabled:
            violations = bind.exec_driver_sql("PRAGMA foreign_key_check").all()
            if violations:
                raise RuntimeError(
                    f"foreign-key violations after Stage 3 schema change: {violations!r}"
                )

    return wrapped


@_with_sqlite_fk_checks_disabled
def upgrade() -> None:
    # Provider names are part of the identifier scheme token, so leave room for
    # future providers beyond the original short DOI/PMID/PMCID schemes.
    with op.batch_alter_table("article_identifiers") as batch_op:
        batch_op.alter_column(
            "scheme",
            existing_type=sa.String(length=40),
            type_=sa.String(length=160),
            existing_nullable=False,
        )

    # Existing snapshots predate parser version tracking. Preserve them with an
    # honest sentinel rather than assigning the current parser retroactively.
    op.add_column(
        "source_snapshots",
        sa.Column(
            "parser_version",
            sa.String(length=100),
            server_default=sa.text("'unknown'"),
            nullable=False,
        ),
    )

    op.add_column(
        "source_records",
        sa.Column(
            "identifiers",
            sa.JSON(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
    )

    op.add_column(
        "search_runs",
        sa.Column(
            "recent_window_days", sa.Integer(), server_default=sa.text("30"), nullable=False
        ),
    )
    op.add_column(
        "search_runs",
        sa.Column("updated_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "search_runs",
        sa.Column(
            "newly_published_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
    )
    op.add_column(
        "search_runs",
        sa.Column("conflict_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    with op.batch_alter_table("search_runs") as batch_op:
        batch_op.create_check_constraint(
            "ck_search_runs_recent_window_days_nonnegative", "recent_window_days >= 0"
        )
        batch_op.create_check_constraint(
            "ck_search_runs_updated_count_nonnegative", "updated_count >= 0"
        )
        batch_op.create_check_constraint(
            "ck_search_runs_newly_published_count_nonnegative",
            "newly_published_count >= 0",
        )
        batch_op.create_check_constraint(
            "ck_search_runs_conflict_count_nonnegative", "conflict_count >= 0"
        )

    op.create_table(
        "search_run_observations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("source_record_id", sa.String(length=36), nullable=False),
        sa.Column("article_id", sa.String(length=36), nullable=False),
        sa.Column("snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("newly_discovered", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("new_to_query", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("newly_published", sa.Boolean(), nullable=True),
        sa.Column("metadata_updated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.ForeignKeyConstraint(
            ["article_id"], ["articles.id"],
            name="fk_search_run_observations_article_id_articles", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "provider"], ["search_runs.id", "search_runs.provider"],
            name="fk_search_run_observations_run_provider_search_runs", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "provider"], ["source_snapshots.id", "source_snapshots.provider"],
            name="fk_search_run_observations_snapshot_provider_source_snapshots", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_record_id", "provider"], ["source_records.id", "source_records.provider"],
            name="fk_search_run_observations_source_provider_source_records", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_search_run_observations"),
        sa.UniqueConstraint(
            "run_id", "source_record_id", name="uq_search_run_observations_run_source"
        ),
    )
    op.create_index(
        "ix_search_run_observations_article_observed",
        "search_run_observations",
        ["article_id", "observed_at"],
    )
    op.create_index(
        "ix_search_run_observations_snapshot_provider",
        "search_run_observations",
        ["snapshot_id", "provider"],
    )

    op.create_table(
        "identity_conflicts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("source_id", sa.String(length=255), nullable=False),
        sa.Column("snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("identifiers", sa.JSON(), nullable=False),
        sa.Column("candidate_article_ids", sa.JSON(), nullable=False),
        sa.Column(
            "status", sa.String(length=40), server_default=sa.text("'unresolved'"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('unresolved', 'resolved', 'dismissed')",
            name="ck_identity_conflicts_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "provider"], ["search_runs.id", "search_runs.provider"],
            name="fk_identity_conflicts_run_provider_search_runs", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "provider"], ["source_snapshots.id", "source_snapshots.provider"],
            name="fk_identity_conflicts_snapshot_provider_source_snapshots", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_identity_conflicts"),
    )
    op.create_index("ix_identity_conflicts_run_id", "identity_conflicts", ["run_id"])
    op.create_index(
        "ix_identity_conflicts_provider_source", "identity_conflicts", ["provider", "source_id"]
    )
    op.create_index(
        "ix_identity_conflicts_status_created", "identity_conflicts", ["status", "created_at"]
    )

@_with_sqlite_fk_checks_disabled
def downgrade() -> None:
    op.drop_index("ix_identity_conflicts_status_created", table_name="identity_conflicts")
    op.drop_index("ix_identity_conflicts_provider_source", table_name="identity_conflicts")
    op.drop_index("ix_identity_conflicts_run_id", table_name="identity_conflicts")
    op.drop_table("identity_conflicts")
    op.drop_index(
        "ix_search_run_observations_snapshot_provider", table_name="search_run_observations"
    )
    op.drop_index(
        "ix_search_run_observations_article_observed", table_name="search_run_observations"
    )
    op.drop_table("search_run_observations")
    with op.batch_alter_table("article_identifiers") as batch_op:
        batch_op.alter_column(
            "scheme",
            existing_type=sa.String(length=160),
            type_=sa.String(length=40),
            existing_nullable=False,
        )
    with op.batch_alter_table("search_runs") as batch_op:
        batch_op.drop_constraint("ck_search_runs_conflict_count_nonnegative", type_="check")
        batch_op.drop_constraint(
            "ck_search_runs_newly_published_count_nonnegative", type_="check"
        )
        batch_op.drop_constraint("ck_search_runs_updated_count_nonnegative", type_="check")
        batch_op.drop_constraint("ck_search_runs_recent_window_days_nonnegative", type_="check")
        batch_op.drop_column("conflict_count")
        batch_op.drop_column("newly_published_count")
        batch_op.drop_column("updated_count")
        batch_op.drop_column("recent_window_days")
    with op.batch_alter_table("source_snapshots") as batch_op:
        batch_op.drop_column("parser_version")
    with op.batch_alter_table("source_records") as batch_op:
        batch_op.drop_column("identifiers")
