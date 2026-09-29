"""Optional per-project Google Sheet link and sync status."""

from alembic import op
import sqlalchemy as sa

revision = "0010_google_sheet_links"
down_revision = "0009_identity_review"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "google_sheet_links",
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("spreadsheet_id", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_synced_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error_code", sa.String(100)),
        sa.PrimaryKeyConstraint("project_id", name=op.f("pk_google_sheet_links")),
        sa.ForeignKeyConstraint(
            ["project_id"], ["research_projects.id"],
            name=op.f("fk_google_sheet_links_project_id_research_projects"),
            ondelete="RESTRICT",
        ),
    )


def downgrade():
    op.drop_table("google_sheet_links")
