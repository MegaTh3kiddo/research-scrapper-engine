"""Create the initial research engine schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-24
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "research_projects",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_research_projects"),
    )

    op.create_table(
        "saved_queries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("expression", sa.Text(), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("enabled_providers", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["research_projects.id"],
            name="fk_saved_queries_project_id_research_projects", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_saved_queries"),
    )
    op.create_index("ix_saved_queries_project_active", "saved_queries", ["project_id", "is_active"])

    op.create_table(
        "provider_query_plans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("query_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("translated_query", sa.Text(), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("unsupported_filters", sa.JSON(), nullable=False),
        sa.Column("translation_version", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["query_id"], ["saved_queries.id"],
            name="fk_provider_query_plans_query_id_saved_queries", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_provider_query_plans"),
        sa.UniqueConstraint(
            "id", "query_id", "provider", name="uq_provider_query_plans_id_query_provider"
        ),
    )
    op.create_index(
        "ix_provider_query_plans_query_provider", "provider_query_plans", ["query_id", "provider"]
    )

    op.create_table(
        "search_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("query_id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=True),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("new_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint("result_count >= 0", name="ck_search_runs_result_count_nonnegative"),
        sa.CheckConstraint("new_count >= 0", name="ck_search_runs_new_count_nonnegative"),
        sa.ForeignKeyConstraint(
            ["plan_id", "query_id", "provider"],
            ["provider_query_plans.id", "provider_query_plans.query_id", "provider_query_plans.provider"],
            name="fk_search_runs_plan_query_provider_provider_query_plans", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["query_id"], ["saved_queries.id"],
            name="fk_search_runs_query_id_saved_queries", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_search_runs"),
        sa.UniqueConstraint("id", "query_id", name="uq_search_runs_id_query_id"),
        sa.UniqueConstraint("id", "provider", name="uq_search_runs_id_provider"),
    )
    op.create_index("ix_search_runs_query_started", "search_runs", ["query_id", "started_at"])
    op.create_index("ix_search_runs_plan_id", "search_runs", ["plan_id"])
    op.create_index("ix_search_runs_provider_started", "search_runs", ["provider", "started_at"])

    op.create_table(
        "journals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("issn_print", sa.String(length=16), nullable=True),
        sa.Column("issn_electronic", sa.String(length=16), nullable=True),
        sa.Column("nlm_id", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_journals"),
    )
    op.create_index("ix_journals_nlm_id", "journals", ["nlm_id"])

    op.create_table(
        "articles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("abstract", sa.Text(), nullable=True),
        sa.Column("journal_id", sa.String(length=36), nullable=True),
        sa.Column("publication_type", sa.String(length=100), nullable=True),
        sa.Column("publication_status", sa.String(length=100), nullable=True),
        sa.Column("publication_year", sa.Integer(), nullable=True),
        sa.Column("publication_month", sa.Integer(), nullable=True),
        sa.Column("publication_day", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "publication_year IS NULL OR publication_year BETWEEN 1 AND 9999",
            name="ck_articles_publication_year_range",
        ),
        sa.CheckConstraint(
            "publication_month IS NULL OR publication_month BETWEEN 1 AND 12",
            name="ck_articles_publication_month_range",
        ),
        sa.CheckConstraint(
            "publication_day IS NULL OR publication_day BETWEEN 1 AND 31",
            name="ck_articles_publication_day_range",
        ),
        sa.CheckConstraint(
            "publication_month IS NULL OR publication_year IS NOT NULL",
            name="ck_articles_publication_month_requires_year",
        ),
        sa.CheckConstraint(
            "publication_day IS NULL OR publication_month IS NOT NULL",
            name="ck_articles_publication_day_requires_month",
        ),
        sa.ForeignKeyConstraint(
            ["journal_id"], ["journals.id"],
            name="fk_articles_journal_id_journals", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_articles"),
    )
    op.create_index("ix_articles_publication_year", "articles", ["publication_year"])
    op.create_index("ix_articles_journal_id", "articles", ["journal_id"])

    op.create_table(
        "article_identifiers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("article_id", sa.String(length=36), nullable=False),
        sa.Column("scheme", sa.String(length=40), nullable=False),
        sa.Column("value", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["article_id"], ["articles.id"],
            name="fk_article_identifiers_article_id_articles", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_article_identifiers"),
        sa.UniqueConstraint("scheme", "value", name="uq_article_identifiers_scheme_value"),
    )
    op.create_index("ix_article_identifiers_article_id", "article_identifiers", ["article_id"])

    op.create_table(
        "source_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("search_run_id", sa.String(length=36), nullable=True),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("compression", sa.String(length=40), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_metadata", sa.JSON(), nullable=False),
        sa.CheckConstraint("byte_size >= 0", name="ck_source_snapshots_byte_size_nonnegative"),
        sa.ForeignKeyConstraint(
            ["search_run_id", "provider"], ["search_runs.id", "search_runs.provider"],
            name="fk_source_snapshots_search_run_provider_search_runs", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_source_snapshots"),
        sa.UniqueConstraint("id", "provider", name="uq_source_snapshots_id_provider"),
    )
    op.create_index(
        "ix_source_snapshots_provider_captured", "source_snapshots", ["provider", "captured_at"]
    )
    op.create_index("ix_source_snapshots_search_run_id", "source_snapshots", ["search_run_id"])
    op.create_index("ix_source_snapshots_sha256", "source_snapshots", ["sha256"])

    op.create_table(
        "source_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("provider_record_id", sa.String(length=255), nullable=False),
        sa.Column("article_id", sa.String(length=36), nullable=False),
        sa.Column("snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parser_version", sa.String(length=100), nullable=False),
        sa.Column("rights_note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["article_id"], ["articles.id"],
            name="fk_source_records_article_id_articles", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "provider"], ["source_snapshots.id", "source_snapshots.provider"],
            name="fk_source_records_snapshot_provider_source_snapshots", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_source_records"),
        sa.UniqueConstraint("id", "provider", name="uq_source_records_id_provider"),
        sa.UniqueConstraint("provider", "provider_record_id", name="uq_source_records_provider_record_id"),
    )
    op.create_index("ix_source_records_article_id", "source_records", ["article_id"])
    op.create_index("ix_source_records_snapshot_id", "source_records", ["snapshot_id"])

    op.create_table(
        "source_record_snapshots",
        sa.Column("source_record_id", sa.String(length=36), nullable=False),
        sa.Column("snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_record_id", "provider"], ["source_records.id", "source_records.provider"],
            name="fk_source_record_snapshots_record_provider_source_records", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "provider"], ["source_snapshots.id", "source_snapshots.provider"],
            name="fk_source_record_snapshots_snapshot_provider_source_snapshots", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("source_record_id", "snapshot_id", name="pk_source_record_snapshots"),
    )
    op.create_index(
        "ix_source_record_snapshots_snapshot_provider", "source_record_snapshots", ["snapshot_id", "provider"]
    )

    op.create_table(
        "query_matches",
        sa.Column("query_id", sa.String(length=36), nullable=False),
        sa.Column("article_id", sa.String(length=36), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_run_id", sa.String(length=36), nullable=True),
        sa.Column("last_run_id", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(
            ["article_id"], ["articles.id"],
            name="fk_query_matches_article_id_articles", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["first_run_id", "query_id"], ["search_runs.id", "search_runs.query_id"],
            name="fk_query_matches_first_run_query_search_runs", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["last_run_id", "query_id"], ["search_runs.id", "search_runs.query_id"],
            name="fk_query_matches_last_run_query_search_runs", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["query_id"], ["saved_queries.id"],
            name="fk_query_matches_query_id_saved_queries", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("query_id", "article_id", name="pk_query_matches"),
    )
    op.create_index("ix_query_matches_article_id", "query_matches", ["article_id"])
    op.create_index("ix_query_matches_first_seen", "query_matches", ["first_seen_at"])

    op.create_table(
        "project_articles",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("article_id", sa.String(length=36), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_decision", sa.String(length=20), server_default=sa.text("'none'"), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "review_decision IN ('none', 'important', 'excluded')",
            name="ck_project_articles_review_decision_valid",
        ),
        sa.ForeignKeyConstraint(
            ["article_id"], ["articles.id"],
            name="fk_project_articles_article_id_articles", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["research_projects.id"],
            name="fk_project_articles_project_id_research_projects", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("project_id", "article_id", name="pk_project_articles"),
    )
    op.create_index(
        "ix_project_articles_review_decision", "project_articles",
        ["project_id", "review_decision", "last_seen_at"],
    )

    op.create_table(
        "tags",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["research_projects.id"],
            name="fk_tags_project_id_research_projects", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tags"),
        sa.UniqueConstraint("project_id", "id", name="uq_tags_project_id_id"),
        sa.UniqueConstraint("project_id", "normalized_name", name="uq_tags_project_normalized_name"),
    )

    op.create_table(
        "project_article_tags",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("article_id", sa.String(length=36), nullable=False),
        sa.Column("tag_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "article_id"], ["project_articles.project_id", "project_articles.article_id"],
            name="fk_project_article_tags_project_article_project_articles", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "tag_id"], ["tags.project_id", "tags.id"],
            name="fk_project_article_tags_project_tag_tags", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("project_id", "article_id", "tag_id", name="pk_project_article_tags"),
    )
    op.create_index("ix_project_article_tags_tag_id", "project_article_tags", ["project_id", "tag_id"])

    op.create_table(
        "authors",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("display_name", sa.String(length=500), nullable=False),
        sa.Column("orcid", sa.String(length=19), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_authors"),
    )
    op.create_index("ix_authors_orcid", "authors", ["orcid"])

    op.create_table(
        "article_authors",
        sa.Column("article_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.String(length=36), nullable=True),
        sa.Column("display_name", sa.String(length=500), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_article_authors_position_nonnegative"),
        sa.ForeignKeyConstraint(
            ["article_id"], ["articles.id"],
            name="fk_article_authors_article_id_articles", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["author_id"], ["authors.id"],
            name="fk_article_authors_author_id_authors", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("article_id", "position", name="pk_article_authors"),
    )
    op.create_index("ix_article_authors_author_id", "article_authors", ["author_id"])

    op.create_table(
        "topics",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("vocabulary", sa.String(length=100), nullable=False),
        sa.Column("normalized_key", sa.String(length=255), nullable=False),
        sa.Column("label", sa.String(length=500), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_topics"),
        sa.UniqueConstraint("vocabulary", "normalized_key", name="uq_topics_vocabulary_normalized_key"),
    )

    op.create_table(
        "article_topics",
        sa.Column("article_id", sa.String(length=36), nullable=False),
        sa.Column("topic_id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("assignment_method", sa.String(length=100), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["article_id"], ["articles.id"],
            name="fk_article_topics_article_id_articles", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"], ["topics.id"],
            name="fk_article_topics_topic_id_topics", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("article_id", "topic_id", "source", name="pk_article_topics"),
    )
    op.create_index("ix_article_topics_topic_source", "article_topics", ["topic_id", "source"])

    op.create_table(
        "article_relations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_article_id", sa.String(length=36), nullable=False),
        sa.Column("target_article_id", sa.String(length=36), nullable=False),
        sa.Column("relation_type", sa.String(length=100), nullable=False),
        sa.Column("source_provider", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_article_id"], ["articles.id"],
            name="fk_article_relations_source_article_id_articles", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_article_id"], ["articles.id"],
            name="fk_article_relations_target_article_id_articles", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_article_relations"),
        sa.UniqueConstraint(
            "source_article_id", "target_article_id", "relation_type",
            name="uq_article_relations_source_target_type",
        ),
    )
    op.create_index("ix_article_relations_target_article_id", "article_relations", ["target_article_id"])

    op.create_table(
        "citation_observations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("article_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("count >= 0", name="ck_citation_observations_count_nonnegative"),
        sa.ForeignKeyConstraint(
            ["article_id"], ["articles.id"],
            name="fk_citation_observations_article_id_articles", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_citation_observations"),
    )
    op.create_index(
        "ix_citation_observations_article_as_of", "citation_observations", ["article_id", "as_of"]
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("type", sa.String(length=150), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("aggregate_type", sa.String(length=100), nullable=False),
        sa.Column("aggregate_id", sa.String(length=36), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint("attempts >= 0", name="ck_outbox_events_attempts_nonnegative"),
        sa.CheckConstraint("schema_version >= 1", name="ck_outbox_events_schema_version_positive"),
        sa.PrimaryKeyConstraint("id", name="pk_outbox_events"),
    )
    op.create_index("ix_outbox_events_pending", "outbox_events", ["processed_at", "occurred_at"])
    op.create_index(
        "ix_outbox_events_aggregate", "outbox_events", ["aggregate_type", "aggregate_id", "occurred_at"]
    )


def downgrade() -> None:
    op.drop_table("outbox_events")
    op.drop_index("ix_citation_observations_article_as_of", table_name="citation_observations")
    op.drop_table("citation_observations")
    op.drop_index("ix_article_relations_target_article_id", table_name="article_relations")
    op.drop_table("article_relations")
    op.drop_index("ix_article_topics_topic_source", table_name="article_topics")
    op.drop_table("article_topics")
    op.drop_table("topics")
    op.drop_index("ix_article_authors_author_id", table_name="article_authors")
    op.drop_table("article_authors")
    op.drop_index("ix_authors_orcid", table_name="authors")
    op.drop_table("authors")
    op.drop_index("ix_project_article_tags_tag_id", table_name="project_article_tags")
    op.drop_table("project_article_tags")
    op.drop_table("tags")
    op.drop_index("ix_project_articles_review_decision", table_name="project_articles")
    op.drop_table("project_articles")
    op.drop_index("ix_query_matches_first_seen", table_name="query_matches")
    op.drop_index("ix_query_matches_article_id", table_name="query_matches")
    op.drop_table("query_matches")
    op.drop_index("ix_source_record_snapshots_snapshot_provider", table_name="source_record_snapshots")
    op.drop_table("source_record_snapshots")
    op.drop_index("ix_source_records_snapshot_id", table_name="source_records")
    op.drop_index("ix_source_records_article_id", table_name="source_records")
    op.drop_table("source_records")
    op.drop_index("ix_source_snapshots_sha256", table_name="source_snapshots")
    op.drop_index("ix_source_snapshots_search_run_id", table_name="source_snapshots")
    op.drop_index("ix_source_snapshots_provider_captured", table_name="source_snapshots")
    op.drop_table("source_snapshots")
    op.drop_index("ix_article_identifiers_article_id", table_name="article_identifiers")
    op.drop_table("article_identifiers")
    op.drop_index("ix_articles_journal_id", table_name="articles")
    op.drop_index("ix_articles_publication_year", table_name="articles")
    op.drop_table("articles")
    op.drop_index("ix_journals_nlm_id", table_name="journals")
    op.drop_table("journals")
    op.drop_index("ix_search_runs_provider_started", table_name="search_runs")
    op.drop_index("ix_search_runs_plan_id", table_name="search_runs")
    op.drop_index("ix_search_runs_query_started", table_name="search_runs")
    op.drop_table("search_runs")
    op.drop_index("ix_provider_query_plans_query_provider", table_name="provider_query_plans")
    op.drop_table("provider_query_plans")
    op.drop_index("ix_saved_queries_project_active", table_name="saved_queries")
    op.drop_table("saved_queries")
    op.drop_table("research_projects")
