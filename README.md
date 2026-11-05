# Nexus Enterprise

Multi-tenant, multi-company payroll, people and accounting software with a
FastAPI backend and a PyQt6 desktop client. Kenya is the first supported
jurisdiction.

> **Status: pre-alpha, in progress.** This repository does not yet implement a
> complete ERP and is not production-ready. The sections below separate what is
> implemented from what is still missing. Do not deploy it for live payroll,
> statutory filing or payments without independent security, legal and tax
> review.

## What exists today

- **Backend authority.** Every calculation, permission decision, lifecycle
  transition and financial posting is owned by the API. The desktop client only
  reads and mutates through authenticated REST calls.
- **Identity and tenancy.** Organisation-scoped memberships, company and branch
  scoped role grants, six built-in roles, login rate limiting, rotating refresh
  tokens, suspended-account token revocation, last-administrator protection and
  append-only audit events enforced by a PostgreSQL trigger.
- **Organisation structures.** Organisations, companies, branches and
  departments, with tenant isolation checks on every request.
- **People.** Employee records with effective-dated compensation and
  termination workflows.
- **Leave and attendance.** Configurable leave types and entitlements, capped
  carryover, company holidays, working-day leave requests, separate approver
  checks, balance movements and an attendance register created from approved
  leave.
- **Loans and advances.** Employee applications, separation of duties,
  disbursement journals, reducing-balance schedules and payroll deduction
  reservations that settle on finalisation.
- **Benefits.** Company benefit plans, effective-dated employee enrolments,
  snapshotted employee and employer contributions, calendar-day proration,
  payroll deductions and balanced benefit payable/expense posting.
- **Expenses.** Configurable expense categories mapped to ledger accounts,
  receipt-backed employee claims, approval and payer separation, and
  idempotent balanced cash reimbursement journals.
- **Payroll.** Pay groups and periods, deterministic Kenya statutory
  calculation, preparer/approver separation of duties, immutable approved runs,
  finalisation, idempotent ledger posting and per-run statutory rule snapshots.
- **Accounting.** Company chart of accounts, fiscal periods, balanced journals,
  source-linked posting and trial balance.
- **Desktop.** Secure keychain session restoration, company switching,
  overview, people directory and onboarding, leave and attendance, loans,
  benefit plans and enrolments, expense claims and categories, payroll setup and
  lifecycle, accounting views, access administration, plus light and dark themes.
- **Quality gates.** Ruff, Pyright strict, PostgreSQL integration tests,
  migration round-trip tests, offscreen pytest-qt UI tests and a GitHub Actions
  workflow.

## Not implemented yet

These are real gaps, not hidden placeholders:

- Recruitment
- Benefit provider billing, eligibility feeds and insurer reconciliations
- Payroll deductions driven by unpaid leave, absence or attendance
- Automatic national public-holiday calendars
- Assets, depreciation, procurement and inventory
- Sales, invoicing, receivables, CRM and projects
- Bank accounts, reconciliations, payment disbursement and payment files
- KRA/eCitizen filing, M-Pesa, bank and accounting-provider integrations
- Documents, workflow designer, notifications, analytics and custom reports
- Production packaging/installers, deployment hardening and backup operations

Provider integrations remain unsupported until real contracts, APIs,
credentials and conformance material are available. No fake integration exists
in the code. NITA is disabled in the statutory rule set pending employer
eligibility and legal confirmation.

## Repository layout

```text
backend/
  alembic/                 database migrations
  src/nexus_api/           authoritative FastAPI service
    accounting/            journals, chart of accounts, trial balance
    employees/             people and compensation
    identity/              authentication, RBAC, memberships, roles
    leave/                 leave types, balances, requests, holidays, attendance
    organizations/         tenant, company, branch and department structures
    payroll/               calculation, lifecycle, statutory engine and rules
    audit/                 append-only audit events
  tests/                   PostgreSQL-backed integration tests
desktop/
  src/nexus_desktop/       PyQt6 desktop client
  tests/                   HTTP-mocked client and offscreen UI tests
docs/                      architecture and operating notes
infrastructure/docker/     backend image
compose.yaml               PostgreSQL 18 plus API for local evaluation
```

## Local development

### Prerequisites

