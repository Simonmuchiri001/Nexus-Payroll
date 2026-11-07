# Development and release checks

## Toolchain

- Python 3.13, managed by `uv`
- PostgreSQL 18
- Ruff with formatting and lint rules
- Pyright in strict mode over application sources
- pytest, pytest-asyncio, pytest-qt

## Daily loop

```bash
uv sync --all-packages
uv run alembic -c backend/alembic.ini upgrade head
uv run uvicorn nexus_api.main:app --host 127.0.0.1 --port 8000
uv run nexus-desktop
```

Run the API from the repository root. Alembic reads `NEXUS_DATABASE_URL`; the
test harness overrides it with `NEXUS_TEST_DATABASE_URL`.

## Tests

```bash
uv run pytest -q
```

The backend test harness:

- requires a database name ending in `_test`;
- rebuilds the public schema through Alembic once per session;
- truncates application tables between tests;
- uses ASGI transport, not a live network.

The desktop test harness:

- uses `httpx.MockTransport` for deterministic API responses;
- forces Qt's offscreen platform in `desktop/tests/conftest.py`;
- exercises the real PyQt widgets, worker threads and signal callbacks.

## Static checks

```bash
uv run ruff format --check backend desktop
uv run ruff check backend desktop
uv run pyright
```

Pyright strict currently covers `backend/src` and `desktop/src`. Tests are
excluded from Pyright but are linted.

## Database migrations

```bash
cd backend
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
```

Review generated migrations manually. Verify server defaults, uniqueness
constraints, foreign keys and tenant indexes. Never edit a migration that has
already been deployed; add a new one.

## Payroll rule changes

1. Confirm the effective date, rates, caps and legal basis in an authoritative
   source.
2. Add the source URL and verification date to the rule set.
3. Update or add unit tests for boundary behaviour and rounding.
4. Increment the rule-set version and checksum.
5. Run the full payroll lifecycle test.
6. Obtain tax/legal review before production use.

The application never downloads statutory rules at runtime.

## Holiday calendar changes

The Kenya national calendar
(`backend/src/nexus_api/leave/kenya_public_holidays.json`) follows the same
discipline as statutory rules:

1. Confirm dates from an authoritative source (Public Holidays Act Cap 110,
   gazette notice, or the responsible ministry).
2. Edit the JSON, bump `version`, update `verified_on` and `source_url`.
3. Update checksum-dependent tests and re-run the holiday tests.
4. Run the full payroll lifecycle test — calendar dates feed
   working-day counts and therefore deductions.
5. Obtain legal review before production use. Movable dates (Easter, Eid)
   must be confirmed for the target year; subject-to-moon holidays are
   gazetted per year.

After import, each company sees the new calendar version. Import is
idempotent and never overwrites a company-custom holiday, so verified
updates are safe to import again.

## Fixed assets operations

Fixed assets need no special command-line operation. Use the desktop Assets
workspace or the `/api/v1/assets` API. Before capitalization, depreciation or
disposal approval, confirm the fiscal calendar contains an open fiscal period
covering the acquisition, depreciation or disposal date; the API rejects
postings outside an open period. Accumulated depreciation and net book value
are returned by the API from posted depreciation rows and are not entered
manually. Tax depreciation and procurement are not implemented.

## Desktop packaging

The desktop executable entry point is:

```bash
uv run nexus-desktop
```

Production installers, code signing, notarisation and auto-update are not yet
implemented. Do not distribute a development environment as an installer.

## CI

`.github/workflows/ci.yml` runs three jobs:

- formatting, lint and Pyright;
- backend integration tests against PostgreSQL 18;
- desktop tests with Qt offscreen.

## Release checklist

- [ ] Ruff format and lint pass
- [ ] Pyright reports zero errors
- [ ] Full test suite passes against PostgreSQL 18
- [ ] Migrations upgrade and downgrade cleanly
- [ ] Statutory rules re-verified for the target period
- [ ] Secrets supplied through the deployment secret store
- [ ] TLS termination and trusted hosts configured
- [ ] Database backup and restore rehearsed
- [ ] Desktop installer signed and notarised (not yet available)
- [ ] Provider integrations verified against real contracts (none enabled)
