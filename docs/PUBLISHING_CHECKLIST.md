# GitHub publishing checklist

This is a pre-publication checklist, not a statement that version `0.1.4` is release-ready. The repository can be public while the software remains an explicitly marked development release. Use [Known Limitations](../KNOWN_LIMITATIONS.md) and [Development Status](../DEVELOPMENT_STATUS.md) as the source of truth for open acceptance gates.

## Before creating a public repository

- [ ] Confirm the approved MIT holder and [LICENSE](../LICENSE) text.
- [ ] Review **both current files and the branch history being pushed** for API keys, service-account JSON, patient data, local databases, raw source snapshots, exports, logs, and private infrastructure addresses. `.gitignore` does not remove data from commits already made. The initial public branch was created from a sanitized snapshot; the previous development history remains local and must not be pushed through backup refs or tags.
- [ ] Keep service-account key files outside the checkout; verify that `RESEARCH_ENGINE_GOOGLE_SERVICE_ACCOUNT_FILE` points to a local file rather than storing its contents in GitHub settings or documentation. `.gitignore` covers common local databases, snapshots, logs, settings, exports, and example workspaces, but it is not a substitute for reviewing `git status` and staged files.
- [ ] Re-read the README's release warning. Do not upload databases or exports from the affected `0.1.0` pilot as examples.
- [ ] Run a fresh virtual-environment install, package build, `pytest -q`, and local browser startup. Run the native Windows checklist on the target machine when that environment is available.
- [ ] Review [API source terms/limits](API_SOURCES.md) and [Google Sheets limits](GOOGLE_SHEETS.md) before describing live service support; live Google permissions remain unverified until an authorized Sheet is shared.

## On GitHub

- [ ] Add a short repository description that says "medical literature API monitoring" rather than implying Google Scholar scraping or clinical decision support.
- [ ] Verify GitHub detects the MIT license, README, and issue/PR templates. Put any official project contact/support address into [SECURITY.md](../SECURITY.md) before promising private disclosures.
- [ ] Configure a private vulnerability-reporting route if available. Do not direct security disclosures to a public issue.
- [ ] Set branch and review protections appropriate for maintainers; add a CI workflow only after its test environment and secret policy are understood. No CI status badge is claimed today.
- [ ] Choose a release tag/version deliberately. The package still reports `0.1.4`; documentation improvements and Google Sheets work do not silently create a new published release.

## After publication

- [ ] Keep [Development Status](../DEVELOPMENT_STATUS.md), [Known Limitations](../KNOWN_LIMITATIONS.md), [Changelog](CHANGELOG.md), and the [Documentation Index](INDEX.md) current with each release.
- [ ] Recheck external API requirements and quotas before shipping new providers or changing search cadence.
- [ ] Treat live acceptance evidence separately from fixture and offscreen tests. Do not call the software production/clinical-ready without resolving the documented gates.
