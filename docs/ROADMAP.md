# Roadmap

## Completed

- Added optional one-way Google Sheets sync after scheduled searches or manual
  browser action, with project links, last-success status and migration `0010`.
  Live access still needs a user-provided service-account file and Sheet share.
- Closed the first six priority gaps in 0.1.4: monitoring continuation, recovery,
  rejected-attempt auditing, source metadata/citation evidence, explicit identity
  review and desktop history/archive/filter/recovery controls.
- Verified migrations through `0009` on SQLite/PostgreSQL, including retained-data
  upgrades and PostgreSQL ingestion/review/scheduled continuation.

- Added 0.1.3 OpenAlex/Crossref discovery, source catalog, per-query choices,
  independent monitoring allow-list, workflow/API/network settings, multi-source
  failure isolation and field-level canonical provenance migration.

- Defined the medical literature monitoring vision and version 1 boundary.
- Evaluated PubMed, Europe PMC, OpenAlex, Crossref, Semantic Scholar, ClinicalTrials.gov, and additional sources.
- Approved the modular-monolith direction, reusable core, provider modules, snapshots, event design, and Research Project concept.
- Created the documentation and Git foundation (this phase).
- Implemented Stage 1 core contracts, provider registry, initial schema, migration, and focused tests.
- Implemented Stage 2 PubMed and Europe PMC discovery adapters, normalized paged results, raw-response provenance, and a UI-independent discovery service.
- Implemented Stage 3 durable raw snapshots, exact identifier resolution, per-run observations, canonical metadata persistence, and transactional discovery/update events.
- Implemented Stage 4 project/query workflows, revision-bound search history, project-specific paper review and tags, and automatic query-to-project paper linking.
- Implemented Stage 5 PySide6 client for the essential manual research workflow using application services and background search execution.
- Implemented Stage 6 project CSV/RIS exports, durable per-query schedules and attempts, and a local background monitoring host.
- Completed Stage 7 wheel packaging, fresh/upgrade migration and PostgreSQL checks, guarded recovery, safe logs, and installation guidance.
- Ran a live acceptance pilot and found a release-blocking PubMed identifier-scope defect; see [Acceptance Report](../ACCEPTANCE_REPORT.md).
- Corrected PubMed identifier scope in 0.1.1 and validated a fresh live rerun; affected 0.1.0 databases still need rebuild or audited repair.
- Added an opt-in, persisted desktop proxy setting in 0.1.2 for both medical literature providers without coupling it to the UI.

## Current phase

**Documentation and acceptance hardening; final deployment acceptance remains open.** The original pilot database/exports remain unsafe. A clean rerun passed PubMed identity checks, while native Windows graphical acceptance and old-data handling remain. Optional Google Sheets sync has automated coverage but awaits a live permission/write check. Details are in [Development Status](../DEVELOPMENT_STATUS.md), [Acceptance Report](../ACCEPTANCE_REPORT.md), [Data Integrity Remediation](DATA_INTEGRITY_REMEDIATION.md), and [Known Limitations](../KNOWN_LIMITATIONS.md).

## Implementation stages

| Stage | Deliverable | Completion check |
| --- | --- | --- |
| 1. Foundation | Domain models, provider/repository/snapshot contracts, development setup, SQLite schema and Alembic migrations. | Core can be imported without GUI or persistence adapter dependencies; a fresh database can migrate. |
| 2. Discovery | PubMed and Europe PMC modules, query translation, pagination, rate handling, bounded query execution. | Both providers return normalized pages and raw evidence; unsupported filters, HTTP failures, and pagination caps are explicit. |
| 3. Audit and merge | Canonical metadata, exact-identifier matching, provenance, immutable snapshots, search history, transactional outbox. | Re-running a query detects new papers without duplicate identities; each stored record is traceable to a response and parser version. |
| 4. Research Projects | Project queries, linked papers, tags, and project-specific review status. | One paper can belong to multiple projects with independent status. |
| 5. Desktop client | Project and query management, results, details, manual refresh, visible run errors. | A researcher can complete the version 1 workflow through PySide6. |
| 6. Monitoring and export | Local scheduling and CSV/RIS export. | New papers appear after an eligible run; selected project results export accurately. |
| 7. Release checks | Relevant tests, upgrade migration check, packaging and restore guidance. | A clean installation and upgrade preserve projects, articles, and snapshots. Completed in isolated test environments; target-machine acceptance remains. |

## Explicitly outside version 1

PDF download, AI analysis, citation-based ranking, notifications, trend analytics, trial monitoring, multi-user collaboration, and a hosted REST API are later possibilities. Architectural contracts allow these without promising them in the first release.

## Next action

Complete native Windows desktop acceptance using the documented isolated pilot.
Confirm whether any affected 0.1.0 databases need a clean rebuild or audited
repair. Validate the optional Google Sheets integration on a user-authorized
spreadsheet before claiming live readiness. Hosted/multi-user deployment, AI,
PDFs, and analytics remain outside this release.
