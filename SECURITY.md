# Security policy

## Scope and support status

This is a local, single-user development release, not a public hosted service or clinical tool. The browser is bound to localhost and does not implement login or multi-user authorization. Native Windows acceptance, live Google Sheets integration testing, and historical `0.1.0` data remediation remain open. See [Known Limitations](KNOWN_LIMITATIONS.md) and [Data Integrity Remediation](docs/DATA_INTEGRITY_REMEDIATION.md).

## Report a vulnerability privately

When the GitHub repository is published, the maintainer should enable **private vulnerability reporting** in repository settings. Use GitHub's **Report a vulnerability** option if it is available. If it is not enabled yet, contact the maintainer privately through the profile/contact method they publish before sharing exploit details. Do not open a public issue with a working exploit, token, private key, database, patient data, or unredacted log. A public issue can be used later for a non-sensitive tracking summary after coordination.

This file does not promise an SLA or security support for particular versions. The maintainer should add a private contact method and supported-version policy before a general release.

## Safe operation

- Keep the web client on its default `127.0.0.1` binding. Its local token/origin guards are not substitutes for authentication, HTTPS, or deployment access controls. Do not expose it through a public tunnel or reverse proxy.
- Keep `NCBI_API_KEY`, `OPENALEX_API_KEY`, service-account JSON, and proxy credentials outside Git. A Google Sheets key belongs in a local file outside the checkout; the app reads its path from `RESEARCH_ENGINE_GOOGLE_SERVICE_ACCOUNT_FILE` and never needs the key pasted into the browser.
- Back up the entire stopped-process local data directory, including snapshots. Protect backups according to the sensitivity of the research data. Remove secrets from bug reports, screenshots, and logs before sharing them.
- Do not use databases populated by `0.1.0` without the documented rebuild/audit. The parser fix in later versions does not repair historical false merges.
