# V1 Acceptance Pilot Report

## Current follow-up — 0.1.4 (2026-09-26)

The original failed 0.1.0 incident report below is retained, not overwritten.
Implementation gaps are remediated through migrations `0006`–`0009`. Automated
verification supports a **limited clean-data single-user pilot**, not general
production deployment. Native Windows launch/interaction is still unverified;
the user selected Windows and [the native checklist](docs/WINDOWS_ACCEPTANCE.md)
is ready. Existing affected 0.1.0 databases still require rebuild/audited repair.

| Verification | Result and scope |
| --- | --- |
| Full test suite | 318 passed, 1 skipped in 58.16 seconds. The skipped opt-in PostgreSQL test passed separately against PostgreSQL 16 (1 passed). |
| Fresh package | 0.1.4 wheel and desktop dependencies installed in a new venv without system site packages; `pip check` passed. Module/distribution versions agree. |
| Installed startup | Outside-checkout startup created SQLite and migrated through `0009`; offscreen desktop launcher exited successfully. Not a native Windows test. |
| PostgreSQL 16 | Fresh migration/model parity and populated `0005` upgrade passed. Audited ingestion, source citation evidence, guarded identity-decision replay and scheduled partial-to-completed cursor continuation passed. Temporary isolated localhost server, not shared-worker deployment. |
| Live primary sources | PubMed and Europe PMC returned five observations each for bounded `KEYNOTE-522` searches, with one exact cross-source overlap: nine canonical papers. Repeating both discovered zero additional papers. Runs remained partial at one page, not exhaustive. No key or proxy required; the 403 proxy fallback rehearsal was not exercised. |
| Export | CSV/RIS each included nine project papers. Enriched fields and review/tag values are covered by automated workflow fixtures; this live pilot does not claim native graphical review testing. |
| Stopped-process backup/restore | SQLite backup plus snapshots restored to a new data directory after services closed. Both copies retained 1 project, 1 query, 9 papers/project associations, 4 search histories, 6 snapshot records and 20 observations. All 4 distinct raw snapshot files retained matching relative paths and SHA-256 hashes. |
| Desktop workflow | Offscreen tests cover archive/restore, revision/run/attempt history, filters, recovery, review, tags, retry, export and explicit identity decisions. Physical resizing, DPI, keyboard usability and responsiveness remain for Windows acceptance. |

Installed environment: Python 3.12, SQLAlchemy 2.1.1, Alembic 1.20.0, HTTPX
0.28.1 and PySide6/Addons/Essentials 6.11.2. Evidence is disposable under
`/tmp/rei-live-api-final-xafvcgu3` and `/tmp/rei-restore-014-ov0aj1o7`.
The verified wheel is under `/tmp/research-engine-release.WCHI1P/wheelhouse-fixed/`.
These temporary paths are not deployment or guaranteed permanent storage.

Classification nuance: `newly_published` means publication date within the run's
recent window, not first observed since the preceding run. Known recent papers
can retain that flag on repeat; `newly_discovered` and discovery events stay zero
for their repeated canonical identities. This distinction must not be interpreted
as duplicate discovery or evidence of publication validity.

Recommended next action: execute native Windows acceptance using a clean 0.1.4
data directory and record results before distribution. Keep AI/PDF/analytics and
additional providers deferred; do not repair affected historical identities through
the new non-destructive identity-review workflow.

Date: 2026-09-25. Build: Stage 7 commit `11f3d21`, Python wheel `research-scrapper-engine 0.1.0`. Environment: isolated Linux container, Python 3.12, PySide6 6.11.2, SQLAlchemy 2.1.0, local SQLite. This was an operator-driven, installed-package rehearsal with live PubMed and Europe PMC requests. The data under `/tmp/research-acceptance.ES2uNv` is disposable test evidence, not a user installation.

## Verdict

**Acceptance failed; do not deploy v1 or use its exports for research decisions.** The live run exposed a critical PubMed identifier-parsing defect that silently merged distinct papers into one canonical record. Native graphical startup also remains unverified because this host lacks a usable display stack. No application code was changed during this validation phase.

