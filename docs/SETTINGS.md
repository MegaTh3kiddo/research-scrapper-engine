# Settings guide

Open **Settings…** in the desktop. Settings belong to the local installation,
not an individual project. The services are UI-neutral for later API/worker use.

## Sources

Choose **New-query default** sources. Creating a query copies these choices;
changing settings does not rewrite existing saved query revisions. Edit a query
to choose any of PubMed, Europe PMC, OpenAlex or Crossref. Tooltips describe source
coverage and supported search features. Select one provider or **All query
sources** next to Run search. A failed source does not discard another's results;
each provider has its own audited run and immutable responses.

**Allow monitoring** is independent. Automatic searches use only saved query
sources that also appear in this allow-list. Monitoring must be enabled on the
query and the desktop must remain open. With no eligible sources, a due attempt
fails visibly. Source errors mark the scheduled attempt failed; page-capped
coverage is partial and continues at the next due attempt. Other sources are still
attempted. Full reconciliation and recent-date lanes have separate checkpoints
and each receives the configured per-run page budget.

Medical-source defaults remain PubMed and Europe PMC. OpenAlex is
multidisciplinary and Crossref is publisher-deposited DOI metadata, so inspect
their relevance rather than assuming all results are medical. Crossref does not
support Boolean syntax or human/open-access filters. Unsupported features fail
explicitly; they are not silently dropped. OpenAlex rejects PubMed field syntax
and granular PubMed clinical-trial/review classifications.
For Yes/No filters, a partially checked box means no filter; checked means Yes
and unchecked means No. Editing preserves explicit false values. Source support
still determines whether that selection can be executed.

## Workflow

- Results per page: 1–100, compatible with all integrated providers.
- Page budget per source/run: 1–100, applied to new manual and scheduled searches.
  Hitting the cap means partial coverage. Existing runs retain their own cursors.
- New-query monitoring interval: 1–720 hours, default 1 hour on new installations;
  saved settings and existing schedules are unchanged.
- Include excluded papers in exports: applies to desktop CSV/RIS exports.
  Other application-service callers can pass an explicit option.

## API access

Contact email is optional, sent to PubMed and Crossref for identification. Public
Crossref and Europe PMC require no key. PubMed and OpenAlex support basic keyless
queries; keys increase access limits/budget. Obtain keys yourself from
[NCBI](https://www.ncbi.nlm.nih.gov/books/NBK25497/) and
[OpenAlex API settings](https://openalex.org/settings/api).

Enter masked **Session API key** fields, or supply `NCBI_API_KEY` and
`OPENALEX_API_KEY` before launching. Blank input keeps the current key. **Clear**
disables the key (including the environment value) for this session. Dialog keys
disappear when the app exits; no plaintext key file is created. Never put secrets
in search expressions. A durable secure credential-store adapter is future work.

## Network and files

Proxy is off by default and applies to all four provider adapters when enabled.
Example: `socks5h://127.0.0.1:10808`. HTTP/SOCKS proxy URLs require host/port;
embedded credentials are rejected. Direct mode ignores ambient proxies. Failures
do not switch routes silently; the application has no automatic 403 fallback.

Non-secret preferences are in `application-settings.json`; network preferences
remain in the compatible `network-settings.json`. Both are atomically replaced
with owner-only POSIX permissions. Include them with database/snapshots in a
stopped-process backup. Writes to both files are not one transaction; after a disk
error, reopen Settings and verify both tabs.

## Reserved steps

Enrichment, Semantic Scholar citation processing, and ClinicalTrials.gov are
shown descriptively, not enabled: no requests are made to those services. Future
identifier-targeted enrichment must retain audit/identity safeguards. Trials need
a separate record family. AI, PDFs and analytics remain outside this extension.
