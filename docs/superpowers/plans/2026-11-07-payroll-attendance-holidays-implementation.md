# Payroll Attendance and Holidays Implementation Plan

**Goal:** Wire the leave/attendance register into payroll as unpaid
deductions, and seed a Kenya national public-holiday calendar.

**Architecture:** Add a versioned JSON holiday dataset with an idempotent
company import, a shared working-day helper, an attendance summary used by
`calculate_run`, explicit unpaid-day/deduction columns, and extended API +
desktop surfaces. The backend owns all calendar math, deductions, permissions
and snapshots.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.x async, PostgreSQL 18,
Alembic, Pydantic, pytest, pytest-qt, PyQt6, httpx MockTransport, Ruff and
Pyright strict.

**Spec:** `docs/superpowers/specs/2026-11-07-payroll-attendance-holidays-design.md`

## Global Constraints

- Backend owns all date math, deductions, permissions, snapshots and journal
  creation; desktop never computes payable days or deductions.
- Every tenant row carries `organization_id`; company rows carry `company_id`;
  services validate both via `AuthContext`.
- Monetary values use PostgreSQL `NUMERIC(20,2)` + Python `Decimal`; API
  returns decimal strings.
- Approved/finalized payroll runs are immutable. Calculation snapshots the
  statutory rules, attendance summary and holiday calendar.
- Missing attendance means paid; only explicit absent/unpaid evidence deducts.
- No timesheets, overtime, shift patterns, auto-fetch, or retroactive recalc.
- Tests use the isolated `_test` PostgreSQL database; no weakening of gates.

## Review Focus

- **Calendar inputs:** weekend vs holiday overlap, holiday-on-weekend,
  month boundaries, leap dates.
- **Attendance inputs:** paid vs unpaid leave, absent, present/remote/holiday,
  missing rows, non-working-day rows.
- **Deduction inputs:** pro-rata rounding, cap at gross, statutory on
  `gross_payable`, zero-unpaid regression.
- **Tenant inputs:** cross-company import, attendance and holiday identifiers.
- **Idempotency inputs:** re-import, recalc determinism, approved-run
  immutability.

---

### Task 1: National holiday dataset, import and storage

**Files:**
- Create: `backend/src/nexus_api/leave/holidays/kenya_public_holidays.json`
- Create: `backend/src/nexus_api/leave/holidays.py`
- Modify: `backend/src/nexus_api/leave/models.py`
- Modify: `backend/src/nexus_api/leave/schemas.py`
- Modify: `backend/src/nexus_api/leave/service.py`
- Modify: `backend/src/nexus_api/leave/router.py`
- Create: `backend/alembic/versions/<generated>_payroll_attendance_holidays.py`
  (part a — `company_holidays` columns)
- Test: `backend/tests/test_leave_holidays.py`
- Modify: `backend/tests/test_migrations.py`

**Interfaces:**
- Produces `load_national_calendar() -> NationalCalendar` with `version`,
  `verified_on`, `source_url`, `checksum` and `holidays: list[(date, name)]`.
- Produces `POST /leave/holidays/import-national` returning
  `{imported, skipped, calendar_version}`.
- `CompanyHoliday` gains `origin` (`company`/`national`) and
  `calendar_version`.

- [ ] **Step 1: Write failing import tests.**
  Add `test_import_national_holidays_is_idempotent_and_preserves_custom`,
  `test_national_calendar_dataset_is_well_formed`, and
  `test_working_days_exclude_weekends_and_holidays` covering year-boundary and
  holiday-on-weekend cases.

- [ ] **Step 2: Run the focused tests to verify RED.**

  ```bash
  uv run pytest backend/tests/test_leave_holidays.py -q --no-header -p no:cacheprovider
  ```

- [ ] **Step 3: Implement dataset, helper and import.**
  Create the JSON dataset by curating Kenya public holidays from an
  authoritative source recorded in the file. Implement `holidays.py`
  parsing/validation with checksum verification. Add the `origin` /
  `calendar_version` migration, list `origin` in holiday responses, and the
  idempotent import service + route with `leave.manage` permission and audit.

- [ ] **Step 4: Run focused tests to verify GREEN, then ruff + pyright.**

