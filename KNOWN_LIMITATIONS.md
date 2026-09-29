# Known Limitations and Release Blockers

The 2026-09-25 pilot failed on a PubMed false-merge defect. Version 0.1.1 fixes new ingestion, but **v1 is not yet approved for general deployment**: affected databases need a safe rebuild and native target-OS desktop acceptance remains outstanding. See [Acceptance Report](ACCEPTANCE_REPORT.md) and [Data Integrity Remediation](docs/DATA_INTEGRITY_REMEDIATION.md).

## Release blockers

- **Existing-data integrity:** Version 0.1.0 PubMed EFetch parsing recursively treated identifiers in cited references as if they belonged to the current paper. Version 0.1.1 fixes the parser for new runs; it does not repair old canonical metadata, review links, counts, or exports. Existing affected databases require a clean rebuild or case-by-case audited repair; backup/restore preserves the defect.
- **Native Windows desktop unverified:** Windows is now the selected target. Automated/offscreen Linux checks are not native acceptance; follow [the Windows pilot guide](docs/WINDOWS_ACCEPTANCE.md) on a physical target machine.

## Functional and operational boundaries

- Google Sheets sync is optional, one-way, and limited to a dedicated app-managed tab.
  The user must provide and safely keep a service-account JSON key, enable the
  Sheets API, and share the destination Sheet with that account. The adapter and
  migration have automated tests, but live Google permission/write acceptance is
  pending a user-provided Sheet and credentials. Multiple independent sync workers
  or concurrent collaborator edits to the managed tab are not supported.
- The desktop/engine setting defaults to five pages per source; the browser manual-search control initially shows one page. Settings allows 1–100 pages and 1–100 results/page. A cap produces `partial` coverage; tables/exports include only ingested pages.
- Scheduling operates only while a desktop monitor or the browser server started with `--monitor` is running. Coverage checkpoints resume committed pages on later due attempts, with separate full/recent lanes. A crashed running attempt still requires explicit operator recovery; expired provider cursors require a deliberate campaign restart. Partial coverage is not labeled as a provider failure.
- A full backup must include both the SQLite database and local `snapshots/` directory. The rehearsal verified a stopped-process directory copy; online backup, automated restore, and large-dataset recovery were not tested.
- The local SQLite/PySide6 deployment is single-user. Shared PostgreSQL workers, distributed rate limiting, authorization, and remote snapshot storage have not been validated.
- Local list/detail reads and edits are synchronous; very large projects may need pagination or background loading. The 370-row pilot is not a scale test.
- The outbox has no consumer. Notifications, AI, PDFs, analytics, identifier-targeted enrichment, Semantic Scholar and clinical-trial integration are not implemented. Four literature providers are active in 0.1.3.
- Exact identifiers drive deduplication. Long normalized title/year matches create conservative review flags and remain separate; this is not comprehensive fuzzy matching. Desktop identity review records reasoned decisions, never historical consolidation. Selecting a candidate applies only to subsequent matching assertions and does not transfer source/identifier ownership. Legacy articles gain candidate keys only on refresh. No authenticated reviewer identity exists in this single-user release.
- Proxy routing is manual/global for all four providers; no per-provider route or automatic HTTP-403 fallback. Credentials in proxy URLs are rejected; failures do not silently switch routes.
- API keys entered in Settings last only for this session; environment variables are supported. No durable secure credential store is implemented. Settings files are saved atomically individually, not as a cross-file transaction.
- Old canonical fields have unknown provenance until reasserted. Selection uses one configurable source ranking applied per field, not a provider-by-field authority matrix. Blank source fields do not delete canonical metadata.
- Provider verification covers fixtures and bounded live checks rather than exhaustive API coverage. Crossref Boolean/quoted queries now filter available title/abstract/subject metadata from candidate pages; missing fields, ranked retrieval and page budgets affect recall. Plain keyword search remains broad. A Chromium desktop/mobile web pilot passed; native Windows acceptance remains outstanding. See [Boolean semantics](docs/BOOLEAN_SEARCH.md).
- Downgrades that remove fields/review tables are not a safe recovery procedure; restore a complete stopped-process backup. SQLite native DROP COLUMN requires SQLite >=3.35. PostgreSQL 16 migrations through `0009`, a populated `0005` upgrade, audited ingestion/review and scheduled continuation passed isolated live checks; this does not validate multi-worker production deployment.
- Manual-query overlap checks are best-effort for a single-user/local process, not a distributed lease. Recovery never automatically expires locks; it relies on an operator confirming the old worker stopped. Unknown/expired provider cursors require an explicit coverage restart.
- Snapshot/assertion/citation/outbox history grows without automated retention. Full/recent lanes have independent page budgets; mutable indexes and PubMed's 10,000-result cap prevent a guarantee of exhaustive or atomic coverage. Narrow broad queries/date ranges when required.
- Provider publication stages, types and retraction evidence are different taxonomies. Source-specific statuses remain visible; a selected canonical label or absent retraction flag is not a clinical-validity judgment.
- `newly_published` classifies an observed paper's date within a recent window, not publication since the previous run; repeat known papers can retain that flag. New canonical discovery is counted separately and does not repeat.
