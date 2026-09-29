"""Track immutable saved-query revisions and pin search runs to them.

Revision ID: 0003_saved_query_revisions
Revises: 0002_stage3_audit
Create Date: 2026-09-25
"""

from functools import wraps
from typing import Callable, Sequence, Union
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "0003_saved_query_revisions"
down_revision: Union[str, None] = "0002_stage3_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _with_sqlite_fk_checks_disabled(operation: Callable[[], None]) -> Callable[[], None]:
    """Allow SQLite batch table swaps while restoring FK enforcement afterward."""

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
                    f"foreign-key violations after Stage 4 schema change: {violations!r}"
                )

    return wrapped


def _backfill_initial_revisions() -> None:
    """Copy each pre-revision query into revision 1 without changing its intent."""

    bind = op.get_bind()
    query = sa.table(
        "saved_queries",
        sa.column("id", sa.String(length=36)),
        sa.column("expression", sa.Text()),
        sa.column("filters", sa.JSON()),
        sa.column("enabled_providers", sa.JSON()),
        sa.column("is_active", sa.Boolean()),
        sa.column("archived_at", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    revision_table = sa.table(
        "saved_query_revisions",
        sa.column("id", sa.String(length=36)),
        sa.column("query_id", sa.String(length=36)),
        sa.column("revision_number", sa.Integer()),
        sa.column("expression", sa.Text()),
        sa.column("filters", sa.JSON()),
        sa.column("enabled_providers", sa.JSON()),
        sa.column("is_active", sa.Boolean()),
        sa.column("archived_at", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    legacy_queries = bind.execute(
        sa.select(
            query.c.id,
            query.c.expression,
            query.c.filters,
            query.c.enabled_providers,
            query.c.is_active,
            query.c.archived_at,
            query.c.created_at,
        )
    ).mappings()
    revision_rows = [
        {
            "id": str(uuid4()),
            "query_id": saved_query["id"],
            "revision_number": 1,
            "expression": saved_query["expression"],
            "filters": saved_query["filters"],
            "enabled_providers": saved_query["enabled_providers"],
            "is_active": saved_query["is_active"],
            "archived_at": saved_query["archived_at"],
            "created_at": saved_query["created_at"],
        }
        for saved_query in legacy_queries
    ]
    if revision_rows:
        bind.execute(sa.insert(revision_table), revision_rows)


@_with_sqlite_fk_checks_disabled
def upgrade() -> None:
    # Existing queries become revision 1. The server default keeps old insert
    # callers valid while project/query application services adopt revisions.
    with op.batch_alter_table("saved_queries") as batch_op:
        batch_op.add_column(
            sa.Column(
                "revision_number",
                sa.Integer(),
                server_default=sa.text("1"),
                nullable=False,
            )
        )

    op.create_table(
        "saved_query_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("query_id", sa.String(length=36), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("expression", sa.Text(), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("enabled_providers", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "revision_number >= 1",
            name="ck_saved_query_revisions_revision_number_positive",
        ),
        sa.ForeignKeyConstraint(
            ["query_id"], ["saved_queries.id"],
            name="fk_saved_query_revisions_query_id_saved_queries", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_saved_query_revisions"),
        sa.UniqueConstraint(
            "query_id", "revision_number",
            name="uq_saved_query_revisions_query_revision",
        ),
        sa.UniqueConstraint(
            "id", "query_id", name="uq_saved_query_revisions_id_query_id"
        ),
    )
    _backfill_initial_revisions()

    op.add_column(
        "search_runs", sa.Column("query_revision_id", sa.String(length=36), nullable=True)
    )
    with op.batch_alter_table("search_runs") as batch_op:
        batch_op.create_foreign_key(
            "fk_search_runs_query_revision_query_saved_query_revisions",
            "saved_query_revisions",
            ["query_revision_id", "query_id"],
            ["id", "query_id"],
            ondelete="RESTRICT",
        )
    op.create_index("ix_search_runs_query_revision_id", "search_runs", ["query_revision_id"])


@_with_sqlite_fk_checks_disabled
def downgrade() -> None:
    op.drop_index("ix_search_runs_query_revision_id", table_name="search_runs")
    with op.batch_alter_table("search_runs") as batch_op:
        batch_op.drop_constraint(
            "fk_search_runs_query_revision_query_saved_query_revisions", type_="foreignkey"
        )
        batch_op.drop_column("query_revision_id")

    op.drop_table("saved_query_revisions")
    with op.batch_alter_table("saved_queries") as batch_op:
        batch_op.drop_column("revision_number")
