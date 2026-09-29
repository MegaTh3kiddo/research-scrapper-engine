"""Record every requested provider execution before a SearchRun exists.

Revision ID: 0007_execution_audit
Revises: 0006_monitoring_coverage
Create Date: 2026-09-26
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_execution_audit"
down_revision: Union[str, None] = "0006_monitoring_coverage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "execution_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("requested_query_id", sa.String(length=36), nullable=False),
        sa.Column("requested_provider", sa.String(length=100), nullable=False),
        sa.Column("request_reference", sa.String(length=100), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.CheckConstraint(
            "state IN ('running', 'completed', 'partial', 'failed', 'skipped')",
            name="ck_execution_attempts_state_valid",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["search_runs.id"],
            name="fk_execution_attempts_run_id_search_runs", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_execution_attempts"),
        sa.UniqueConstraint("run_id", name="uq_execution_attempts_run_id"),
    )
    op.create_index(
        "ix_execution_attempts_state_started",
        "execution_attempts", ["state", "started_at"],
    )
    op.create_index(
        "ix_execution_attempts_requested_query_provider_started",
        "execution_attempts",
        ["requested_query_id", "requested_provider", "started_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_execution_attempts_requested_query_provider_started",
        table_name="execution_attempts",
    )
    op.drop_index("ix_execution_attempts_state_started", table_name="execution_attempts")
    op.drop_table("execution_attempts")
