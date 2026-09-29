# Changelog

## Public repository documentation foundation — 2026-09-29

- Rewrote the README as a current quick start and honest release-status entry point;
  added a documentation index, contribution/security guides, GitHub issue/PR
  templates, and user-approved MIT license.
- Corrected stale desktop-only, daily-default, and five-second polling language in
  current guides. Historical acceptance reports remain dated evidence.
- Added a public-push checklist and generalized a private proxy address in the
  current settings example and tests. Earlier commits still contain that address.
- Extended `.gitignore` for example workspaces, SQLite sidecars, snapshots, settings,
  and logs to reduce accidental publication of local research data.
- No application behavior or database schema was changed in this documentation pass.

## Optional Google Sheets projection

- Added per-project Sheet links and durable sync status (migration `0010`).
- Manual browser Sync and opt-in sync after completed/partial scheduled searches;
  idempotent canonical-paper rows and last-successful-update marker in a dedicated tab.
- Service-account JSON stays outside the database; optional `sheets` dependency,
  bounded Google API retries, and saved proxy reuse. Search-run outcomes are not
  changed by Sheet failures. Live permission check awaits user credentials/Sheet.
- Verification: 373 passed, 1 optional PostgreSQL test skipped; 0009-to-0010
  upgrade retained project data, mock Google API request shapes passed, and wheel
  includes the new integration and migration. Browser JavaScript syntax passed.

## Activity scrolling and monitoring cadence

- Recent activity is keyboard-focusable and scrolls vertically at a maximum height of `100vh`.
- Automatic browser refresh runs every minute; manual Refresh now remains available.
- New source-search schedules default to hourly in application services and both clients.
- Existing stored query schedules/settings are preserved; server due checks remain every minute by default.

## User guides

- Added `docs/USER_GUIDE.md` and packaged browser **Help / Guide** with separate
  First-Time Setup and Ongoing Workflow flows.
- Documented OS-specific launch commands, source/settings boundaries, Boolean
  syntax, scheduling, identity tracking, reviews, exports, backup, and troubleshooting.
- Clarified desktop-only actions and corrected the web setup guide's OS headings.
- Browser HTML assets are explicitly decoded as UTF-8 for consistent help text on Windows.
- No schema changes, provider changes, or additional dependencies.
- Verification: 359 tests passed, 1 optional PostgreSQL test skipped; Chromium
  Help navigation, keyboard expansion, existing workflows, and desktop/mobile
  layout passed. Wheel build includes the guide asset. Native Windows remains pending.

## Browser polish and portable Boolean search

- Responsive browser dashboard, multiline expression editor, operator insertion,
  live syntax/provider previews and saved-query editing with revision history.
- Shared NOT/AND/OR parser with parentheses, quoted phrases, character-position
  errors and explicit provider grouping; translation versions advanced.
- Crossref Boolean metadata evaluation, bounded candidate branch pagination,
  filtered-page raw snapshots and canonical deduplication through existing services.
- Source execution/coverage notices, readable activity/library/history and shared
  publication-link policy. No schema migration required.
- Chromium desktop/mobile interaction pilot passed; native NOT API checks accepted
  by PubMed, Europe PMC and OpenAlex.
- Full suite: 359 passed, 1 optional PostgreSQL test skipped. Wheel build and
  bounded live Crossref Boolean candidate-page/continuation checks passed.

## Local browser client

- Optional `[web]` dependencies and `research-engine-web` loopback launcher.
- Shared application service routes for projects, queries, searches, papers,
  execution audit and monitoring; packaged lightweight browser interface.
- Worker-thread long operations, bounded transport jobs, clean shutdown, optional
  schedule polling and local-browser mutation/Host safeguards.
- No new schema, AI/PDF functionality, or public/multi-user deployment support.

## 2026-09-26 — 0.1.4 priority gap remediation

- Added durable full/recent monitoring campaigns and partial continuation (0006).
- Added operator recovery/restart and compatible new-run retry with audit history.
- Added pre-validation execution journal and client history (0007).
- Preserved original source assertions, URL/type/status provenance and source-specific
  snapshot-linked citation observations (0008), with provider parser updates.
