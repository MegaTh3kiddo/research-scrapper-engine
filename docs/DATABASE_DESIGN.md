# Database Design

This page records the implemented migration-backed model alongside longer-term
extension considerations. The schema is not merely a proposal; see the numbered
Alembic migrations for exact constraints and [Architecture](ARCHITECTURE.md) for
service boundaries. PostgreSQL execution of migration `0010` remains to be checked
on a disposable live server.

## Google Sheets integration (0010)

`google_sheet_links` is a project-owned optional configuration. Its primary key is
`project_id`, with `spreadsheet_id`, `enabled`, `last_success_at`,
`last_synced_count`, and `last_error_code`. Credentials remain outside the database
in a local service-account JSON file. Canonical articles and project-paper
relations remain the source of truth; the Sheet is an idempotent projection keyed
by `article_id`, not a second record store. A failed sync does not change a search
run's persisted status. No historical project data is rewritten.

## Identity review (0009)

Articles gain an indexed, optional conservative title/year candidate key. It never
participates in automatic deduplication. `possible_duplicate_candidates` retains
ordered canonical pairs, raw snapshot evidence and triage status. Existing articles
receive keys only on refresh; no unknown historical evidence is fabricated.
`identity_review_decisions` stores one immutable decision per conflict/candidate,
reason/time, evidence snapshot, optional selected article, normalized rejected IDs
and assertion signature. Conditional status transitions and decision/outbox writes
share a transaction. Source-selection decisions apply only to the same normalized
provider/source identifier signature, never moving existing source/identifier
ownership. Changed or newly conflicting identifiers require another review.
Rejected/accepted assertion replay records snapshot/run-linked outbox audit events.
Possible duplicates can only be kept separate; historical consolidation is not a
supported action. These are local-operator decisions, not authenticated multi-user
audit identities. Downgrading removes new review tables: restore a full backup
instead of treating downgrade as a recovery mechanism.

## Metadata completeness (0008)

Articles retain canonical URL and multiple publication types, with legacy singular
type retained for compatibility. URL/types/status use existing field-level evidence
and source priority. Source records retain database, URL and normalized assertions;
source-record/snapshot links preserve immutable parsed assertion history in addition
to original raw bytes. Existing rows keep null/empty unknown origins until refreshed.
Citation observations optionally reference provider-matched snapshots; one count per
article/snapshot, including zero, remains attributable to retrieval/run/parser.
Different sources' counts are not summed. Paper details expose the latest known
count per source and its timestamp; older observations remain queryable in storage.
CSV adds types/status/source databases and JSON citation observations; RIS retains
these source-specific facts as notes, without inventing a shared citation metric.

## Execution-attempt journal (0007)

`execution_attempts` records requests before query validation/provider planning.
Requested query/provider identifiers and retry references are bounded and sanitized
and intentionally lack foreign keys so rejected unknown IDs can be retained.
Attempts have start/end, running/completed/partial/failed/skipped state, sanitized
error code and an optional unique SearchRun foreign key. A linked run owns atomic
finalization/recovery of its attempt. Legacy runs have no fabricated attempt rows.
Pre-run process interruption requires explicit stopped-worker recovery; an empty
recent-window intersection is recorded as skipped, not a provider failure.

## Monitoring coverage migration

`0006_monitoring_coverage` adds immutable campaign context with mutable atomic
progress: query revision, provider, lane, request signature, frozen bounds,
cursor/visited cursors, completion timestamp and page count. A partial unique
index allows one active campaign per query/provider/lane; historical campaigns
remain linked to SearchRun.coverage_id. SearchRun.start_cursor preserves exactly
where each continuation began. Scheduled executions now distinguish partial
coverage from failure. Existing rows migrate unchanged; downgrading converts
partial attempts to failed with a CoverageDowngrade marker rather than deleting
history. See [gap remediation](GAP_REMEDIATION.md).

## 0.1.3 integration review

Provider names/IDs are data; existing revisions, snapshots, runs and observations
support OpenAlex/Crossref. Migration `0005_metadata_provenance` adds an article
JSON map for chosen title/abstract/date/journal/authors, linking each to provider,
source ID, snapshot, parser and run. This avoids arrival-order metadata changes
when higher-ranked sources omit fields. Existing rows receive `{}`: old field
origins are not guessed. Source priorities are injectable and applied per field;
no evidence-quality assessment or provider-by-field authority matrix is added.
Settings remain local non-secret configuration. Clinical trials and explicit
identifier-targeted enrichment remain future schema/use-case work. See
[Source/settings design](SOURCE_SETTINGS_DESIGN.md).

