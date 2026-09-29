# PubMed identifier incident: safe handling of existing databases

The `0.1.0` PubMed parser could assign identifiers from cited references to the citing paper. Version `0.1.1` limits article identifiers to the current record's direct `PubmedData/ArticleIdList`, rejects a conflicting top-level PubMed ID, and labels new evidence with parser version `pubmed-parser-v2`. The fix prevents this specific false merge for **new ingestion**; it does not rewrite previously merged canonical records.

If a database has ingested PubMed records using `pubmed-parser-v1`, treat its canonical articles, project-paper links, review decisions, search classifications, and exports as potentially affected. A lack of obvious duplicate titles does not prove safety: one bad reference identifier can cause a false cross-provider match later. The 2026-09-25 pilot database was affected. Do **not** upgrade that database and merely rerun searches; existing false identifiers would remain in the identity index.

Version 0.1.1 conservatively refuses **all new ingestion** when it finds `pubmed-parser-v1` source records, even if a particular old record might be unaffected. This prevents new PubMed or Europe PMC data from merging against a potentially polluted identity index. The existing project and audit data remain available for inspection and backup; exports from that database remain untrusted.

## Safe recovery for a local single-user installation

1. Stop the desktop and local monitoring worker. Record the installed package version and data-directory path.
2. Make a full, cold copy of the data directory, including `research_engine.db`, `snapshots/`, and logs. Keep it immutable for audit. Do not delete or overwrite it.
3. Record project names/descriptions and saved query expressions, filters, enabled providers, and schedules from the old installation. These workflow definitions are not inherently made wrong by the parser defect. Do not treat old paper exports as a clean import source.
4. Install `0.1.1` or later. Set `RESEARCH_ENGINE_DATA_DIR` to a **new empty directory** and launch. Recreate projects and saved queries, then rerun PubMed and Europe PMC searches. Confirm PubMed source IDs are one-to-one with canonical articles and inspect run counts before relying on exports.
5. Re-review papers and reapply important/excluded decisions and tags against the newly ingested records. Do not automatically copy review links by old canonical article ID: those IDs may represent multiple unrelated papers.
6. Keep the old directory as a read-only audit archive, separate from the clean installation. If historical review work must be preserved automatically, plan a case-by-case migration using the retained raw snapshots and human review; no unattended repair command is provided.

This is deliberately a rebuild path, not an in-place mutation. The old database contains shared canonical article IDs, source records, query matches, review state, and outbox events whose correct split cannot be inferred reliably from one identifier field. Preserving the original archive is necessary for auditability. The disposable acceptance data was rebuilt from a new empty database and produced 132 distinct new PubMed papers with no metadata updates or identity conflicts; it is not a substitute for reviewing a user's own affected records.
