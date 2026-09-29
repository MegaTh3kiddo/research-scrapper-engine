"""Track canonical bibliographic field selection without guessing old origins."""

from alembic import op
import sqlalchemy as sa

revision = "0005_metadata_provenance"
down_revision = "0004_monitoring"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("articles", sa.Column(
        "canonical_metadata_provenance", sa.JSON(), nullable=False,
        server_default=sa.text("'{}'"),
    ))


def downgrade() -> None:
    # SQLite >=3.35 supports DROP COLUMN without table rebuild; no FK targets change.
    op.drop_column("articles", "canonical_metadata_provenance")
