# Research Scrapper Engine

A local, single-user workspace for discovering medical literature, tracking what is new, and keeping the evidence behind each paper. It uses documented scientific APIs—not Google Scholar page scraping. The repository keeps its original `scrapper` spelling; the Python package is `research_engine`.

The reusable Python engine is independent of the desktop and browser interfaces. PubMed, Europe PMC, OpenAlex, and Crossref are separate provider modules. SQLite is the default local database; PostgreSQL is an integration target, not a supported multi-user deployment.

> **Release status:** This is an installable development release (`0.1.4`), not yet cleared for general/clinical deployment. Native Windows graphical acceptance and live Google Sheets permission testing remain open. Databases populated by version `0.1.0` may contain false PubMed identifier merges; upgrading does **not** repair them. Read [Known Limitations](KNOWN_LIMITATIONS.md) and [Data Integrity Remediation](docs/DATA_INTEGRITY_REMEDIATION.md) before using old data.

## What it does

- Save Boolean searches with `AND`, `OR`, `NOT`, parentheses, and quoted phrases. Preview source-specific translations before saving.
- Search PubMed, Europe PMC, OpenAlex, and Crossref; retain raw source snapshots, source identifiers, query revisions, and run history.
- Identify new canonical papers, repeat sightings, metadata updates, and possible identity conflicts without silently merging uncertain matches.
- Organize shared papers into research projects with project-specific review status and tags.
- Run manual searches or schedule monitored searches. New schedules default to hourly source searches; the local monitor checks for due work every minute while running.
- Export project results to CSV/RIS from the desktop client. Optionally project paper rows into a user-shared Google Sheet from the browser.

The browser provides projects, queries, manual searches, paper inspection, monitoring history, schedules, and optional Google Sheets sync. The desktop client also provides review/tag edits, CSV/RIS export, settings, recovery, and identity-review controls. The browser **does not** yet edit review status or export CSV/RIS. Neither client downloads PDFs or performs AI analysis.

## Quick start

Use Python 3.11 or newer from the repository root. Choose **one** operating-system block. The app creates/migrates its local database when it starts; you do not need to run Alembic separately.

Linux/macOS (Bash), browser:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[web]'
.venv/bin/python -m research_engine.web.main --monitor
```

Windows (PowerShell), browser:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[web]"
.\.venv\Scripts\python.exe -m research_engine.web.main --monitor
```

Open <http://127.0.0.1:8000>. Keep that terminal running. `--monitor` enables due-query polling; leave it off for manual-only use. Use `Ctrl+C` to stop. Start with a project, save a query, run a small manual search, and inspect its provider outcomes. The [User Guide](docs/USER_GUIDE.md) walks through first-time and ongoing workflows.

For the desktop client, stop the browser server if both would monitor the same database, then install and launch the desktop extra:

| Shell | Install | Launch |
| --- | --- | --- |
| Bash | `.venv/bin/python -m pip install -e '.[desktop]'` | `.venv/bin/research-engine-desktop` |
| PowerShell | `.\.venv\Scripts\python.exe -m pip install -e ".[desktop]"` | `.\.venv\Scripts\research-engine-desktop.exe` |

The desktop needs a graphical display; Linux offscreen tests do not establish native Windows acceptance. For an installed wheel, use the wheel path with the desired extra instead of `-e .`. See [Installation](docs/INSTALLATION.md) and [Windows Acceptance](docs/WINDOWS_ACCEPTANCE.md).

## Data, access, and safety

By default, local data lives in `~/.research-scrapper-engine` (SQLite database, immutable source snapshots, settings, and logs). Set `RESEARCH_ENGINE_DATA_DIR` or pass `--data-dir` to the browser launcher for another local workspace. If `DATABASE_URL` is set, it overrides the database location even when `--data-dir` is provided. Stop clients/workers before copying the full data directory for a backup; CSV/RIS exports are not backups.

API keys for PubMed/OpenAlex are optional in the supported access modes; the app may need a contact email or keys when a provider enforces a limit. Use desktop Settings or the documented environment variables. A proxy is opt-in; HTTP 403 does not automatically activate it. The local web server binds to `127.0.0.1` and has no user accounts or remote-hosting authorization. **Do not expose it publicly or put it behind a public tunnel.**

Google Sheets is optional: install `.[web,sheets]`, provide a **local** service-account JSON path, and share only the destination Sheet with that account as Editor. The key never belongs in the browser, a Git commit, or a public issue. See [Google Sheets setup](docs/GOOGLE_SHEETS.md). No Google API key is needed for that integration.

This software collects literature metadata; it does not validate clinical claims or provide medical advice. Source coverage, search semantics, and citation fields differ. A completed bounded run is not proof that a search was exhaustive; inspect partial/failed outcomes and [provider limitations](docs/API_SOURCES.md).

## Architecture at a glance

```text
PySide6 desktop / local FastAPI browser
                 ↓
       Application services
                 ↓
   Reusable research engine + provider ports
                 ↓
 PubMed / Europe PMC / OpenAlex / Crossref
                 ↓
 SQLite or PostgreSQL + immutable snapshots
```

Projects own queries and project-paper review state; canonical papers are shared across projects. Provider observations, original identifiers, and response snapshots preserve provenance. The event outbox records events but does not send notifications in this release. See [Architecture](docs/ARCHITECTURE.md) and [Database Design](docs/DATABASE_DESIGN.md).

## Documentation

Start at the [Documentation Index](docs/INDEX.md). Common destinations:

| Need | Document |
| --- | --- |
| First-time setup and research workflow | [User Guide](docs/USER_GUIDE.md) |
| Install, upgrade, backup, and recovery | [Installation](docs/INSTALLATION.md) |
| Browser-specific setup and boundaries | [Web Client](docs/WEB_CLIENT.md) |
| Boolean syntax and provider behavior | [Boolean Search](docs/BOOLEAN_SEARCH.md) |
| Google Sheets connection and sync | [Google Sheets](docs/GOOGLE_SHEETS.md) |
| Current status and open gates | [Development Status](DEVELOPMENT_STATUS.md), [Known Limitations](KNOWN_LIMITATIONS.md) |
| Original purpose, decisions, and progress | [Project Vision](docs/PROJECT_VISION.md), [Decisions](docs/DECISIONS.md), [Roadmap](docs/ROADMAP.md), [Changelog](docs/CHANGELOG.md) |

## Development and contributions

See [Contributing](CONTRIBUTING.md) for a clean setup, tests, documentation conventions, and pull-request expectations. The full suite is run with `.venv/bin/python -m pytest -q` on Bash or `.\.venv\Scripts\python.exe -m pytest -q` in PowerShell after installing `.[dev,desktop,web,sheets]`. The last local run passed **373 tests**, with **one optional PostgreSQL test skipped**; it used mocked Google calls, not a live Sheet. The optional PostgreSQL check needs an explicitly disposable `TEST_POSTGRES_URL`.

Please use [Security](SECURITY.md) for vulnerability reporting and never post API keys, service-account JSON, proxy credentials, patient information, or local database dumps in public issues.
Before a public push, follow the [GitHub Publishing Checklist](docs/PUBLISHING_CHECKLIST.md); it includes a separate review of committed history.

## License

MIT — see [LICENSE](LICENSE).