- Added explicit evidence-bound identity review and conservative title/year flags
  without historical merging or ownership transfer (0009).
- Desktop exposes history, archived queries, filters, recovery, review and truthful
  busy/source status; publication links accept only HTTP(S).
- Moved composition out of desktop; aligned module/package version from metadata.
- Verification: 318 passed/1 skipped by default; separate PostgreSQL 16 migration,
  retained-data upgrade, ingestion/review and scheduled-continuation checks passed.
- Windows is the native acceptance target; physical interaction remains pending.

## 2026-09-26 — Gap remediation, recovery

- Added stopped-worker manual recovery, compatible manual resume, coverage
  campaign restart and immediate scheduled retry queue with audit events.
- Moved service composition out of the desktop package for CLI/future workers.
- Expanded recovery CLI and installation guide; no automatic lock expiry.
- Full suite: 233 passed, 1 skipped before additional CLI review regressions.

## 2026-09-26 — Gap remediation, monitoring coverage

- Added `0006_monitoring_coverage` and atomic per-lane campaign checkpoints.
- Full sweeps continue/reconcile; recent windows freeze until exhausted and overlap
  previous coverage by seven days. Query/config changes abandon old campaigns
  without mutating their historical run links.
- Added partial scheduled state and cross-attempt cursor-loop rejection.
- Full suite: 215 passed, 1 skipped. Windows selected for native acceptance.

Document material repository and product changes here. Entries describe what changed and why; implementation releases can later follow a versioned format.

## 2026-09-26 — 0.1.3 sources and workflow settings

- Added OpenAlex and Crossref discovery/fetch modules with fixture tests, current
  access rules, explicit capabilities, pagination and raw-response audit.
- Replaced two-source UI assumptions with a catalog, source choices and tabbed
  settings for discovery/monitoring, budgets, export, API access and network.
- Added session/environment-only keys, safe HTTPX URL-log redaction and non-secret
  atomic settings persistence; retained compatible proxy settings.
- Added application-layer batch searches and monitoring failure isolation.
- Added `0005_metadata_provenance`, per-field source selection and detail tracing;
  protected unknown existing ownership rather than inventing provenance.
- Kept enrichment, Semantic Scholar/trials, AI, PDFs and analytics out of scope.
- Verification: 208 passed/1 skipped; live new-source ingestion/repeat/export
  checks and installed-wheel offscreen startup passed. Native acceptance remains.

## 2026-09-25 — 0.1.2 opt-in literature API proxy

- Added a saved desktop Network / proxy setting behind a UI-independent application service; PubMed and Europe PMC manual/scheduled sessions use the selected route.
- Added `httpx[socks]` and `socks5h://` support, direct-mode environment isolation, URL validation, and owner-only local settings persistence.
- Verified the configured SOCKS5H proxy against both live APIs and added settings, transport, composition, and offscreen UI tests.

## 2026-09-25 — 0.1.1 PubMed identity remediation

- Restricted PubMed self-identifiers to the direct `PubmedData/ArticleIdList`, rejected mismatched top-level PubMed IDs, and advanced parser provenance to `pubmed-parser-v2`.
- Refuse new ingestion against databases containing legacy `pubmed-parser-v1` source records; existing data remains available for backup and inspection.
- Added nested-reference parser and ingestion regression tests. Repeated live searches in a fresh database; PubMed produced 132 distinct discoveries with no metadata updates or identity conflicts.
- Documented safe handling of affected 0.1.0 databases. No automatic in-place repair was attempted; native graphical acceptance remains open.

## 2026-09-25 — Acceptance pilot (documentation only)

- Installed the Stage 7 wheel in a fresh environment; exercised offscreen PySide6 workflows against live PubMed and Europe PMC, plus CSV/RIS export and stopped-process backup/restore.
- Found a critical PubMed reference-identifier parsing defect causing false canonical merges. Marked v1 not deployable and documented remediation/target-machine acceptance gates in the acceptance report and known limitations.

## 2026-09-25 — Stage 7 release verification and hardening

