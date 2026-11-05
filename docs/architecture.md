# Architecture

## System shape

```text
PyQt6 desktop
    |
    | HTTPS / JSON, bearer access token
    v
FastAPI service  -------------------->  PostgreSQL 18
    |  SQLAlchemy 2.x async                    |
    |  Alembic migrations                      |  append-only audit trigger
    |  Pydantic validation                     |  unique posting source key
    v                                          v
statutory rule files (checked in, versioned)
```

The backend is the only component that may touch business data. The desktop is
a presentation client. It does not calculate statutory deductions, decide
permissions, write journals or connect to PostgreSQL.

## Backend modules

The backend is a modular monolith. Packages are split by business capability:

- `identity`: users, passwords, JWT access tokens, refresh-token sessions,
  memberships, role assignments, login history and tenant-aware permission
  checks.
- `organizations`: organisations, companies, branches and departments.
- `employees`: employee records, effective-dated compensation and termination.
- `leave`: leave types, entitlements, capped carryover, holidays, working-day
  requests, approval decisions, balance movements and attendance records.
- `loans`: employee loans and advances, approval, disbursement journals,
  reducing-balance schedules and payroll deduction settlement.
- `benefits`: benefit plans, effective-dated employee enrolments, contribution
  snapshots and payroll benefit charges.
- `expenses`: expense categories, receipt metadata, employee claims, approval
  separation and cash reimbursement posting.
- `payroll`: pay groups, periods, runs, calculations, lifecycle transitions,
  statutory rule sets and per-run snapshots.
- `accounting`: company chart of accounts, fiscal periods, journals, posting and
  trial balance.
- `audit`: append-only event recording.
- `api`: shared dependencies such as authentication and pagination.
- `core`: error types, request context and structured logging.
- `database`: SQLAlchemy engine, session factory and metadata.

A request enters the API, is authenticated into an `AuthContext` containing the
user, organisation membership and tenant, then calls a service function. The
service checks permissions and tenant/company access before reading or writing.
Routers translate HTTP input to service calls and return typed response models.

## Tenant isolation

Every tenant-owned table carries `organization_id`. Company-scoped tables also
carry `company_id`. Service functions resolve the entity and verify that it
belongs to the authenticated organisation and to a company the membership may
access. Tests cover direct cross-tenant identifiers, not only filtered lists.

## Identity and sessions

- Passwords are hashed with Argon2 through `pwdlib`.
- Access tokens are short-lived signed JWTs with issuer, audience, tenant,
  membership and unique `jti` claims.
- Refresh tokens are random values. Only their SHA-256 hashes are stored.
- Refresh rotation revokes the previous refresh token. Reuse of a revoked token
  is rejected.
- Suspending a membership revokes active refresh tokens.
- The last active organisation administrator cannot be removed or suspended.
- Desktop refresh tokens live in the OS keychain. Access tokens stay in memory.

## Payroll lifecycle

```text
draft -> calculated -> approved -> finalized -> posted
```

- Creating a run snapshots the applicable statutory rule set.
- Calculation writes one result per eligible employee and a line-level snapshot.
- Recalculation is allowed only in `draft` or `calculated` states.
- Approval requires a different user from the preparer. After approval, the run
  and its results are immutable.
- Finalisation and posting require their own permissions.
- Posting is idempotent. The journal source key
  `(company_id, source_type, source_id)` is unique, so repeated calls return
  the same journal instead of creating a second one.
- Payroll periods remain open at this stage of the product; period closure and
  reversals are future work.

## Leave and attendance

- Leave types define an annual entitlement and maximum carryover. Balance rows
  are created per employee, leave type and calendar year.
- Pending requests consume available balance immediately. Approval moves days
  from pending to used and creates attendance records for each working day;
  rejection releases the pending days.
- Working days exclude weekends and company holidays. A request is rejected if
  the period contains no working days.
- The user who records a request cannot approve or reject it.
- Approved leave that has already started cannot be cancelled. Future approved
  leave can be cancelled, which releases the balance and removes its attendance.
- Attendance is weekday-only, cannot be recorded in the future, and cannot
  collide with approved leave. Leave attendance is owned by the leave request
  and is not editable through the attendance register.
- Company holidays are configured per company. National public holidays are not
  imported automatically yet, so they must be recorded explicitly.
- Leave and attendance are not yet consumed by payroll calculation. Unpaid
  leave, absence and attendance-driven deductions remain future work.

## Loans and benefits

- Loan and advance applications move through
  `pending -> approved -> disbursed -> closed`, with rejection and cancellation
  branches. The requester cannot approve their own application.