- Python 3.13
- [uv](https://docs.astral.sh/uv/)
- PostgreSQL 18
- A desktop OS supported by PyQt6 and an OS keychain for session storage

### 1. Install dependencies

```bash
uv sync --all-packages
```

### 2. Configure the environment

```bash
cp .env.example .env
```

Set a strong `NEXUS_JWT_SECRET` of at least 32 characters. Create the database
role and databases with PostgreSQL:

```sql
CREATE ROLE nexus LOGIN PASSWORD 'choose-a-password';
CREATE DATABASE nexus OWNER nexus;
CREATE DATABASE nexus_test OWNER nexus;
```

Then point `NEXUS_DATABASE_URL` at the `nexus` database.

### 3. Migrate and bootstrap

```bash
cd backend
uv run alembic upgrade head
uv run nexus-api bootstrap \
  --organization-slug nexus-demo \
  --organization-name "Nexus Demo" \
  --company-code NEXUS \
  --company-name "Nexus Demo Limited" \
  --admin-email admin@nexus.co.ke \
  --admin-name "Nexus Administrator"
```

The bootstrap command prompts for a password unless
`NEXUS_BOOTSTRAP_ADMIN_PASSWORD` is set. It creates the first tenant, company,
administrator, chart of accounts, fiscal year, statutory rule seed, pay group
and open payroll period.

### 4. Run the API

From the repository root:

```bash
uv run uvicorn nexus_api.main:app --host 127.0.0.1 --port 8000
```

Health checks are at `/api/v1/health/live` and `/api/v1/health/ready`. API
documentation is at `/api/docs` outside production.

### 5. Run the desktop

```bash
uv run nexus-desktop
```

Use the API URL, organisation slug and administrator credentials from
bootstrap. Refresh tokens are stored in the operating-system keychain; access
tokens remain in memory.

For headless verification or automation:

```bash
QT_QPA_PLATFORM=offscreen uv run python -m nexus_desktop
```

## Docker evaluation stack

The compose stack is for local evaluation, not a production deployment:

```bash
cp .env.docker.example .env.docker
# Replace both required secrets in .env.docker.
docker compose --env-file .env.docker up -d --build
docker compose --env-file .env.docker ps
```

The API applies Alembic migrations before starting. Create the first tenant and
administrator inside the running container (pass the password through the
environment so it is not stored in your shell history):

```bash
docker compose --env-file .env.docker exec \
  -e NEXUS_BOOTSTRAP_ADMIN_PASSWORD='choose-a-strong-password' \
  api nexus-api bootstrap \
  --organization-slug nexus-demo \
  --organization-name 'Nexus Demo' \
  --company-code NEXUS \
  --company-name 'Nexus Demo Limited' \
  --admin-email admin@nexus.co.ke \
  --admin-name 'Nexus Administrator'
```

The `backend.Dockerfile` installs only backend dependencies and runs as a
non-root user. Production deployments still need TLS termination, secret
management, restricted network access, backups, monitoring and an explicit
migration policy.

## Quality commands

```bash
uv run ruff format --check backend desktop
uv run ruff check backend desktop
uv run pyright
uv run pytest -q
```

Backend tests rebuild the `public` schema in a database whose name ends in
`_test`. They refuse to run against any other database. Desktop tests use a
deterministic in-process HTTP transport and Qt's offscreen platform, so they do
not need a display or a running API.

## Statutory rules

Kenya rules live in a versioned JSON file at
`backend/src/nexus_api/payroll/rules/kenya_2026.json`. Each rule set records its
effective dates, source URLs, checksum and verification time. A payroll run
stores an immutable snapshot of the rules used for its calculation.

Changing a rate or threshold requires authoritative source verification,
updated tests, a new rule-set version and review. The existing data must not be
treated as current or legally sufficient without re-verification for the payroll
period being processed.

## Security notes

- Access and refresh tokens are distinct. Refresh tokens rotate on use, are
  stored only as SHA-256 hashes in the database and are bound to a session.
- Tenant scope is enforced server-side; client-supplied company IDs are
  validated against the authenticated membership.
- Approved payroll is immutable, the preparer cannot approve their own run, and
  posting is idempotent through a unique journal source key.
- Audit rows reject `UPDATE` and `DELETE` at the database level.
- The desktop application never connects to PostgreSQL.

## Documentation

- [Architecture](docs/architecture.md)
- [Development and release checks](docs/development.md)