- Packaged Alembic configuration/revisions with the wheel and verified a clean virtual-environment installation and offscreen desktop startup outside the source tree.
- Verified PostgreSQL 16 fresh migration and existing-data upgrade from revision `0003`; added an opt-in disposable-database migration check.
- Added guarded interrupted-attempt recovery that also fails linked running provider runs atomically, plus safe rotating local logs.
- Expanded provider, recovery, and packaging tests; documented installation, backup, upgrade, recovery, and known deployment limitations.

## 2026-09-25 — Stage 6 monitoring and export

- Added reusable CSV/RIS project export with canonical metadata, identifiers, review state, tags, and project context; added background desktop file export.
- Added `0004_monitoring` for persisted schedules and execution attempts, a one-running-attempt-per-query index, and exact links to provider search runs.
- Added UI-neutral scheduled monitoring orchestration, a local polling host, and desktop query controls for monitoring intervals.
- Added migration, export, schedule success/failure/overlap, and offscreen desktop tests. The outbox remains recording-only.

## 2026-09-25 — Stage 5 desktop client

- Added an optional PySide6 desktop client with separate composition root, presentation views, controller, and background search worker.
- Added paper-detail and manual-search application contracts reusable by later API/server clients.
- Added essential project, saved-query, manual discovery, paper detail, review, and tag controls. Project deletion is an explicit archive preserving audit history.
- Added offscreen desktop workflow tests with a fake provider; deferred scheduling, export, and packaging to later stages.

## 2026-09-25 — Stage 4 Research Projects and workflow backend

- Added `0003_saved_query_revisions` with backfill, exact run-to-revision links, and upgrade/downgrade tests.
- Added UI-independent project/query application and persistence services, including archive/restore and historical run listing.
- Added shared canonical-paper project associations, project-scoped review/read state and tags, filtering, and manual-attachment/review outbox events.
- Ingestion now pins its saved-query revision and idempotently attaches matched papers to the owning project.
- Added workflow tests for cross-project isolation, idempotence, query revision races, and audited events.

## 2026-09-25 — Stage 3 records, persistence, and audit

- Added `0002_stage3_audit` for parser versions, source identifier assertions, run observations/classifications, unresolved exact-identity conflicts, and run counters.
- Added immutable content-addressed local response storage with atomic writes and integrity verification.
- Added exact DOI/PMID/PMCID/provider identity resolution, separate recent-publication classification, and conservative canonical metadata updates.
- Added page-level SQLAlchemy ingestion with per-run provenance, query matches, conflict audit, and `PaperDiscovered`/`PaperMetadataUpdated` outbox writes in the same transaction.
- Added migration, storage, identity, classification, and ingestion tests; verified live end-to-end one-page ingestion from PubMed and Europe PMC.

## 2026-09-24 — Stage 2 discovery

- Refined the provider contract to retain all raw responses used by a page, the source hit count, and per-record response/source identity.
- Added PubMed ESearch/EFetch and Europe PMC core-search adapters with query translation, pagination, normalization, explicit unsupported filters, and source provenance.
- Added a UI-independent bounded discovery service and shared paced/retrying HTTP client.
- Added focused provider, HTTP, and discovery tests; verified a one-record live search against each source.
- Kept results in memory; durable snapshot storage and cross-run deduplication remain Stage 3.

## 2026-09-24 — Stage 1 foundation

- Added the installable `research_engine` Python package with pure domain models and provider, repository, and snapshot contracts.
- Added an explicit provider registry so source modules can be registered without changing the core.
- Added SQLAlchemy mappings, a SQLite-aware engine factory, and the explicit `0001_initial_schema` Alembic migration.
- Preserved partial publication dates, independent reading and review decisions, query-plan provenance, source snapshot history, and an event outbox in the initial schema.
- Added UTC timestamp conversion, composite audit foreign keys, and archive timestamps to keep the initial schema consistent and traceable.
- Added focused core and SQLite migration/integrity tests. Live providers and a desktop application are not yet implemented.

## 2026-09-24 — Project foundation

- Initialized the Git repository and created the project documentation structure.
- Recorded the approved architecture, source assessment, database proposal, roadmap, and open product decisions.
- Added a Python-oriented `.gitignore` for future local environments, databases, snapshots, and exports.
- No application code or runtime configuration was added.
