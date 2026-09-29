# Documentation index

Use this page to find the current instructions without mistaking an earlier pilot or design proposal for release guidance. The [README](../README.md) is the public entry point. [Development Status](../DEVELOPMENT_STATUS.md) and [Known Limitations](../KNOWN_LIMITATIONS.md) state what is verified today; versioned findings remain in the historical records below.

## Start here

| Task | Current guide |
| --- | --- |
| Install and try the browser or desktop | [Installation](INSTALLATION.md) and [User Guide](USER_GUIDE.md) |
| Set up a local browser session | [Web Client](WEB_CLIENT.md) |
| Learn Boolean query syntax and source differences | [Boolean Search](BOOLEAN_SEARCH.md) |
| Choose literature sources, API keys, and proxy | [Settings](SETTINGS.md) and [API Sources](API_SOURCES.md) |
| Connect a Google Sheet | [Google Sheets](GOOGLE_SHEETS.md) |
| Run the physical Windows acceptance checklist | [Windows Acceptance](WINDOWS_ACCEPTANCE.md) |
| Restore/recover after an interrupted run | [Installation and Recovery](INSTALLATION.md) |

## Understand the system

| Topic | Document |
| --- | --- |
| Original motivation and future possibilities | [Project Vision](PROJECT_VISION.md) |
| Components, UI boundaries, provider ports, and data flow | [Architecture](ARCHITECTURE.md) |
| Records, links, source evidence, migrations | [Database Design](DATABASE_DESIGN.md) |
| Source selection and workflow settings rationale | [Source/Settings Design](SOURCE_SETTINGS_DESIGN.md) |
| Decisions and alternatives | [Decisions](DECISIONS.md) |
| Delivered stages and remaining gates | [Roadmap](ROADMAP.md) |
| Dated changes | [Changelog](CHANGELOG.md) |

## Release evidence and historical reports

- [Development Status](../DEVELOPMENT_STATUS.md) — current implementation and validation snapshot.
- [Known Limitations](../KNOWN_LIMITATIONS.md) — deployment blockers and supported boundaries.
- [Acceptance Report](../ACCEPTANCE_REPORT.md) — dated pilot evidence, not an evergreen install guide.
- [Data Integrity Remediation](DATA_INTEGRITY_REMEDIATION.md) — mandatory reading for databases populated by version `0.1.0`.
- [Gap Remediation](GAP_REMEDIATION.md) — historical plan and closure evidence for earlier architecture gaps.

## For contributors

- [Contributing](../CONTRIBUTING.md) — development setup, tests, change discipline, and pull requests.
- [Security](../SECURITY.md) — handling vulnerabilities and secrets.
- [License](../LICENSE) — MIT.
- [GitHub publishing checklist](PUBLISHING_CHECKLIST.md) — privacy, packaging, release claims, and repository settings to check before a public push.

The browser's **Help / Guide** section includes the two main user flows. It is packaged with the Python wheel; this index is for the repository. When changing behavior, update the current guide and the browser guide if both describe it, then record the change in the changelog/status documents.
