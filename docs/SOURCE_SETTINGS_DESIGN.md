# Source selection and settings

## Reviewed gaps and implementation scope

The reusable provider contract already covers query planning, pagination, fetch,
normalization and raw responses. It is sufficient for adding OpenAlex and Crossref
without changing discovery semantics or weakening the audited ingestion path.
An explicit `aclose` lifecycle method documents the cleanup already required by
worker/service factories. Later
permutation tests identified a canonical-field ownership gap, addressed below.
The composition root and desktop, however, hard-coded two sources and exposed only
proxy settings. This extension replaces those assumptions with a source catalog
and UI-neutral application settings.

## Workflow stages

1. Discovery: new queries inherit user-selected source defaults; each saved query
   explicitly owns its source selection and immutable revisions. Manual search
   can run one source or all of that query's sources.
2. Monitoring: an independent global source allow-list is intersected with the
   query's saved sources. This limits background traffic without rewriting query
   history. A scheduled attempt with no permitted sources fails visibly. All
   selected sources are attempted even if another fails.
3. Metadata enrichment: reserved, not implemented. A future identifier-targeted
   use case must pin the target paper and retain fetch snapshots and identity
   checks; a free-text discovery search must not masquerade as enrichment.
4. Citation analysis and clinical-trial monitoring: reserved, not implemented.
   Semantic Scholar belongs in citation/enrichment work; ClinicalTrials.gov needs
   a separate trial aggregate and paper-trial relationships, not article rows.

OpenAlex and Crossref are opt-in defaults because they cover more than medicine
and do not share PubMed's query syntax. Unsupported syntax/filters fail explicitly.

## Settings boundaries

`ApplicationSettingsService` validates/persists non-secret workflow preferences
through a storage port. It exposes source descriptors and session-only credentials
(environment variables are also supported). API keys are never saved in JSON,
query plans, logs or request provenance. Restarting requires re-entering session
keys or supplying environment variables. A durable OS credential-store adapter is
a future extension; plaintext persistence is deliberately excluded.

Settings tabs cover Sources, Workflow, API access and Network. Controls that are
not implemented are descriptive labels, not selectable fake functionality.
Defaults affect new queries, not existing saved queries. Page limits, monitoring
allow-lists and proxy changes affect newly started operations, never an in-flight
operation. Running providers are owned/closed by the worker's service session.

Existing `network-settings.json` remains compatible. New non-secret preferences
live in `application-settings.json`, written atomically with owner-only POSIX
permissions. Backups should include both files; keys are external/session-only.

## Source access references (checked 2026-09-26)

- [OpenAlex authentication](https://help.openalex.org/api/authentication/): basic
  keyless access exists; a free key increases budget. Pagination maximum is 100.
- [Crossref access](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/)
  and [July 2026 rate update](https://community.crossref.org/t/refining-rest-api-limits-for-improved-stability-and-reliability/16137):
  no public key required; contact email selects polite access. Use conservative
  paced requests and existing retry/backoff rather than evading limits via proxies.

## Schema and event review

Provider names and identifiers are data, not a two-source schema enum. Existing
snapshots, runs, observations, identity conflicts and outbox events support the
new literature sources. A metadata-selection gap found by permutation tests does
require migration `0005_metadata_provenance`: an article JSON map records the
chosen provider, source ID, snapshot, parser and run for each bibliographic field.
Existing rows receive an empty map; unknown old field origins are not invented.
Outbox recording remains sufficient;
no notification or AI consumer is added. Canonical metadata continues to use the
existing conservative merge policy, preserving every original source response.

With more sources, arrival-order overwrite is no longer acceptable. The SQL
adapter uses an injectable deterministic source-priority policy: PubMed > Europe
PMC > Crossref > OpenAlex, applied independently to each nonempty field. A source
that omits a field does not claim that field. Lower-priority records fill absent
values but cannot replace a higher-priority field winner. Unknown sources use rank
zero with a stable name tie-break. Old populated fields without recorded ownership
are protected conservatively using the best existing source rank until an equal
or higher source reasserts them. This is not evidence-quality assessment or a
source-specific per-field authority matrix; all assertions remain in snapshots.
