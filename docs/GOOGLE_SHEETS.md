# Google Sheets sync

Google Sheets is an optional, one-way projection of each research project's paper library. The database and immutable source snapshots remain authoritative. Sync adds new canonical papers, updates changed metadata and project review fields, and records the last successful update in both the Sheet and local status. It does not import edits made in Google Sheets.

## First-time setup

1. In Google Cloud, [enable the Google Sheets API](https://developers.google.com/workspace/guides/enable-apis), [create a service account and its JSON key](https://developers.google.com/workspace/guides/create-credentials). Keep the file outside the repository and do not paste its private key into the browser, chat, or a query. The application never stores the key in its database.
2. Create or choose a Google Sheet and share it with the JSON file's `client_email` as **Editor**. A URL alone does not grant access. Use a Sheet intended for this project; each project gets a dedicated `Research Engine - <project UUID>` tab. Existing tabs are left alone.
3. Install the optional integration from the repository folder:

   Linux/macOS: `.venv/bin/python -m pip install -e '.[web,sheets]'`

   Windows PowerShell: `.\.venv\Scripts\python.exe -m pip install -e ".[web,sheets]"`

4. Set `RESEARCH_ENGINE_GOOGLE_SERVICE_ACCOUNT_FILE` to the **absolute path** of the JSON key file before starting the web server. For example, in Bash: `export RESEARCH_ENGINE_GOOGLE_SERVICE_ACCOUNT_FILE=/secure/path/google-service-account.json`; in PowerShell: `$env:RESEARCH_ENGINE_GOOGLE_SERVICE_ACCOUNT_FILE = 'C:\secure\google-service-account.json'`. Restart the server after changing this variable.
5. In the browser, select a project, paste the Sheet URL into **Google Sheets sync**, choose whether to **Sync after scheduled searches**, and click **Save connection**. Then click **Sync now** to check access and populate the tab. Refresh to see the job result and last successful update.

No Google API key is needed for this path. The service-account JSON and Editor share are required. If you want us to verify a live connection, provide the Sheet URL/ID and the *path* to your local JSON file, not the JSON contents or private key.

## How updates work

- The project sheet tab has a status row (`Last successful update (UTC)`), a project name row, a paper-count row, a header row, and then paper rows. Its 19 columns match the CSV export, including project/paper IDs, title, authors, journal, publication date, DOI, PMID, URL, review status, read flag, tags, sources, abstract, publication types/status, source databases, and source-specific citation observations.
- Canonical `article_id` is the row key. Repeat syncs update existing rows and append only newly collected papers. Excluded papers remain included, so the tab is an auditable project projection. Do not edit the app-managed tab's headers or IDs; the sync rejects unexpected headers, duplicate IDs, and foreign project rows rather than overwriting them.
- An enabled connection syncs after a scheduled query finishes with a completed or partial ingestion outcome. A failed provider run does not trigger sync. A manual literature search does not automatically sync; click **Sync now** when you want to publish its results.
- The Sheet's last-successful-update cell is written only after the paper writes succeed. Local status separately retains the previous successful timestamp and records a safe error code if a later attempt fails. Retry by clicking **Sync now**; the app reads existing IDs to avoid duplicate rows after interrupted writes. Search run status is not changed by a Sheet failure.
- Repointing a project to a different spreadsheet resets the local last-successful-update status. The old Sheet is not deleted or modified. Disabling automatic sync leaves the manual button available.

## Boundaries and troubleshooting

- The tab is app-managed, not bidirectional. Spreadsheet edits to managed columns can be overwritten on the next sync. Deleting rows in the Sheet may cause them to be re-added. Changing canonical paper IDs or moving rows between projects is unsupported.
- The server must remain running with `--monitor` for automatic scheduled sync. Existing query schedules retain their saved intervals; new schedules default to hourly source searches. Google Sheets sync happens only after a due search completes; it does **not** call literature providers on browser refresh.
- The current local, single-worker design prevents overlap in the browser and within a service instance, but concurrent independent workers or collaborators editing the same tab are not supported. Retry after a Google `429`/transient error; the adapter uses bounded backoff.
- A `403` usually means the service account lacks Editor access, the Sheets API is disabled, or an organization policy blocks sharing. A `404` can mean the URL/ID is wrong or the service account cannot see the Sheet. The saved local network proxy is also applied to Google token and Sheets requests. No proxy or private-key details are shown in the browser.
- The integration needs the `sheets` Python extra. Without it or the environment file path, saving a Sheet link still works but sync reports `DEPENDENCY_MISSING` or `CREDENTIALS_MISSING`. Live access cannot be verified until you provide a service account and Sheet.

For the current API operations, see Google's [Sheets API documentation](https://developers.google.com/workspace/sheets/api/reference/rest), [service-account sharing instructions](https://developers.google.com/workspace/guides/create-credentials), and [usage limits](https://developers.google.com/workspace/sheets/api/limits). Google's quotas and billing terms may change; check them before large-scale automation.
