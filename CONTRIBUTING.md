# Contributing

Thanks for helping make the research workflow more reliable. This is a single-user development project being prepared for a public repository; current release boundaries and integrity warnings are in [README](README.md), [Development Status](DEVELOPMENT_STATUS.md), and [Known Limitations](KNOWN_LIMITATIONS.md). Please read them before proposing a change that affects medical-literature identity, provenance, or deployment.

## Before opening an issue

Search existing issues once the GitHub repository is available. Use the issue templates to describe the expected behavior and a small, reproducible example. Include the operating system, Python version, install method, relevant provider, and sanitized log/error code. Do **not** attach a real database, raw API response containing private information, API keys, service-account JSON, proxy credentials, or patient data. Use [Security](SECURITY.md) for a suspected vulnerability.

## Local development

Fork/clone the repository, open a terminal in its root, and use Python 3.11 or newer.

Linux/macOS (Bash):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,desktop,web,sheets]'
.venv/bin/python -m pytest -q
```

Windows (PowerShell):

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,desktop,web,sheets]"
.\.venv\Scripts\python.exe -m pytest -q
```

The desktop extra is optional for core changes but needed for desktop tests. The Sheets extra is optional for Google adapter tests. The default test suite uses fixtures and fake provider responses; it must not rely on live provider calls or a real Google Sheet. One PostgreSQL integration test is opt-in: set `TEST_POSTGRES_URL` only to an **empty, disposable** database that you are authorized to reset. Do not point tests at a working research database. See [Installation](docs/INSTALLATION.md) for startup migration and backup behavior.

## Design boundaries

- Keep domain and application services independent of PySide6, FastAPI, and database implementation. UIs call application services; providers implement their ports and preserve raw response/provenance data.
- Do not silently merge uncertain paper identities, drop failed-source evidence, or reinterpret historical snapshot/parser versions.
- New provider behavior needs deterministic fixtures for pagination, filtering, rate/error handling, normalization, and provenance.
- Schema changes need an Alembic migration, a fresh-install test, and an upgrade test that retains existing records. Do not rewrite or renumber published migrations.
- Network integrations should keep credentials out of source control and saved responses, use safe errors, and document provider terms/limits. External writes must remain scoped to the configured destination.
- A feature that changes both repository docs and browser Help should update both. Keep the [Documentation Index](docs/INDEX.md), [Changelog](docs/CHANGELOG.md), and [Development Status](DEVELOPMENT_STATUS.md) current.

## Pull-request checklist

1. Explain the user-visible outcome, risks, and any changed defaults.
2. Add or adjust tests for behavior and failure paths; include migration/upgrade checks if applicable.
3. Run `.venv/bin/python -m pytest -q` (or the PowerShell equivalent), plus relevant package/browser checks. State any tests you could not run.
4. Update documentation and note any live validation that still needs credentials, a target OS, or external services.
5. Keep the change focused. Avoid including local databases, snapshots, exports, logs, `.env` files, or service-account keys.

This repository uses the [MIT License](LICENSE). By submitting a contribution, you agree that it can be distributed under that license.