- [ ] **Step 5: Commit.**
  `git add backend && git commit -m "Add national holiday calendar import"`

### Task 2: Attendance summary and unpaid payroll deduction

**Files:**
- Modify: `backend/src/nexus_api/payroll/models.py`
- Modify: `backend/src/nexus_api/payroll/schemas.py`
- Modify: `backend/src/nexus_api/payroll/service.py`
- Migration (part b — payroll columns) in the same Alembic revision or a
  follow-up revision; update `backend/tests/test_migrations.py`.
- Test: `backend/tests/test_payroll_attendance.py`

**Interfaces:**
- Produces `summarize_attendance(...) -> AttendanceSummary` with
  `working_days`, `paid_days`, `unpaid_days` and per-status counts.
- Produces an `UNPAID` payslip deduction line and `unpaid_days` /
  `unpaid_deduction` / `working_days` / `paid_days` on results, plus
  `unpaid_deduction_total` on runs.
- Snapshots gain `attendance` and `calendar` blocks.

- [ ] **Step 1: Write failing deduction tests.**
  Add `test_unpaid_leave_reduces_gross_and_statutory_on_payable`,
  `test_absent_and_unpaid_cap_at_gross`,
  `test_paid_leave_and_missing_attendance_do_not_deduct`,
  `test_attendance_snapshot_is_stored_and_run_is_immutable`,
  and `test_unpaid_deduction_is_zero_without_attendance`.

- [ ] **Step 2: Run the focused tests to verify RED.**

- [ ] **Step 3: Implement summary + deduction.**
  Add the working-day helper reuse, attendance summary query (group by
  employee/date, join `leave_types.is_paid`), daily-rate deduction capped at
  gross, statutory on `gross_payable`, payslip/journal adjustment, new
  columns + response fields, and snapshot blocks.

- [ ] **Step 4: Run focused + full backend tests, ruff, pyright.**

- [ ] **Step 5: Commit.**
  `git add backend && git commit -m "Add payroll attendance unpaid deduction"`

### Task 3: Desktop holidays and unpaid surfaces

**Files:**
- Modify: `desktop/src/nexus_desktop/api/models.py`
- Modify: `desktop/src/nexus_desktop/api/client.py`
- Modify: `desktop/src/nexus_desktop/ui/pages/leave.py`
- Modify: `desktop/src/nexus_desktop/ui/pages/payroll.py`
- Modify: `desktop/tests/fake_api.py`
- Modify: `desktop/tests/test_client.py`
- Modify: `desktop/tests/test_ui_smoke.py`

**Interfaces:**
- Typed holiday `origin`/`calendar_version`, import method, unpaid fields on
  payroll models.
- Leave page: origin badges + import button; Payroll page: unpaid columns.
  All actions gated by existing permissions, run via `TaskRunner`.

- [ ] **Step 1: Write failing client + UI tests.**
  Client: import payload, holiday origin parse, unpaid fields. UI: national
  badge, import button enabled/disabled by `leave.manage`, unpaid editing
  shows API values.

- [ ] **Step 2: Run focused desktop tests to verify RED.**

- [ ] **Step 3: Implement typed contracts, fake routes and pages.**

- [ ] **Step 4: Run desktop tests, ruff, pyright.**

- [ ] **Step 5: Commit.**
  `git add desktop && git commit -m "Add desktop holiday and unpaid payroll views"`

### Task 4: Documentation, review and final gates

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/development.md` if holiday re-verification notes are needed.
- Test: repository-wide suites.

- [ ] **Step 1: Update documentation.**
  Document attendance-driven payroll, national calendar import/versioning,
  snapshot reproducibility and exclusions.

- [ ] **Step 2: Run final gates.**
  `uv run pytest -q --no-header -p no:cacheprovider`,
  `uv run ruff format --check . && uv run ruff check .`,
  `uv run pyright`,
  `uv run alembic -c backend/alembic.ini upgrade head` /
  `current`, plus live `/api/v1/health/ready` smoke.

- [ ] **Step 3: Review and commit.**
  Check tenant isolation, working-day vs payroll alignment, Decimal rounding,
  snapshot immutability, no new suppressions/placeholders.
  `git add README.md docs/architecture.md docs/development.md`
  `git commit -m "Document payroll attendance and holidays workflows"`
