"""Add durable query schedules and execution attempts.

Revision ID: 0004_monitoring
Revises: 0003_saved_query_revisions
Create Date: 2026-09-25
"""

from typing import Sequence, Union
from functools import wraps

from alembic import op
import sqlalchemy as sa


revision: str = "0004_monitoring"
down_revision: Union[str, None] = "0003_saved_query_revisions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _sqlite_batch_foreign_keys(operation):
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
                raise RuntimeError(f"foreign-key violations after monitoring migration: {violations!r}")
    return wrapped


@_sqlite_batch_foreign_keys
def upgrade() -> None:
    op.create_table(
        "query_schedules",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("query_id", sa.String(length=36), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("interval_minutes >= 1", name="ck_query_schedules_interval_minutes_positive"),
        sa.ForeignKeyConstraint(
            ["query_id"], ["saved_queries.id"],
            name="fk_query_schedules_query_id_saved_queries", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_query_schedules"),
        sa.UniqueConstraint("query_id", name="uq_query_schedules_query_id"),
        sa.UniqueConstraint("id", "query_id", name="uq_query_schedules_id_query_id"),
    )
    op.create_index("ix_query_schedules_due", "query_schedules", ["enabled", "next_due_at"])
    op.create_table(
        "scheduled_executions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("schedule_id", sa.String(length=36), nullable=False),
        sa.Column("query_id", sa.String(length=36), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.CheckConstraint(
            "state IN ('running', 'completed', 'failed')",
            name="ck_scheduled_executions_state_valid",
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id", "query_id"], ["query_schedules.id", "query_schedules.query_id"],
            name="fk_scheduled_executions_schedule_query_query_schedules", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["query_id"], ["saved_queries.id"],
            name="fk_scheduled_executions_query_id_saved_queries", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_scheduled_executions"),
        sa.UniqueConstraint("id", "query_id", name="uq_scheduled_executions_id_query_id"),
    )
    op.create_index(
        "ix_scheduled_executions_query_started", "scheduled_executions",
        ["query_id", "started_at"],
    )
    op.create_index(
        "uq_scheduled_executions_running_query", "scheduled_executions",
        ["query_id"], unique=True,
        sqlite_where=sa.text("state = 'running'"),
        postgresql_where=sa.text("state = 'running'"),
    )
    op.add_column("search_runs", sa.Column("scheduled_execution_id", sa.String(length=36), nullable=True))
    with op.batch_alter_table("search_runs") as batch_op:
        batch_op.create_foreign_key(
            "fk_search_runs_scheduled_execution_query_scheduled_executions",
            "scheduled_executions", ["scheduled_execution_id", "query_id"],
            ["id", "query_id"], ondelete="RESTRICT",
        )
    op.create_index("ix_search_runs_scheduled_execution_id", "search_runs", ["scheduled_execution_id"])


@_sqlite_batch_foreign_keys
def downgrade() -> None:
    op.drop_index("ix_search_runs_scheduled_execution_id", table_name="search_runs")
    with op.batch_alter_table("search_runs") as batch_op:
        batch_op.drop_constraint(
            "fk_search_runs_scheduled_execution_query_scheduled_executions", type_="foreignkey"
        )
        batch_op.drop_column("scheduled_execution_id")
    op.drop_index("uq_scheduled_executions_running_query", table_name="scheduled_executions")
    op.drop_index("ix_scheduled_executions_query_started", table_name="scheduled_executions")
    op.drop_table("scheduled_executions")
    op.drop_index("ix_query_schedules_due", table_name="query_schedules")
    op.drop_table("query_schedules")
