"""Evidence-bound identity decisions and non-merging duplicate candidates."""

from alembic import op
import sqlalchemy as sa

revision = "0009_identity_review"
down_revision = "0008_metadata_completeness"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("articles", sa.Column("possible_duplicate_key", sa.String(64), nullable=True))
    op.create_index("ix_articles_possible_duplicate_key", "articles", ["possible_duplicate_key"])
    op.create_table(
        "possible_duplicate_candidates",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("left_article_id", sa.String(36), nullable=False),
        sa.Column("right_article_id", sa.String(36), nullable=False),
        sa.Column("snapshot_id", sa.String(36), nullable=False),
        sa.Column("reason", sa.String(100), nullable=False),
        sa.Column("status", sa.String(40), server_default=sa.text("'unresolved'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_possible_duplicate_candidates")),
        sa.ForeignKeyConstraint(["left_article_id"], ["articles.id"], name=op.f("fk_possible_duplicate_candidates_left_article_id_articles"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["right_article_id"], ["articles.id"], name=op.f("fk_possible_duplicate_candidates_right_article_id_articles"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["source_snapshots.id"], name=op.f("fk_possible_duplicate_candidates_snapshot_id_source_snapshots"), ondelete="RESTRICT"),
        sa.UniqueConstraint("left_article_id", "right_article_id", name="uq_possible_duplicate_pair"),
        sa.CheckConstraint("left_article_id < right_article_id", name=op.f("ck_possible_duplicate_candidates_ordered_pair")),
        sa.CheckConstraint("status IN ('unresolved', 'kept_separate')", name=op.f("ck_possible_duplicate_candidates_status_valid")),
    )
    op.create_index("ix_possible_duplicate_status_created", "possible_duplicate_candidates", ["status", "created_at"])
    op.create_table(
        "identity_review_decisions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("conflict_id", sa.String(36)),
        sa.Column("candidate_id", sa.String(36)),
        sa.Column("selected_article_id", sa.String(36)),
        sa.Column("snapshot_id", sa.String(36), nullable=False),
        sa.Column("provider", sa.String(100)),
        sa.Column("source_id", sa.String(255)),
        sa.Column("assertion_signature", sa.String(64)),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("rejected_identifiers", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_identity_review_decisions")),
        sa.ForeignKeyConstraint(["conflict_id"], ["identity_conflicts.id"], name=op.f("fk_identity_review_decisions_conflict_id_identity_conflicts"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["candidate_id"], ["possible_duplicate_candidates.id"], name=op.f("fk_identity_review_decisions_candidate_id_possible_duplicate_candidates"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["selected_article_id"], ["articles.id"], name=op.f("fk_identity_review_decisions_selected_article_id_articles"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["source_snapshots.id"], name=op.f("fk_identity_review_decisions_snapshot_id_source_snapshots"), ondelete="RESTRICT"),
        sa.UniqueConstraint("conflict_id", name="uq_identity_review_conflict"),
        sa.UniqueConstraint("candidate_id", name="uq_identity_review_candidate"),
        sa.CheckConstraint("(conflict_id IS NULL) <> (candidate_id IS NULL)", name=op.f("ck_identity_review_decisions_one_issue")),
        sa.CheckConstraint("action IN ('keep_separate', 'reject_source', 'select_article')", name=op.f("ck_identity_review_decisions_action_valid")),
        sa.CheckConstraint("(action = 'select_article' AND selected_article_id IS NOT NULL) OR (action <> 'select_article' AND selected_article_id IS NULL)", name=op.f("ck_identity_review_decisions_selection_required")),
        sa.CheckConstraint("candidate_id IS NULL OR action = 'keep_separate'", name=op.f("ck_identity_review_decisions_candidate_action")),
        sa.CheckConstraint("conflict_id IS NULL OR (provider IS NOT NULL AND source_id IS NOT NULL AND assertion_signature IS NOT NULL)", name=op.f("ck_identity_review_decisions_conflict_evidence")),
    )
    op.create_index("ix_identity_review_source_signature", "identity_review_decisions",
                    ["provider", "source_id", "assertion_signature", "reviewed_at"])


def downgrade():
    op.drop_table("identity_review_decisions")
    op.drop_table("possible_duplicate_candidates")
    op.drop_index("ix_articles_possible_duplicate_key", table_name="articles")
    op.drop_column("articles", "possible_duplicate_key")
