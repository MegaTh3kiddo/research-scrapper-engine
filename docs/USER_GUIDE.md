# User guide

Research Engine collects medical literature into research projects, keeps the original source responses for audit, and tracks searches over time. This guide covers the current local, single-user release—not planned features.

The browser has a **Help / Guide** section with the same two user flows below. For detailed search syntax, see [Boolean search](BOOLEAN_SEARCH.md); for installation, migrations, and recovery details, see [Installation](INSTALLATION.md).

## Which interface should I use?

| Activity | Browser | Desktop |
| --- | --- | --- |
| Create a project, save/edit queries, run searches | Yes | Yes |
| Inspect papers, source provenance, and execution history | Yes | Yes |
| Configure query schedules | Yes | Yes |
| Change review status, manage project tags | View existing values only | Yes |
| Export CSV/RIS | Not yet | Yes |
| Sync project papers to Google Sheets | Yes, optional integration | Not yet |
| Configure sources, credentials, proxy, and workflow defaults | Not yet | Settings |
| Recovery and identity-conflict decisions | Inspect history only | Yes |

Both clients use the same application services and can use the same local data directory. They do not automatically sync between computers. Stop one client before switching to the other when using scheduled monitoring against the same database.

## Flow 1 — First-Time Setup

### 1. Install and start the browser client

Open a terminal in the repository folder. Use the commands for your operating system. If `.venv` already exists, skip its creation.

Linux or macOS (Bash):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[web]'
.venv/bin/python -m research_engine.web.main --monitor
```

Windows (PowerShell):

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[web]"
.\.venv\Scripts\python.exe -m research_engine.web.main --monitor
```

Open **http://127.0.0.1:8000**. Keep the terminal running. Stop the server with `Ctrl+C` when finished; allow active work to finish shutting down. Windows paths such as `.\.venv\Scripts` are not Bash commands: do not paste the Windows commands into a Linux terminal.

The application creates the database and applies migrations on startup. By default, data lives in `.research-scrapper-engine` inside your home directory. For an isolated trial, append `--data-dir ./web-pilot` to the launch command. An existing `DATABASE_URL` environment variable still overrides the database location; unset it when testing with a disposable directory. You can change the port with `--port 8001` and open the matching browser address.

If you already have data collected with version 0.1.0, do not rely on it without rebuilding or auditing it: upgrading does not repair historical PubMed identity errors. Follow [Data integrity remediation](DATA_INTEGRITY_REMEDIATION.md).

This release is intended for a local, single-user workstation. Keep the server bound to its default localhost address; it has no account/login system for public deployment.

### 2. Choose sources and connection settings

PubMed and Europe PMC are the initial choices. OpenAlex and Crossref are also supported. Browser source checkboxes choose which sources belong to a query, but sources must also be allowed by saved discovery settings.

For settings or desktop-only actions, install the desktop extra and launch the desktop client after stopping the web server:

Linux/macOS:

```bash
.venv/bin/python -m pip install -e '.[desktop]'
.venv/bin/research-engine-desktop
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[desktop]"
.\.venv\Scripts\research-engine-desktop.exe
```

In desktop **Settings**, use:

- **Sources:** select new-query source defaults and which sources may run during monitoring. Edit existing queries separately to change their saved sources.
- **API access:** configure contact email and optional keys. `NCBI_API_KEY` and `OPENALEX_API_KEY` environment variables supply keys to the web process. Desktop session keys are not stored on disk or shared with a separate web process. Do not put keys in queries.
- **Network:** enable a reachable proxy and enter its URL if needed, or leave it disabled for direct requests. HTTP, SOCKS5, and SOCKS5H URLs are supported; embedded proxy credentials are not. A proxy is not enabled automatically after a 403 error.
- **Workflow:** choose page budgets, result counts, default monitoring interval, and whether exports include excluded papers.

Restart the web client after changing settings. Use the same data directory for both clients if you want the same workspace. If the browser used `--data-dir ./web-pilot`, set `RESEARCH_ENGINE_DATA_DIR` to that directory before starting the desktop client: `export RESEARCH_ENGINE_DATA_DIR="$PWD/web-pilot"` in Bash, or `$env:RESEARCH_ENGINE_DATA_DIR = "$PWD/web-pilot"` in PowerShell. Leave it unset for the default home-directory workspace. Check `DATABASE_URL` as well if set.

If a provider reports authentication or quota errors, check its credentials and current service requirements before retrying. See [Settings](SETTINGS.md) for more configuration details.

### 3. Create a research project

Create and select a project in **Project workspace**, for example “Breast cancer immunotherapy.” A project is your workspace for saved queries and collected papers. You can keep separate projects for different research questions.

