# V1 gap remediation

Requested order: monitoring coverage, recovery, execution audit, metadata,
conflict resolution, desktop workflow, release acceptance. Implement and test each
increment separately; do not add AI, PDFs, trial providers or analytics.

## Completion status — 0.1.4

Steps 1–6 are implemented and committed, with migrations through `0009`.
Step 7 automated verification passed: 318 tests (one optional PostgreSQL test
skipped in the default run), plus the separate live PostgreSQL migration/upgrade,
audited ingestion/review and scheduled-continuation check. Final native Windows
acceptance is not complete; use [the pilot checklist](WINDOWS_ACCEPTANCE.md).
The designs below preserve the rationale and limitations, not promises of
exhaustive provider coverage or multi-user deployment.

## Step 1 — Monitoring coverage (design before implementation)

`monitoring_coverage` stores one checkpoint per query/provider/lane. Full sweeps
use the exact saved query and continue from committed pages instead of restarting.
A separate recent lane intersects original date filters with an overlapping
publication window (initial 30 days; subsequent windows start seven days before
the last completely covered upper date). Only providers that support both date
bounds use that lane. Unsupported intent is never relaxed. Full sweeps repeat on
completion to reconcile delayed indexing, older metadata updates and undated
records. This is eventual best-effort reconciliation against mutable API indexes,
not an atomic snapshot of an external database.

Windows remain frozen until exhausted, including across failures. Query revision,
parser/translation version or page-size changes invalidate old cursor signatures.
Every actual request still creates its own revision-bound audited SearchRun.
SearchRun points to its coverage checkpoint; effective date bounds remain in its
provider plan. Page persistence atomically advances checkpoint, run cursor,
observations and outbox. Process death cannot skip an uncommitted page. Previously
committed pages are retained; invalid/expired cursors require an explicit restart.

Scheduled executions gain `partial`: a bounded page budget is unfinished coverage,
not a provider failure. Any genuine source error still makes the attempt failed,
without preventing other sources from running. The existing scheduled-query lock
remains the owner; coverage use requires a running scheduled execution. Restarting
or editing a query never mutates historical runs or canonical paper identities.

Migration `0006_monitoring_coverage` adds checkpoint records, SearchRun linkage and
the partial execution state, preserving existing rows. Tests must verify multiple
due attempts reach later pages, frozen recent bounds, revision invalidation,
transaction rollback, failed-page retry and exhausted-cycle reconciliation.

External limitations remain honest: PubMed's 10,000-result search cap requires
narrower queries/date ranges, and publication windows alone cannot find old papers
indexed late. Full reconciliation is therefore necessary. Native graphical pilot
requires an actual target display; PostgreSQL checks require an accessible test DB.

## Step 2 — Recovery (design before implementation)

Add a UI-neutral RecoveryService and repository port to list running provider
runs, explicitly fail interrupted manual runs, restart invalid/expired coverage
campaigns, and queue scheduled retries. Never expire a live lock automatically.
Recovering requires confirmation that its worker has stopped; campaign restarts
and retry queues reject still-running owners. Every operator action writes an
outbox audit event in the same transaction. History and raw snapshots are retained.

Manual partial/failed runs can be resumed through the ingestion service only if
query revision, provider translation/parameters and parser remain compatible.
Scheduled runs resume through their coverage campaign instead, not a manual path.
The new run preserves start_cursor and an audited link to its predecessor. No new
schema is needed for this increment: existing run/checkpoint/outbox fields suffice.
CLI controls must expose inspect/recover/restart/queue operations without importing
the desktop client. Desktop recovery/history screens follow in Step 6.

## Step 3 — Execution audit (approved implementation design)

Add an additive `execution_attempts` journal, recorded before validation/planning.
An attempt retains requested query/provider identifiers (not search text, API keys,
proxy URLs or raw exceptions), start/end, running/completed/partial/failed state,
sanitized error class, and optional actual SearchRun link. Requested identifiers
are not foreign keys: rejected unknown queries/providers must also be auditable.
Successful planning still uses the existing revision-bound SearchRun and plan;
the attempt links that run and is finalized alongside run completion. Interrupted
pre-run attempts remain running until explicit stopped-worker recovery. No
historical run/snapshot is rewritten and no pre-migration attempt is fabricated.
Migration 0007 adds this journal without rebuilding existing audit tables.

## Step 4 — Metadata completeness (implementation design)

Extend the provider record with optional citation count, multiple publication
types and explicit publication status. Unknown counts stay unknown; counts from
different sources are separate observations, never summed or treated as equivalent.
Persist canonical URL and publication types/status with the existing field-level
source-selection/provenance policy. Preserve each source's database, URL and
normalized assertions on its latest record and immutable snapshot association.
Store count observations linked to snapshot/provider, which already identifies
the search run and retrieval time. Historical count rows without evidence keep
nullable linkage; do not fabricate origins. Legacy URLs/types remain unknown
until refreshed, with identifier-derived links used only as a display fallback.
Migration 0008 adds these fields and citation evidence linkage. Paper details and
exports expose the enriched metadata without requiring desktop components.

## Step 5 — Identity review (implementation design)

Add UI-neutral review operations for exact identifier conflicts and conservative
possible-duplicate candidates. Candidates with the same normalized long title and
known publication year remain separate canonical papers; a flag is not evidence
that they should be merged. Review can record keep-separate decisions, reject a
source assertion, or select an existing conflict candidate for subsequent ingestion.
Selecting a candidate explicitly rejects the asserted identifiers owned by other
papers; it never transfers identifier ownership or reparents an existing source.
Apply that decision only when the incoming normalized identifier fingerprint
matches the reviewed evidence. Changed assertions need a new review. Record reviewer
reason, decision time, evidence snapshot and outbox audit event transactionally.
No destructive historical consolidation or automatic fuzzy merging is introduced.
Historical conflicted runs remain conflicted; a later search can ingest a reviewed
record. Migration 0009 will preserve review decisions and candidate triage.

## Step 6 — Desktop workflow (implementation design)

Expose immutable query revision/run history, archived-query restoration,
status/read/tag filters and existing recovery operations through application
services. Retry network work uses a background worker. Show honest indeterminate
progress/source status and sanitized failures; do not invent page percentages.
Identity review UI consumes the new review service, not database models. Keep
native acceptance pending until Windows interaction evidence is available.

## Step 7 — Acceptance target

The user selected Windows for final native desktop acceptance. Repository tests
and Linux/offscreen verification cannot complete that gate. Prepare reproducible
Windows PowerShell installation, isolated pilot data, backup/restore and GUI
checklists; record actual target-machine results separately from automation.
