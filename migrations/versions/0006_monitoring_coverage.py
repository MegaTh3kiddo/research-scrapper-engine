"""Add resumable provider monitoring coverage and search-run checkpoints.

Revision ID: 0006_monitoring_coverage
Revises: 0005_metadata_provenance
Create Date: 2026-09-26
"""

from functools import wraps
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_monitoring_coverage"
down_revision: Union[str, None] = "0005_metadata_provenance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _with_sqlite_fk_checks_disabled(operation):
    """Permit SQLite batch table swaps and verify foreign keys afterward."""

    @wraps(operation)
    def wrapped() -> None:
        bind = op.get_bind()
        was_enabled = bind.dialect.name == "sqlite" and bool(
            bind.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
        )
        if was_enabled:
            bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            operation()
        finally:
            if was_enabled:
                bind.exec_driver_sql("PRAGMA foreign_keys=ON")
        if was_enabled:
            violations = bind.exec_driver_sql("PRAGMA foreign_key_check").all()
            if violations:
                raise RuntimeError(
                    f"foreign-key violations after monitoring coverage migration: {violations!r}"
                )

    return wrapped


@_with_sqlite_fk_checks_disabled
def upgrade() -> None:
    op.create_table(
        "monitoring_coverage",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("query_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("lane", sa.String(length=20), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("signature", sa.String(length=64), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column("seen_cursors", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("complete", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("published_from", sa.Date(), nullable=True),
        sa.Column("published_to", sa.Date(), nullable=True),
        sa.Column("last_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pages_completed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "lane IN ('full', 'recent')", name="ck_monitoring_coverage_lane_valid"
        ),
        sa.CheckConstraint(
            "pages_completed >= 0",
            name="ck_monitoring_coverage_pages_completed_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["query_id"], ["saved_queries.id"],
            name="fk_monitoring_coverage_query_id_saved_queries", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revision_id", "query_id"],
            ["saved_query_revisions.id", "saved_query_revisions.query_id"],
            name="fk_monitoring_coverage_revision_query_saved_query_revisions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_monitoring_coverage"),
        sa.UniqueConstraint(
            "id", "query_id", "provider",
            name="uq_monitoring_coverage_id_query_provider",
        ),
    )
    op.create_index(
        "ix_monitoring_coverage_lookup", "monitoring_coverage",
        ["query_id", "provider", "lane", "created_at"],
    )
    op.create_index(
        "uq_monitoring_coverage_active", "monitoring_coverage",
        ["query_id", "provider", "lane"], unique=True,
        sqlite_where=sa.text("is_active = true"),
        postgresql_where=sa.text("is_active = true"),
    )

    op.add_column("search_runs", sa.Column("coverage_id", sa.String(length=36), nullable=True))
    op.add_column("search_runs", sa.Column("start_cursor", sa.Text(), nullable=True))
    with op.batch_alter_table("search_runs") as batch_op:
        batch_op.create_foreign_key(
            "fk_search_runs_coverage_query_provider_monitoring_coverage",
            "monitoring_coverage",
            ["coverage_id", "query_id", "provider"],
            ["id", "query_id", "provider"],
            ondelete="RESTRICT",
        )
    op.create_index("ix_search_runs_coverage_id", "search_runs", ["coverage_id"])

    with op.batch_alter_table("scheduled_executions") as batch_op:
        batch_op.drop_constraint("ck_scheduled_executions_state_valid", type_="check")
        batch_op.create_check_constraint(
            "ck_scheduled_executions_state_valid",
            "state IN ('running', 'completed', 'partial', 'failed')",
        )


@_with_sqlite_fk_checks_disabled
def downgrade() -> None:
    # Preserve execution history when the newer partial state is no longer
    # representable. The explicit marker explains why the state was rewritten.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE scheduled_executions "
            "SET state = 'failed', error_code = 'CoverageDowngrade' "
            "WHERE state = 'partial'"
        )
    )
    with op.batch_alter_table("scheduled_executions") as batch_op:
        batch_op.drop_constraint("ck_scheduled_executions_state_valid", type_="check")
        batch_op.create_check_constraint(
            "ck_scheduled_executions_state_valid",
            "state IN ('running', 'completed', 'failed')",
        )

    op.drop_index("ix_search_runs_coverage_id", table_name="search_runs")
    with op.batch_alter_table("search_runs") as batch_op:
        batch_op.drop_constraint(
            "fk_search_runs_coverage_query_provider_monitoring_coverage",
            type_="foreignkey",
        )
        batch_op.drop_column("start_cursor")
        batch_op.drop_column("coverage_id")

    op.drop_index("uq_monitoring_coverage_active", table_name="monitoring_coverage")
    op.drop_index("ix_monitoring_coverage_lookup", table_name="monitoring_coverage")
    op.drop_table("monitoring_coverage")