### 4. Build and save a query

Enter an expression, select sources, and inspect **Expression preview**. For example:

```text
("breast cancer" OR "triple negative breast cancer") AND immunotherapy NOT mice
```

Wait for a valid preview, then save the query. The preview shows each source's translation and any limitations. **Save as new query** creates another saved query. To modify an existing one, select it, choose **Edit query**, and then **Save changes**.

Typing into the expression editor does not change the saved query until you save. Searches and schedules operate on the selected saved query, not unsaved text. Existing query filters are retained when editing in the browser; date and other advanced filters are configured in the desktop query editor.

### 5. Run a small manual search

Select the saved query and use **Run search**. Begin with a small **Pages per source** budget to check relevance and connection settings. Each source may return a different number of results.

Inspect **Recent activity**, **Query history & monitoring**, and the paper library. Open a paper title for its abstract, identifiers, source information, and metadata provenance. **Open publication page** opens the provider/publisher page; access may depend on that site. The application does not download PDFs.

A successful bounded search is not a promise that all matching literature has been collected. Check for partial or failed source runs before interpreting a small result count.

### 6. Set up monitoring if wanted

Select the saved query, enable its schedule, set the interval in minutes, and choose **Save schedule**. New schedules default to **60 minutes (1 hour)** between source API searches. Existing schedules keep their stored intervals; change them to 60 and save if you want hourly searches. Start the web server with `--monitor` to poll schedules automatically. Both the query schedule and its allowed monitoring sources must be enabled.

The next run becomes due after the saved interval. **Run due** executes due queries across projects; it does not force every query to run immediately. Use **Run search** for an immediate manual search.

The server must remain running. Closing the browser tab does not stop monitoring; stopping the server does. Without `--monitor`, automatic polling is paused even if query schedules are saved.

## Flow 2 — Ongoing Workflow

### 1. Refine queries using Boolean expressions

- **AND** requires both terms: `cancer AND immunotherapy`.
- **OR** accepts alternatives: `Alzheimer OR dementia`.
- **NOT** excludes a term: `cancer NOT mice`.
- **Parentheses** group alternatives: `(Alzheimer OR dementia) AND "artificial intelligence"`.
- **Double quotes** keep a phrase together: `"breast cancer"`.

Operators are case-insensitive outside quotes. Precedence is **NOT**, then **AND**, then **OR**. Adjacent words imply AND, so `breast cancer` means `breast AND cancer`; use quotes if you intend a phrase. `NOT alpha beta` means `(NOT alpha) AND beta`, not a negated phrase. Use `NOT "alpha beta"` or `NOT (alpha OR beta)` when that is your intent.

Check the preview after every edit. An incomplete operator, quote, or parenthesis prevents saving. Provider-specific field tags, wildcards, and proximity expressions are not part of the portable syntax.

PubMed, Europe PMC, and OpenAlex receive source-specific Boolean translations. Crossref Boolean searches use candidate retrieval followed by filtering available title, abstract, and subject metadata. Missing metadata and the page budget can reduce recall, especially for NOT expressions. Providers index different content, so equivalent expressions need not return identical papers. Prefer a positive topic clause over a very broad negative-only query. See [Boolean search](BOOLEAN_SEARCH.md) for exact rules and source caveats.

### 2. Understand projects, papers, and duplicates

The structure is:

```text
Project
├── Saved queries → revisions → execution attempts and provider runs
└── Project-paper links → review state and project tags
                           ↓
                 Shared canonical paper
                 └── Source identifiers, observations, and raw snapshots
```

One canonical paper can belong to multiple projects. Each project keeps its own review state and tags. Finding the same paper through another query does not create another copy in that project's library, but the new search/source evidence is retained.

Identity resolution uses DOI, PMID, PMCID, and provider identifiers first. Source identifiers are preserved. Uncertain title/year matches and conflicting identifiers are flagged rather than silently merged. Use desktop **Identity review…** for available decisions; do not assume similar titles prove two records are the same paper.

These outcomes mean different things:

- **Newly discovered:** a canonical paper first encountered by this installation. It may be an older publication.
- **Newly published:** an observation falls within the configured publication-recency window. It can also refer to an already known paper.
- **Updated metadata:** a known paper's normalized metadata changed; it is not another new paper.
- **Seen again:** another source/run found an existing paper; its provenance and observation history remain useful.

“New” run counts refer to newly discovered canonical records, not necessarily everything newly visible in the selected project. Citation counts remain source-specific; they are not added together. A zero-new run can still find known papers or update metadata.

### 3. Check monitoring and execution history