Stage 1 defined the initial SQLAlchemy schema in `0001_initial_schema`; Stage 3 extends it through `0002_stage3_audit`, and Stage 4 adds `0003_saved_query_revisions`. SQLite is the tested local database; PostgreSQL remains the future shared-server target. The domain and application layers access persistence contracts rather than ORM models.

Stage 5's desktop client requires no schema migration. Its paper-detail read model joins existing canonical article, author, identifier, journal, source, and project-review records without duplicating them.

## Stage 6 schema decision (documented before implementation)

Migration `0004` adds `query_schedules` (one configurable interval and next due time per saved query) and `scheduled_executions` (one durable attempt with running/completed/failed state, start/end times, and a safe error code). A nullable composite foreign key from `search_runs` to its scheduled execution links even failed ingestion runs to the attempt while preserving legacy/manual runs. A partial unique index on running executions per query rejects overlapping scheduled claims, including across independent local processes. Claiming a due schedule and advancing its next due time happen in one transaction. Crashed attempts remain visible as running and block that query until an explicit recovery action marks them failed; no automatic expiry can accidentally start a second concurrent worker. The outbox schema is unchanged and remains recording-only.

## Stage 3 schema refinement (documented before implementation)

The initial schema provides the run → snapshot and source record → snapshot links, but it cannot independently audit each run's new/updated classification, and parser version is only on the mutable latest source-record pointer. Migration `0002` will add `source_snapshots.parser_version`, source-asserted identifiers on `source_records`, a `search_run_observations` table containing the run, source record, article, metadata snapshot, observation time and classification flags, and an `identity_conflicts` table for exact identifiers that point to different canonical articles. Search-run counters and the configured recent-publication window will be recorded with the run. The identifier scheme column is widened for future provider namespaces. Existing Stage 1 tables and identifiers remain intact; the parser-version column uses an explicit `unknown` backfill for any preexisting snapshots, not a claim that they were parsed by the new version.

For Stage 3, “newly discovered” means a canonical article first created in this library; “new to query” means its first match to that saved query. “Newly published” is an independent assessment against a configurable recent window (default 30 days) ending at the run start; it is unknown if the source date lacks day precision. “Metadata updated” means an accepted canonical display field changed, not merely that a fresh raw response was fetched. These flags can coexist. Title similarity alone never auto-merges; conflicting exact identifiers create an unresolved conflict record rather than overwriting either article.

Snapshot bytes are immutable content-addressed files. Each retrieval gets its own database snapshot row even if bytes are identical, preserving retrieval time, safe request metadata, run, provider, and parser version. File writes are atomic; an aborted database transaction may leave an unreferenced blob, which is harmless and can be reconciled later. The business rows and outbox events are committed in one database transaction.

## Stage 4 schema decision (documented before implementation)

The existing `research_projects`, `project_articles`, `tags`, and `project_article_tags` tables already support shared canonical papers, per-project tags, and independent reading and review decisions. No new review-status column is needed: `unread`/`read` derive from `read_at` when the decision is `none`; `important` and `excluded` take display precedence while preserving whether the paper was read. Read/unread filtering remains independent of display status.

Saved queries previously held only their latest expression and filters. To preserve the precise user intent behind historical executions when queries are edited, migration `0003` adds append-only `saved_query_revisions` with a per-query revision number and copies of expression, filters, enabled providers, active state, and archive timestamp. It backfills one revision for each existing saved query. `saved_queries.revision_number` records the current revision, and `search_runs.query_revision_id` links new runs to the exact revision used; legacy runs may have a null revision link because their original portable intent cannot be reconstructed reliably. The ingestion service rejects a concurrent query edit between loading a revision and starting its run. Existing provider query plans continue to preserve source-specific translation.

Project-paper links created from a query match are idempotent and retain the earliest discovery time; subsequent sightings update `last_seen_at`. A manual project attachment is allowed for an existing canonical paper. Hard unlinking is deferred because deleting the relation would erase project-specific review/tag history; `excluded` is the reversible way to remove a paper from ordinary review views.

## Main entities