- Disbursement posts a balanced journal from the employee loan receivable
  account to cash exactly once, keyed by the loan source.
- Repayment uses a reducing-balance schedule. Instalments due by the payroll
  payment date are reserved during calculation and marked deducted on
  finalisation. A loan closes when its final instalment is settled.
- Benefit plans carry monthly employee and employer contributions. Enrolments
  snapshot those amounts and are effective-dated; only one active enrolment per
  employee and plan is allowed.
- Payroll prorates contributions by calendar-day overlap with the period. The
  employee share is a post-tax deduction; the employer share is additional
  employer cost. Posting credits employee benefits payable and debits employee
  benefits expense.
- Benefit employee and employer totals are stored on the run and each result,
  including a per-enrolment snapshot, so later plan changes cannot rewrite a
  calculated or finalised run.
- Employer-provided benefit tax treatment and provider billing are not inferred
  or automated. They require verified jurisdiction rules and provider contracts.

## Expenses and reimbursements

- Expense categories are company-scoped and map to active ledger expense
  accounts. A category cannot be archived while draft, submitted or approved
  claims still need it.
- Claims carry the employee, expense date, purpose, amount, currency and
  receipt metadata. Drafts can be edited by the requester or an expense manager;
  submission freezes the claim for approval.
- The requester cannot approve or reject their own claim. The approver cannot
  reimburse the same claim, and the requester cannot reimburse it. Reimbursement
  requires an authorised payer and an open fiscal period.
- Reimbursement creates one posted journal keyed by
  `(company_id, 'expense_claim', claim_id)`: debit the mapped expense account
  and credit cash. Retried reimbursement requests return the same claim and
  journal without paying twice.
- A category's account mapping cannot change after claims have been recorded,
  preserving the accounting meaning of historical claims and journals.

## Statutory calculation

`backend/src/nexus_api/payroll/rules/kenya_2026.json` is parsed and validated
into typed rules. The engine calculates PAYE bands and relief, NSSF, SHIF,
housing levy and optional NITA using decimal arithmetic and explicit rounding.
Every run records the rule-set version and rules snapshot. Rule updates are code
changes with source URLs and tests, not runtime web lookups.

## Accounting invariants

- A journal cannot be posted unless total debits equal total credits.
- Posted journals cannot be edited or deleted through the API.
- Payroll posting creates a balanced journal from the finalised run.
- The trial balance aggregates posted journal lines only.
- Fiscal periods gate posting dates.
- Monetary values use `NUMERIC` in PostgreSQL and `Decimal` in Python.

## Error contract

API failures use one envelope:

```json
{
  "error": {
    "code": "conflict",
    "message": "A payroll run already exists for this pay group and period.",
    "details": {}
  },
  "request_id": "..."
}
```

The desktop client parses this envelope and presents the safe message. Unexpected
backend errors are logged and returned without internal details.

## Testing strategy

- Unit tests cover the deterministic statutory engine and invalid rule data.
- ASGI API tests cover authentication, refresh rotation, identity administration
  and tenant isolation.
- PostgreSQL lifecycle tests cover payroll figures, segregation of duties,
  immutability, ledger posting and audit actors.
- Leave tests cover working-day calculation, holiday exclusion, balance
  movements, separation of duties, overlap and entitlement boundaries,
  carryover caps and attendance collisions.
- Loan tests cover approval and disbursement journals, payroll deduction,
  affordability rejection and automatic closure.
- Benefit tests cover plan and enrolment boundaries, duplicate active
  enrolments, archive protection, calendar-day proration, payroll totals and
  balanced benefit posting.
- Expense tests cover receipt and employment-date validation, draft editing,
  approval and payer separation, category archive protection, balanced cash
  reimbursement and idempotent retry behaviour.
- Migration tests run upgrade, downgrade to base and re-upgrade in an isolated
  schema.
- Trigger tests prove audit rows cannot be updated or deleted.
- Desktop client tests use `httpx.MockTransport` to exercise refresh-on-401,
  structured errors and unrecoverable sessions.
- Offscreen pytest-qt tests load every workspace page, switch pages and exercise
  payroll setup against the mocked transport.

## Extension rules

When adding a domain module:

1. Add SQLAlchemy models with tenant/company keys and an Alembic migration.
2. Add Pydantic request/response schemas.
3. Put access checks, invariants and writes in a service layer.
4. Expose a thin router with typed responses and status codes.
5. Record audit events for materially significant changes.
6. Add cross-tenant and cross-company denial tests.
7. Add the desktop API methods, typed models and page without business logic.
8. Run Ruff, Pyright strict and the complete test suite.
