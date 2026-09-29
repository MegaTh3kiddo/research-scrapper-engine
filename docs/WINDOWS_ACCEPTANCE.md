# Windows desktop acceptance

Target selected by the user: Windows. Status: **not yet verified on a native
Windows desktop**. Linux Qt offscreen tests do not satisfy this checklist.

## Isolated installation (PowerShell)

Install a supported Python 3.11+ release with the Python launcher. From the
repository folder, without reusing an existing development environment:

```powershell
py -3 -m venv .venv-pilot
& .\.venv-pilot\Scripts\python.exe -m pip install ".[desktop]"
$env:RESEARCH_ENGINE_DATA_DIR = Join-Path $env:LOCALAPPDATA "ResearchScrapperEngine-pilot"
& .\.venv-pilot\Scripts\research-engine-desktop.exe
```

Use a new pilot data directory, not a production or affected 0.1.0 database.
Do not set `QT_QPA_PLATFORM=offscreen`: this pilot must display real windows.
If `DATABASE_URL` is set from another session, clear it for the SQLite pilot:
`Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue` before launch.
For a downloaded wheel, replace `".[desktop]"` with the actual wheel path plus
`[desktop]`; do not use an outdated hard-coded package version.

## Functional checklist

- Confirm first startup creates the database, migrations and log without error.
- Create a project and one narrow query per PubMed/Europe PMC source; run each.
- Configure OpenAlex access if needed; never paste API keys into screenshots/logs.
- Confirm results, paper details, original source links and provenance are visible.
- Repeat searches: existing papers should not become additional canonical papers.
- Mark read/important/excluded, add tags, close and relaunch; verify persistence.
- Export CSV and RIS; verify UTF-8 authors, dates, identifiers, review status/tags.
- Enable an appropriately short monitoring interval; verify partial/completed/error
  history, continuation and responsive UI during provider operations.
- Enable/disable proxy in Settings; an unavailable proxy must fail safely, not
  silently connect directly. The supplied LAN proxy must be reachable from Windows.
- Exercise query archive/restore, history, recovery and identity-review controls;
  record any unavailable control as a failed/pending item rather than a pass.
- Check resizing, high-DPI scaling, keyboard focus, dialog scrolling, closing while
  a search/export is running, readable errors and status messages.

## Backup and restore rehearsal

Stop the desktop and any worker first. Copy the **entire** pilot data directory
to a separate backup folder. It includes `research_engine.db`, `snapshots/` and
settings. Restore the entire copy to a different new directory, set
`RESEARCH_ENGINE_DATA_DIR` to it, and launch the installed package. Compare project,
paper, query, review/tag and history counts, exports and source snapshots. Never
copy a live SQLite database without a database-aware backup procedure.

## Record evidence

Append to `ACCEPTANCE_REPORT.md`: Windows version, Python/package versions,
installation commands, pilot date, migration revision, source counts, duplicate
behavior, monitoring/recovery results, export and restore results, and usability
issues. Redact keys/proxy credentials. Update `KNOWN_LIMITATIONS.md` from actual
findings. Until these checks have evidence, native acceptance remains open.
