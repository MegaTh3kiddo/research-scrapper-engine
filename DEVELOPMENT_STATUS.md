# Development Status

Last reviewed: **2026-09-29**. The package version is `0.1.4`; the current working tree also contains the optional Google Sheets integration and its `0010` migration. This file describes the present state. Dated implementation history remains in the [Changelog](docs/CHANGELOG.md), [Roadmap](docs/ROADMAP.md), and [Acceptance Report](ACCEPTANCE_REPORT.md).

## Current stage

The modular-monolith core, four literature providers, audited storage, Research Projects, desktop client, local browser client, scheduling, and CSV/RIS export are implemented. The browser also has optional one-way Google Sheets sync. This is still a **local, single-user development release**, not a validated general or clinical deployment.

The latest local full suite passed **373 tests**, with **one optional PostgreSQL test skipped**. Tests cover fresh SQLite startup and `0009`→`0010` upgrade, browser/application workflows, provider fixtures, migration paths, and mocked Google Sheets requests. A wheel build includes the integration and migration. This does not prove live Google permissions, native Windows graphical behavior, or distributed PostgreSQL deployment.

## Approved architecture and scope

- Core/domain and application services are independent of PySide6, FastAPI, and a specific database.
- PubMed, Europe PMC, OpenAlex, and Crossref sit behind provider contracts; raw responses and source provenance remain auditable.
- Canonical papers are shared; queries, review state, and tags belong to Research Projects. Uncertain identities are flagged, not silently merged.
- SQLite is the local default. PostgreSQL has isolated schema/integration checks but is not a supported shared-worker setup.
- The event outbox records domain events. It has no notification/AI/analytics consumer yet.
- Browser and desktop are clients of the same application services. The browser is local-only and has no accounts or public-hosting authorization.

## Current workflows

| Workflow | Status |
| --- | --- |
| Save/preview Boolean queries, run manual searches, inspect papers and history | Browser and desktop |
| Schedule source searches | Both clients; new schedules default to 60 minutes; monitoring requires a running host |
| Review status, project tags, CSV/RIS export, source/proxy settings, recovery, identity review | Desktop client |
| Google Sheets project projection | Optional browser setup and manual Sync; opt-in after eligible scheduled searches |
| Raw snapshots, search/query history, migrations, last-success status | Durable local storage |

The browser refreshes local activity/history every minute; this **does not** call literature APIs. The server checks for due schedules every minute by default; only due enabled queries perform scheduled source searches. Existing saved schedule intervals and existing settings are not changed by the new default.

## Acceptance and limitations

1. The 2026-09-25 pilot found a **PubMed `0.1.0` false-merge defect**. The parser fix protects new ingestion, but old affected databases/exports cannot be trusted without rebuild or audited repair. See [Data Integrity Remediation](docs/DATA_INTEGRITY_REMEDIATION.md).
2. Native Windows physical-display acceptance is still pending. Linux/offscreen UI tests and packaged-launch checks do not replace it. Follow the [Windows checklist](docs/WINDOWS_ACCEPTANCE.md).
3. A real Google Sheet has not been written in this workspace because no user Sheet URL or service-account file path was supplied. Setup and constraints are in [Google Sheets](docs/GOOGLE_SHEETS.md).
4. Very large collections, concurrent independent workers, hosted multi-user use, online backup, and exhaustive systematic-review coverage are not validated. See [Known Limitations](KNOWN_LIMITATIONS.md).

## Not implemented

No AI summaries, PDF processing/viewer, trend analytics, push/email alerts, clinical-trial monitoring, browser review/tag editing, hosted accounts, or distributed worker coordination. Google Sheets is a one-way projection, not an import or backup. The outbox has no consumer.

## Next action

The README, documentation index, contribution/security guides, issue/PR templates,
MIT license, and publishing checklist are prepared locally. Before a public push,
review staged files **and Git history** for secrets or private infrastructure data;
the original local proxy address still exists in earlier commits. Run a fresh
installation check and do not publish workspace data. For release readiness,
complete native Windows acceptance, decide how to handle any `0.1.0` data, and
perform a live Google Sheet permission/write check only after the owner shares an
authorized Sheet with the service account. Fixture tests alone do not establish
production or clinical readiness.
