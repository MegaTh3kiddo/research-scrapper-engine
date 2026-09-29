# Local browser client

The optional FastAPI client uses the same `create_application_services` composition
as the desktop and recovery CLI. It never imports PySide6. No **manual** migration
step is needed: startup applies the migration chain, including `0010` for optional
project-to-Google-Sheet links.

For first-time setup and the daily research workflow, see the [User guide](USER_GUIDE.md), also available through **Help / Guide** in the browser. Review edits, tags, exports, and settings currently use the desktop client.

Optional per-project [Google Sheets sync](GOOGLE_SHEETS.md) adds a browser connection
and manual Sync button. Install `.[web,sheets]` and supply a local service-account
JSON file to use it; automatic sync runs after eligible scheduled searches.

## Linux / macOS quick start

For the Linux shell shown in the repository prompt, run:

```bash
.venv/bin/python -m pip install -e '.[web]'
.venv/bin/python -m research_engine.web.main --monitor
```

Create the environment first with `python3 -m venv .venv` if it does not exist.

## Windows quick start

From the repository folder, with Python 3.11 or newer:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[web]"
.\.venv\Scripts\research-engine-web.exe --monitor
```

If `.venv` already exists, skip its creation. Open **http://127.0.0.1:8000**.
On Linux/macOS use `.venv/bin/python` and `.venv/bin/research-engine-web` instead.
An alternative launcher is `python -m research_engine.web.main --monitor`.

The default directory is the same as the desktop (`~/.research-scrapper-engine`,
or `RESEARCH_ENGINE_DATA_DIR`). For an isolated trial:

```powershell
.\.venv\Scripts\research-engine-web.exe --data-dir .\web-pilot --port 8001 --monitor
```

`DATABASE_URL` still overrides the database selection, even with `--data-dir`.
Stop with Ctrl+C; normal shutdown waits for active searches before closing storage.

## Workflow

1. Create or select a project. Existing projects and saved queries are visible.
2. Save a query with selected providers. Saved discovery/monitoring settings still
   govern permitted sources; disabled discovery sources produce audited rejections.
3. Select a query and run a bounded search. Inspect source results in Jobs, provider
   runs, failed pre-run attempts, canonical papers and source provenance.
4. Enable a schedule and set its interval (new schedules default to 60 minutes,
   or hourly source API searches; existing schedules retain their interval).
   `--monitor` checks due schedules every
   60 seconds while this server is running (`--poll-seconds` can change this).
   Without that flag, schedules do not run automatically. The due-schedules button
   executes only schedules that are already due, across all projects.
5. The page refreshes jobs, papers and history every minute; **Refresh now** checks
   immediately, including after an active search finishes. Browser refresh reads
   local results; source APIs run on due schedules or manual searches. Recent activity scrolls within
   a maximum height of `100vh`. Inspect scheduled
   execution status and full/recent coverage separately; partial is not exhaustive.

The responsive dashboard now includes a multiline Boolean editor, operator/phrase
insertion buttons and a live server preview with syntax positions and per-source
translations. Saving is enabled after validation. **Edit query** updates the selected
query through the shared application service, preserving filters and execution history;
**Save as new query** creates another saved query. See [Boolean syntax and source
behavior](BOOLEAN_SEARCH.md), especially Crossref's metadata filtering.

API requests reuse provider credentials/proxy configuration from the existing
settings stores and environment. This minimal client does not edit those settings.
Use the desktop Settings interface to configure proxy/workflow settings before
launching it. No new API key is required by the web layer itself.

## Boundaries and limitations

- Local single-user only: loopback binding, Host allowlist, random per-process
  mutation token, same-origin checks, no CORS and safe text rendering. These are
  local-browser safeguards, **not authentication or multi-user authorization**.
  Do not expose it on a LAN, through a public tunnel or reverse proxy.
- One server process and one long workflow at a time. Jobs return HTTP 202;
  overlapping web searches/monitoring return 409. Do not run the desktop scheduler
  or another worker against the same data concurrently.
- Jobs retain the last 20 transport results in memory, lost on restart. Durable
  provider runs, attempts, snapshots, schedules and coverage remain in the database.
  An all-failed provider batch can finish its transport job successfully: inspect
  its source error codes and persistent failed run states rather than job completion
  alone. A hard process kill uses the existing explicit recovery CLI.
- No web archive/review/tag/export/recovery UI yet; those workflows remain
  available through the desktop and application services. Query creation here uses
  default filters; existing queries retain their filters.
- Paper/run collections are currently unpaginated; attempts are capped at the
  existing newest-200 service default. One-minute browser polling is intended for
  modest local collections, not large-scale analytics or production hosting.
- Tests exercise HTTP/SQLite workflows and deterministic provider fixtures. A
  native Windows browser interaction pilot remains necessary.

Earlier browser verification (2026-09-26): 359 tests passed, 1 optional PostgreSQL
test skipped. Tests covered
Boolean precedence/validation, native translation, Crossref branch/empty-page/retry
audit and deduplication, plus browser-service preview/edit and existing workflows.
The loopback launcher created a fresh database and served HTML/status successfully;
the wheel includes the browser assets. A follow-up Chromium browser pilot passed
project creation, all-provider Boolean previews, invalid syntax/save blocking, saved
query editing and schedule configuration at desktop/mobile sizes, without page errors
or mobile horizontal overflow. Native Windows browser interaction remains outstanding.

Architecture: browser → thin HTTP routes → shared project/search/monitoring/recovery
services → existing providers, audited ingestion and storage. Blocking workflow
execution is isolated in threads; providers are created and closed in their owning
worker event loop. FastAPI resources are managed through
[lifespan](https://fastapi.tiangolo.com/advanced/events/).