| Entity | Purpose and important fields |
| --- | --- |
| `research_projects` | Stable ID, name, description, archive timestamp, and created/updated timestamps. Future owner and sharing fields can be added. |
| `saved_queries` | Project ID, user-entered query, filters, enabled providers, active state, archive timestamp, and created/updated times. Schedule preference is a later addition. |
| `saved_query_revisions` | Immutable per-query snapshots of portable expression, filters, enabled providers, and active state; new search runs reference the exact revision used. |
| `provider_query_plans` | Saved-query and provider IDs, translated query, sanitized parameters, unsupported filter record, translation version. Makes source semantics auditable. |
| `search_runs` | Query/provider and optional exact plan ID, start and end times, state, cursor or checkpoint, result/new/update/recent/conflict counts, recent-publication window, and failure details. |
| `query_schedules` | One per-query interval, enabled flag, next due time, and timestamps. |
| `scheduled_executions` | Durable claimed attempt with running/completed/failed state, times, and safe error code; linked search runs retain provider-level outcomes. |
| `articles` | Canonical publication ID, title, normalized display metadata, abstract if permitted, journal, publication type, status, and separate publication year/month/day fields. Null month or day preserves source precision. |
| `article_identifiers` | Article ID, identifier scheme and normalized value with uniqueness constraints; DOI, PMID, PMCID, OpenAlex and other IDs. |
| `source_records` | Provider, provider record ID, original asserted identifiers, article ID, retrieval and source-update times, latest snapshot ID, parser version, license or rights metadata. Multiple source records may support one article. |
| `source_snapshots` | Immutable storage key, content hash, parser version, content type, compression, size, capture time, optional search-run ID, and safe request metadata. Raw payload lives behind the snapshot storage adapter. |
| `source_record_snapshots` | Historical many-to-many link from source records to every response snapshot that contained them. |
| `search_run_observations` | Per-run source/article/snapshot link, observation time, and independent newly discovered, new-to-query, newly published, and metadata-updated facts. |
| `identity_conflicts` | Unresolved exact-identifier assertions pointing to multiple canonical articles, with run and snapshot provenance for later manual review. |
| `query_matches` | Query/article association, first and last seen times, and first/last run IDs. |
| `project_articles` | Project/article association, first and last seen times, `read_at`, and independent review decision (`none`, `important`, `excluded`). |
| `tags` and `project_article_tags` | Project-scoped tag names and paper assignments. |
| `authors` and `article_authors` | Author information, optional stable IDs such as ORCID, article order, and source display name. Name alone is not a merge key. |
| `journals` | Title, ISSNs and optional NLM ID. |
| `topics` and `article_topics` | Controlled vocabulary/code or free keyword, assignment source, method, and time. |
| `article_relations` | Typed links for preprint version, correction, retraction, related study, and similar relationships. |
| `citation_observations` | Article, source, count, and observation time. Citation counts remain provider-specific. |
| `outbox_events` | Event ID and version, type, aggregate ID, payload, occurred time, processing state and retry metadata. Future application services must write events in the same transaction as domain changes. |

## Integrity and migration rules

- Store timestamps in UTC; retain the original source date and its precision through separate year, month, and day fields. The persistence `UTCDateTime` type normalizes aware values on write and restores UTC on SQLite reads.
- Enforce unique normalized `(scheme, value)` identifiers when an identifier is trustworthy. Keep provider record identity unique within a provider.
- Database date checks enforce year/month/day ranges and presence relationships; the domain `PartialDate` validates actual calendar dates. Future repository writes must pass through that domain validation.
- Make project/article associations unique while allowing the same article in multiple projects.
- Enforce provider and query identity along audit links with composite foreign keys. Project and query hard deletes are restricted while dependent query history exists; normal removal should archive records.
- Keep snapshots immutable and verify content hashes. Never persist API keys or secrets in request metadata or query-plan parameters.
- Preserve source assertions when a canonical value changes. A later parser should be able to reprocess a source snapshot and explain changed metadata.
- Make search runs and outbox delivery repeatable and idempotent; a failed run must not advance a checkpoint past unprocessed records.
- A run may lack a query-plan ID when planning itself fails. Future application services must require an exact plan for completed provider runs.
- Use portable SQLAlchemy mappings and Alembic migrations, then verify migrations against both database engines before server deployment.

Stage 1 tests verify the initial migration against SQLite, including up/down migration, ORM metadata parity, foreign keys, uniqueness, provider/query audit identity, project tag scoping, timestamp round trips, and snapshot history. Stage 3 tests verify the upgrade/backfill/downgrade of `0002`, immutable snapshot byte storage, exact-identifier ingestion, cross-run classifications, conflict audit, and same-transaction outbox writes. Stage 4 tests verify `0003` upgrade/backfill/downgrade, revision-bound runs, project-scoped review/tags, and idempotent linking. Stage 6 tests verify `0004` migration parity/downgrade, scheduled run links, and overlap enforcement. Stage 7 verified a fresh PostgreSQL 16 migration and a `0003`-to-head upgrade with existing rows, plus an opt-in empty-database migration test. Shared PostgreSQL deployment and outbox consumption remain later work.

## Later expansions

Clinical trial monitoring should add `clinical_studies`, status history, and study-to-article links, keyed by NCT ID. Multiple researchers later add users, memberships, permissions, and audit events. Full-text assets, AI outputs, relevance scores, and evidence extractions should be separately versioned and connected to the exact article and source snapshot used; none should overwrite bibliographic source data.
