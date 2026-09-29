"""Preserve complete provider metadata and citation evidence.

Revision ID: 0008_metadata_completeness
Revises: 0007_execution_audit
Create Date: 2026-09-26
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_metadata_completeness"
down_revision: Union[str, None] = "0007_execution_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("url", sa.Text(), nullable=True))
    op.add_column(
        "articles",
        sa.Column(
            "publication_types", sa.JSON(), nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )

    op.add_column("source_records", sa.Column("source_database", sa.String(100)))
    op.add_column("source_records", sa.Column("url", sa.Text()))
    op.add_column(
        "source_records",
        sa.Column(
            "normalized_metadata", sa.JSON(), nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column(
        "source_record_snapshots",
        sa.Column(
            "normalized_metadata", sa.JSON(), nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )

    # SQLite requires a table rebuild to add a composite foreign key. Alembic's
    # automatic batch mode selects this only for SQLite and emits ordinary
    # ALTER TABLE operations on databases that support them.
    with op.batch_alter_table("citation_observations") as batch:
        batch.add_column(sa.Column("snapshot_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_citation_observations_snapshot_provider_source_snapshots",
            "source_snapshots",
            ["snapshot_id", "provider"],
            ["id", "provider"],
            ondelete="RESTRICT",
        )
        batch.create_unique_constraint(
            "uq_citation_observations_article_snapshot",
            ["article_id", "snapshot_id"],
        )
    op.create_index(
        "ix_citation_observations_snapshot_provider",
        "citation_observations", ["snapshot_id", "provider"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_citation_observations_snapshot_provider",
        table_name="citation_observations",
    )
    with op.batch_alter_table("citation_observations") as batch:
        batch.drop_constraint(
            "uq_citation_observations_article_snapshot", type_="unique"
        )
        batch.drop_constraint(
            "fk_citation_observations_snapshot_provider_source_snapshots",
            type_="foreignkey",
        )
        batch.drop_column("snapshot_id")

    op.drop_column("source_record_snapshots", "normalized_metadata")
    op.drop_column("source_records", "normalized_metadata")
    op.drop_column("source_records", "url")
    op.drop_column("source_records", "source_database")
    op.drop_column("articles", "publication_types")
    op.drop_column("articles", "url")