The browser automatically refreshes activity, papers, and history once per minute; **Refresh now** checks immediately, including after a running search finishes. Recent activity scrolls within a maximum height of one viewport (`100vh`). Browser refresh reads local application results, not literature APIs. The server checks for due schedules every minute by default; literature APIs are searched only for due enabled queries (new schedules default to hourly) or when you run a manual search. Review provider outcomes, execution attempts, scheduled executions, and coverage checkpoints rather than only the latest activity message.

- **Completed:** that recorded execution finished within its scope.
- **Partial:** some work or coverage remains, for example because the page budget was reached or a source failed while another succeeded.
- **Failed:** inspect the issue and successful source runs before retrying; failure is not evidence of zero matching papers.
- **Running:** work has not finished. Wait before starting overlapping work.
- **Skipped:** an attempt was not executed; inspect its reason.

Monitoring combines recent-publication checks with continuing full-query coverage. Saved cursors let bounded campaigns continue on later scheduled runs; overlapping recent windows help revisit late-indexed or changed records. Your saved date filters still restrict the search. Source limits and changing indexes mean monitoring is not a guarantee of exhaustive systematic-review coverage.

The application prevents overlapping executions of the same query within its supported workflow. Do not run multiple scheduler processes against the same workspace. After a crash, stop any remaining workers before using desktop **Recovery…**. Interrupted work is retained for audit; recovery is explicit, not an automatic promise that every interrupted attempt resumed. Recent browser activity jobs are temporary, while stored run history survives restarts.

There are no email/push notifications in this release. Monitoring results appear in the workspace and history.

### 4. Review and organize papers

Use the desktop client to change review state and tags. The browser displays these values but currently does not edit them.

- **Mark read:** marks the paper read and clears an important/excluded decision.
- **Mark important:** flags the paper as important without automatically marking it read; an existing read timestamp is retained.
- **Exclude:** keeps the record and audit evidence but flags it excluded in this project; it also retains an existing read timestamp.
- **Reset to unread:** clears both the read timestamp and the important/excluded decision.

The displayed status prioritizes important/excluded over read/unread. A paper can therefore be important and already read. Use desktop review/read filters and **Show excluded** to control the list. Add project tags, select a paper, and attach/remove tags to organize collections. These actions affect only that project's paper relationship—not other projects or the shared canonical metadata.

### 5. Export results

In the desktop client, select the project and use **Export CSV** or **Export RIS**. Check Settings if you want to omit excluded papers; the initial default includes them.

CSV is useful for spreadsheets. RIS is useful for reference managers. Exports include canonical bibliographic metadata and project context, including review state and tags where the format supports them. Some reference managers ignore project/review notes in RIS, so keep CSV if those details matter.

Exports are not backups: they do not preserve saved queries, complete execution history, or raw source snapshots.

### 6. Back up and handle common problems

Optional: connect a Google Sheet in the browser's **Google Sheets sync** card. The
service-account JSON file must be configured on the server, and that account must
have Editor access to the Sheet. Click **Sync now** for an initial upload; enable
scheduled sync to update after monitored searches. The dedicated tab shows its
last successful update time. See [Google Sheets sync](GOOGLE_SHEETS.md) for setup,
idempotency, and data-ownership details. This is not a backup or a two-way import.

For a local SQLite workspace, stop all clients/workers and copy the complete data directory, including the database, snapshots, and settings, to a safe backup location. Restore into a separate clean directory and launch with that directory selected. Check projects, queries, papers, and history before replacing a working copy. Environment-supplied API keys are not part of the backup. For custom database locations or PostgreSQL, follow [Installation](INSTALLATION.md) rather than relying on a data-directory copy.

| Symptom | What to check |
| --- | --- |
| Browser says unavailable | Server terminal still running, correct localhost port, startup errors |
| Save query is disabled | Project selected, at least one allowed source, valid expression preview |
| Source disabled notice | Desktop Settings discovery sources, then saved query source selection |
| No automatic updates | Server launched with `--monitor`, query schedule enabled, next due time, monitoring source allow-list |
| “Run due” starts nothing | Queries may not be due yet; use a manual search for immediate execution |
| 403, authentication, or quota error | Run details, credentials, service limits, and explicitly configured reachable proxy if appropriate |
| Few/no papers | Expression, filters, provider outcome, page budget; do not treat failed searches as negative evidence |
| Workspace appears empty after restart | Data directory and `DATABASE_URL`; no automatic cross-device sync |
| Interrupted run remains running | Stop workers, inspect history, use explicit desktop Recovery |

AI analysis, PDF processing, analytics, extra providers beyond the four listed, browser review editing, and remote multi-user hosting are not part of this guide's current release.
