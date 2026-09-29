# Installation, upgrade, and recovery

**Data warning:** The 2026-09-25 live [acceptance pilot](../ACCEPTANCE_REPORT.md) found a critical false-merge defect in 0.1.0 PubMed ingestion. Version 0.1.1 fixes new ingestion, but upgrading an affected database does not repair its records; follow [Data Integrity Remediation](DATA_INTEGRITY_REMEDIATION.md). Native desktop acceptance remains outstanding.

Version 1 is a single-user local release. Python 3.11+ is required; a graphical desktop supported by PySide6 is needed only for the desktop client. The optional browser client uses FastAPI on localhost. A headless Qt smoke test is not a substitute for a target-machine display test.

Windows is the selected native acceptance target. See the
[Windows installation and pilot checklist](WINDOWS_ACCEPTANCE.md) for PowerShell
commands and isolated backup/restore checks. Native Windows acceptance is pending.

## Clean install: desktop

In a new virtual environment, from a downloaded source checkout or wheel:

```sh
python3 -m venv .venv
./.venv/bin/python -m pip install '.[desktop]'
./.venv/bin/research-engine-desktop
```

For a wheel, replace `'.[desktop]'` with the actual downloaded wheel path plus
`[desktop]` (current build: 0.1.4). Use the launcher installed in that environment.
Startup creates the database and applies migrations through `0010`; no separate
migration command is required. Default data lives in `~/.research-scrapper-engine`
(`research_engine.db`, `snapshots/`, rotating logs and settings). Set
`RESEARCH_ENGINE_DATA_DIR` to choose another local directory. The wheel includes
migrations and SOCKS support. `DATABASE_URL` changes the SQLAlchemy database;
snapshot/settings files still remain in the local data directory.

For the browser instead, install `'.[web]'` and run
`./.venv/bin/research-engine-web --monitor` from Bash, or use the PowerShell commands
in [Web Client](WEB_CLIENT.md). Open <http://127.0.0.1:8000>. Optional Google Sheets
sync needs `'.[web,sheets]'` and the separate [Google Sheets setup](GOOGLE_SHEETS.md).
The web server is localhost-only and has no public-hosting authorization.

Desktop **Settings… → Network / proxy** enables an explicit proxy for new sessions
of all four providers, including monitoring. Supported URLs are `http://host:port`,
`socks5://host:port` and `socks5h://host:port`; SOCKS5H sends destination hostnames
through the proxy. Proxy is off by default, and direct mode ignores environment
proxy variables. Changes affect new sessions, not an in-flight search. The URL is
saved locally with owner-only permissions on POSIX; embedded credentials are
rejected. An unavailable proxy fails safely rather than switching routes. HTTP
403 never automatically enables a proxy; change the setting before retrying.
Transport uses [HTTPX proxy support](https://www.python-httpx.org/advanced/proxies/).

For development, `python -m pip install -e '.[dev,desktop]'` and `python -m pytest -q` run the test suite. The `postgres` extra installs the optional PostgreSQL driver; PostgreSQL use is intended for controlled integration testing and later server deployment, not a shared v1 desktop installation.

## Upgrade and backup

Stop the desktop, browser server, and local monitoring workers before upgrading. Back up the entire data directory, including both SQLite database and `snapshots/`; copying only the database loses source-response evidence. Keep the backup until the new version opens and a project, paper detail, and snapshot are checked. Install the newer package in the virtual environment and launch it; startup migrates the database to head. Do not run a new binary against an older schema with a concurrent old process. An upgrade from revision `0003` with existing project, query, and search-run rows was verified; the test suite also covers earlier SQLite backfills and a populated `0009`→`0010` project-link upgrade. Migration reversal is not a substitute for restoring a complete database-and-snapshot backup. A separately stored Google service-account key file must be protected/backed up separately; it is not inside the workspace directory.

## Interrupted scheduled attempt

An interrupted running attempt blocks only its saved query. After confirming the desktop/local worker process has stopped, inspect and recover it:

```sh
./.venv/bin/research-engine-recover --list-running
./.venv/bin/research-engine-recover --recover EXECUTION_ID --confirm-worker-stopped
```

Use `--data-dir PATH` if the desktop used another data directory. Recovery atomically marks the attempt and its linked still-running provider runs failed; committed pages and immutable snapshots remain. The next scheduled due time is retained. `--retry-query QUERY_ID` queues an enabled schedule immediately, but a running desktop/worker must execute it. Never recover while another worker might still be executing that attempt: the confirmation flag is an operational safeguard, not process detection. Failed provider runs remain visible in history; raw exception text is not written to the local log.

Additional operator controls:

```text
research-engine-recover --list-runs
research-engine-recover --recover-run RUN_ID --confirm-worker-stopped
research-engine-recover --coverage QUERY_ID
research-engine-recover --restart-coverage QUERY_ID PROVIDER full --confirm-worker-stopped
research-engine-recover --resume-run RUN_ID --confirm-worker-stopped
research-engine-recover --retry-query QUERY_ID
research-engine-recover --list-attempts
research-engine-recover --recover-attempt ATTEMPT_ID --confirm-worker-stopped
```

Use `recover-run` only for manual runs; recover scheduled children through their owning execution. `resume-run` creates a new manual run from the last committed cursor of a failed/partial predecessor, rejecting changed query revision, page size, translation or parser. Scheduled retries resume their existing full/recent coverage. Restarting coverage archives its checkpoint and starts a new campaign on the next scheduled attempt; it does not delete papers, snapshots or history. All recovery/restart/retry actions retain an outbox audit record.

## Verified scope and limitations

- A wheel with desktop extra installed and started outside the source tree in a fresh virtual environment; SQLite migrated from zero to head. Startup was exercised with Qt's offscreen platform, not a physical display.
- PostgreSQL 16 fresh migration, metadata parity, a `0003`-to-head upgrade retaining existing rows, and schedule claims were exercised against a disposable local server through revision `0009`. The added `0010` Sheet-link migration has SQLite fresh/upgrade coverage; its PostgreSQL execution still needs a live integration check. Run `TEST_POSTGRES_URL` against an **empty disposable database** to repeat the opt-in test. Multi-user use, remote snapshot storage, and server deployment are not verified.
- SQLite relies on local filesystem semantics, foreign-key PRAGMA on application connections, serialized writes, partial unique indexes, and SQLite batch-table migration behavior. PostgreSQL schema checks do not validate concurrent worker rollout or backup/restore.
- Scheduling runs only while the desktop monitor or browser server (`--monitor`) is running. A process crash may leave a running attempt needing explicit recovery. Application services reject manual execution while the same query has a running scheduled/provider run; distributed multi-worker manual locking is not provided in v1.
- The outbox records events but has no consumer. No PDF, AI, analytics, notification, shared-user, or installer/binary bundle support exists in v1.