## Checklist and evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Fresh package installation | Pass | New virtual environment installed the Stage 7 wheel with the `desktop` extra; `pip check` found no broken requirements. |
| Desktop launch and zero-state migrations | Partial | Installed launcher opened with Qt `offscreen`; SQLite database was created and revisions `0001` through `0004` applied. Native Wayland and XCB startup failed in this host; no physical display was tested. |
| Project and saved queries | Pass | PySide6 controller created one project and two `KEYNOTE-522` saved queries, one per provider. |
| Live PubMed search | **Data-integrity failure** | Provider run completed in about 10.05 seconds with 132 observations, but 100 distinct PubMed source IDs were assigned to one canonical paper. |
| Live Europe PMC search | Partial and affected | Provider run was `partial` after the five-page desktop cap, with 499 observations in about 14.83 seconds. Its data was then merged against already-corrupted canonical identities. |
| Ingestion, review, and export | Mechanically passes; content unsafe | UI showed 370 canonical rows; read/important/tag actions persisted; CSV and RIS each contained 370 records. Those records cannot be trusted because of the merge defect. |
| Backup and restore | Pass for fidelity, not correctness | With the app closed, copied the full data directory to a backup and then to a clean restore directory. Installed the wheel into a second fresh virtual environment and opened the restored copy there. Original, backup, and restore had the same project/query/run IDs, 370 papers, 2 query revisions, 629 run observations, 9 snapshots, and 471 outbox events. SQLite integrity and foreign-key checks passed; every snapshot passed content-hash verification. The restored offscreen UI showed 1 project, 2 queries, and 370 rows. Corruption was preserved faithfully. |

## Critical finding: incorrect PubMed identity scope

The PubMed parser in [`src/research_engine/infrastructure/providers/pubmed.py`](src/research_engine/infrastructure/providers/pubmed.py) uses `article.findall(".//ArticleIdList/ArticleId")`. This recursive selector includes identifiers inside a paper's `ReferenceList`, not just identifiers belonging to the paper. In retained raw EFetch XML for PMID `32709714`, the record has 5 direct `PubmedData` article IDs but the recursive selector returns 33 IDs and finds a nested reference list. A live source record consequently asserted cited PMIDs, PMCIDs, and DOIs as its own; the exact-identifier merge then joined unrelated articles. In the pilot database, one canonical article has **100 distinct PubMed provider record IDs** and **235 source records across both providers**. The first PubMed run reported 31 discoveries, 99 metadata updates, and 2 identity conflicts despite starting with an empty article database; those counts are symptoms, not valid discovery metrics. Immutable snapshots retain the evidence but do not repair the canonical rows automatically.

This is a release blocker for paper counts, titles, identifiers, provenance interpretation, review decisions, and CSV/RIS exports. Do not reuse the pilot database as clean research data. A fix must constrain self-identifiers to the record's own top-level `PubmedData/ArticleIdList` and validate it against an EFetch fixture containing nested references. The affected database needs a deliberate rebuild or audited repair from raw snapshots; simply rerunning searches can leave polluted canonical identifiers. Repeat this acceptance pilot from an empty database after the fix.

## Operational review

- Installation: Python wheel and dependencies installed without dependency conflicts. The launcher and migrations worked offscreen. This host's native Wayland connection was unavailable; XCB also failed with Qt's `libxcb-cursor0` prerequisite missing, including under Xvfb. A real target-OS graphical launch remains mandatory.
- Usability: Creating project/query, choosing providers, reviewing, tagging, and exporting worked through the PySide6 controller. The `partial` Europe PMC run was reported in the status bar, but the five-page limit requires clearer operator guidance; a broad query can look like a complete collection in the paper table.
- Performance: The two live provider operations took about 10 and 15 seconds. A 370-row restored project populated the offscreen UI; larger collections and interactive responsiveness were not measured on a physical display.
- Unexpected errors: No provider or UI warning dialog occurred. The critical failure was silent data corruption, found by auditing source IDs per canonical article. Native Qt platform startup failed as described above.
- Missing acceptance input: Target desktop OS/display, expected maximum query volume, and whether partial search results are acceptable for pilot use have not been confirmed. These do not excuse the identity defect.

## Deployment recommendation and next phase

Do not distribute v1 yet. The next phase should be **release-blocking data-integrity remediation**, not a new feature phase: correct PubMed self-identifier parsing, add nested-reference regression coverage and cross-provider identity checks, define a safe rebuild/repair path for affected databases, then rerun the entire live acceptance and backup/restore rehearsal on the fixed build. Separately, perform native graphical acceptance on the intended OS with its required Qt system libraries. Only after these gates pass should a limited single-user pilot be considered. AI, PDF, analytics, and additional providers remain out of scope.

## 0.1.1 remediation follow-up

This report's failed verdict describes the original 0.1.0 build and is retained as incident evidence. The subsequent 0.1.1 fix restricts identifiers to the record's direct `PubmedData/ArticleIdList`, bumps parser provenance, and refuses new ingestion against legacy v1 source records. A fresh offscreen live rerun created 132 PubMed articles from 132 observations, with zero PubMed metadata updates, conflicts, or canonical rows containing multiple PubMed source IDs. Europe PMC ingested 499 observations, ending `partial` at the existing five-page cap. The clean database has 516 canonical papers; CSV/RIS were generated without UI warnings. The affected 0.1.0 pilot database was not repaired or reused for ingestion. See [Data Integrity Remediation](docs/DATA_INTEGRITY_REMEDIATION.md) for handling other affected databases. Native graphical acceptance is still open.
