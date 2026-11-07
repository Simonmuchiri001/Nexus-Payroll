# Nexus Enterprise Payroll Attendance and Holidays Design

**Date:** 2026-11-07
**Scope:** Payroll deductions for unpaid leave / absence / attendance, plus a
seeded Kenya national public-holiday calendar.

## Purpose

Payroll currently prorates by calendar days and ignores the leave/attendance
register. This slice makes payroll consume attendance: unpaid leave and
absences reduce pay, while the Kenya national holiday calendar gives every
company a complete working-day calendar without manual entry. The backend
owns all calendar math, deductions, permissions and snapshots; the desktop
only displays and submits commands.

## Decisions

### National holiday calendar

- A versioned JSON dataset lives at
  `backend/src/nexus_api/leave/holidays/kenya_public_holidays.json`, mirroring
  the statutory-rule pattern: `version`, `source_url`, `verified_on`,
  `checksum`, and a `holidays: [{date, name}]` list for the Kenya public
  holidays in force.
- The dataset is curated and verified offline. Rule updates are code changes
  with source URLs and tests. The application never downloads holidays at
  runtime, matching the payroll statutory-rule precedent.
- `POST /leave/holidays/import-national` (company-scoped, `leave.manage`)
  seeds the active dataset version into `company_holidays` for that company.
  Import is idempotent: a date that already exists (company-custom or
  previously imported) is skipped and never overwritten. The response
  reports how many holidays were imported vs skipped, plus the calendar
  version. The operation is audited.
- `company_holidays` gains an `origin` column (`company` or `national`) and a
  `calendar_version` column so company-custom holidays remain distinguishable
  from imported national holidays. Existing rows default to `company`.

### Working days

- Working days are Monday–Friday excluding company holidays (national +
  custom) that fall on weekdays. This matches the existing leave working-day
  helper and keeps one definition across leave and payroll.
- Saturday/Sunday are non-working. No shift-pattern or overtime model is
  introduced.

### Attendance summary

- For a payroll period, each employee gets an attendance summary computed
  server-side from `attendance_records` plus `leave_types.is_paid`:
  - `present`, `remote`, paid `leave` and `holiday` count as paid;
  - `absent` and unpaid `leave` (manual or leave-sourced with
    `is_paid = false`) count as unpaid;
  - only working days are counted for both paid and unpaid totals.
- Attendance rows outside working days (e.g. a holiday) never create unpaid
  days. Leave-sourced rows inherit `is_paid` from their leave type.
- Missing attendance dates are treated as paid (no deduction). Payroll must
  not break for employees with no attendance register; only explicit
  absent/unpaid evidence deducts.

### Unpaid deduction

- Let `monthly_total` be the employee's full monthly compensation
  (base + allowances). `days_in_period` is the calendar-day count.
  The daily rate is `monthly_total / days_in_period` (Decimal, half-up to
  `NUMERIC(20,2)`).
- `unpaid_deduction = daily_rate * unpaid_working_days`, capped at the
  prorated gross so net can never go negative from attendance alone.
- `gross_payable = gross - unpaid_deduction`. Taxable income, employee
  statutory (PAYE/NSSF/SHIF/housing levy) and employer statutory are all
  calculated on `gross_payable`. Loan and benefit deductions are then
  checked against net available exactly as today.
- The payslip shows an `UNPAID` deduction line for transparency. The
  accounting journal posts expense at `gross_payable` (no payable is created
  for the unpaid amount), so the journal stays balanced without a credit leg
  for unpaid time.
- Every calculated run snapshots the attendance summary, the working-day
  calendar (dates + national calendar version) and the unpaid inputs, alongside
  the existing statutory snapshot. Approved/finalized runs are immutable, so
  later attendance or holiday edits cannot rewrite history.

### Data changes

- Migration adds to `payroll_results`: `unpaid_days` (int), `unpaid_deduction`
  (numeric 20,2), `working_days` (int), `paid_days` (int); and to
  `payroll_runs`: `unpaid_deduction_total` (numeric 20,2). Defaults preserve
  existing runs (`unpaid = 0`).
- Migration adds to `company_holidays`: `origin` (`company`/`national`,
  default `company`) and `calendar_version` (nullable text).

## API surface

- `POST /leave/holidays/import-national?company_id=…` →
  `{ imported, skipped, calendar_version, holidays: […] }`
- `GET /leave/holidays?company_id=…&year=…` (existing list, now includes
  `origin` / `calendar_version` per row).
- Payroll run result responses gain `unpaid_days`, `unpaid_deduction`,
  `working_days`, `paid_days`; run totals gain `unpaid_deduction_total`.
  Calculation snapshots gain an `attendance` block and a `calendar` block.

All monetary values remain decimal strings.

## Desktop surface

- Leave page lists holidays with an origin badge (National/Company) and an
  "Import national holidays" action gated by `leave.manage`.
- Payroll results table shows unpaid days and unpaid deduction alongside
  net pay. No local day math — the API returns every figure.

## Security and permissions

No new permissions. Import requires `leave.manage`; calculation requires
`payroll.calculate` as today. Every command validates company access through
`AuthContext`. Attendance/holiday writes are audited.

## Verification requirements

- National import: idempotent re-import, skip-on-custom-conflict, tenant
  isolation, audit event, migration round-trip.
- Working-day math: weekends excluded, holidays excluded, holiday-on-weekend
  not double-counted.
- Attendance: paid leave vs unpaid leave vs absent vs present, missing = paid,
  non-working-day attendance ignored.
- Payroll: deduction pro-rata correctness, cap at gross, statutory on
  `gross_payable`, snapshot reproducibility, approved-run immutability.
- Desktop: holiday badges + import payload, unpaid columns rendering,
  permission-gated actions.

## Explicit exclusions

No timesheet/clock-in, no overtime, no shift patterns, no auto-fetch from
government APIs, no leave-balance deduction changes, no retroactive recalc of
approved runs. National holiday data must be re-verified for each year before
production use, like statutory rules.
