# Fixed Assets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a tenant-safe fixed-asset subledger with approved capitalization, monthly straight-line depreciation, disposal gain/loss accounting, and a PyQt6 Assets workspace.

**Architecture:** Add a modular `assets` backend package with category, asset, depreciation and disposal persistence models. The backend owns all lifecycle transitions, Decimal calculations, fiscal-period checks, permissions and idempotent journals; the desktop client consumes typed REST contracts and runs every blocking call through `TaskRunner`.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.x async, PostgreSQL 18, Alembic, Pydantic, pytest, pytest-qt, PyQt6, httpx MockTransport, Ruff and Pyright strict.

**Spec:** `docs/superpowers/specs/2026-11-05-assets-design.md`

## Global Constraints

- Backend owns all calculations, permissions, lifecycle transitions and journal creation; the desktop never connects to PostgreSQL or computes book values.
- Every tenant-owned row carries `organization_id`; company-scoped rows carry `company_id`; every service command validates both through `AuthContext`.
- Monetary values use PostgreSQL `NUMERIC(20, 2)` and Python `Decimal`; API monetary responses are decimal strings.
- Posted journals must balance and are idempotent through the existing `(company_id, source_type, source_id)` journal source index.
- Approved/active asset financial fields are immutable; optimistic version checks are required for mutable commands.
- No tax depreciation, impairment, revaluation, component accounting, procurement integration, vendor master, inventory receipt or external payment execution is introduced.
- Tests must use the isolated `_test` PostgreSQL database and must not weaken existing quality gates or add suppressions.

## Review Focus

- **Financial boundary inputs:** residual value equal to cost, residual value above cost, zero useful life and non-positive cost must be rejected; covered by Task 1 asset validation tests.
- **Calendar and rounding inputs:** non-month boundaries, pre-service periods, final-period rounding and over-depreciation must produce deterministic results; covered by Task 3 depreciation tests.
- **Disposal sign inputs:** proceeds below, equal to and above net book value must post the correct loss, no gain/loss or gain; covered by Task 3 disposal tests.
- **Tenant/account inputs:** cross-company category, asset, account and funding identifiers must be rejected; covered by Task 1 and Task 2 integration tests.
- **Concurrency/idempotency inputs:** stale versions, repeated approval, repeated depreciation and repeated disposal must not duplicate state or journals; covered by Task 2 and Task 3 integration tests.

---

### Task 1: Persistence, permissions and asset/category API contracts

**Files:**
- Create: `backend/src/nexus_api/assets/__init__.py`
- Create: `backend/src/nexus_api/assets/models.py`
- Create: `backend/src/nexus_api/assets/schemas.py`
- Create: `backend/src/nexus_api/assets/service.py`
- Create: `backend/src/nexus_api/assets/router.py`
- Create: `backend/alembic/versions/<generated>_fixed_asset_register_and_lifecycle.py`
- Test: `backend/tests/test_assets.py`
- Modify: `backend/src/nexus_api/database/metadata.py`
- Modify: `backend/src/nexus_api/identity/permissions.py`
- Modify: `backend/src/nexus_api/main.py`
- Modify: `backend/tests/test_migrations.py`

**Interfaces:**
- Produces `AssetCategory`, `FixedAsset`, `AssetDepreciation` and `AssetDisposal` SQLAlchemy models with tenant/company foreign keys and optimistic versions.
- Produces typed category, asset, depreciation and disposal request/response schemas.
- Produces `/api/v1/assets` routes and service functions for category management, asset draft creation/editing, listing and detail retrieval.
- Adds `asset.view`, `asset.manage`, `asset.approve`, `asset.depreciate` and `asset.dispose` to the permission catalogue; finance managers receive the operational permissions and auditors receive view-only access.

- [ ] **Step 1: Write failing migration and contract tests.**

  Add assertions that the migration creates `asset_categories`, `fixed_assets`,
  `asset_depreciations` and `asset_disposals`, reaches the new revision, and
  round-trips through base. In `test_assets.py`, add tests named
  `test_asset_category_rejects_invalid_account_mapping_and_duplicate_code` and
  `test_asset_draft_validates_cost_residual_life_and_tenant_accounts`.

- [ ] **Step 2: Run the focused tests to verify RED.**

  Run:

  ```bash
  uv run pytest backend/tests/test_assets.py backend/tests/test_migrations.py -q --no-header -p no:cacheprovider
  ```

  Expected: collection or assertion failures because the assets module, tables
  and routes do not exist.

- [ ] **Step 3: Implement the persistence and API contracts.**

  Define category mappings for asset cost, accumulated depreciation,
  depreciation expense and disposal gain/loss accounts. Define fixed assets with
  draft/submitted/active/disposal-pending/disposed/rejected/cancelled status,
  snapshotted account IDs, base-currency cost fields, acquisition and in-service
  dates, residual value and useful-life months. Define one depreciation row per
  asset/month and one disposal row per asset. Enforce database checks for
  positive cost, `0 <= residual_value <= cost`, positive useful life, valid
  statuses, date ranges and unique company asset/category numbers.

  Implement category account-type validation, category archive protection and
  draft asset create/update/list/detail services. Asset account mappings are
  snapshotted at asset creation and category mappings cannot change after any
  asset is recorded against the category. Register the router in `main.py`,
  import all models in metadata, generate the Alembic migration and update
  migration-head assertions.

- [ ] **Step 4: Run the focused tests to verify GREEN.**

  Run the focused command from Step 2, then:

  ```bash
  uv run ruff check backend && uv run pyright
  ```

  Expected: all focused tests pass, Ruff reports no errors and Pyright reports
  zero errors, warnings or informations.

- [ ] **Step 5: Commit the persistence/API increment.**

  ```bash
  git add backend
  git commit -m "Add fixed asset register and category contracts"
  ```

### Task 2: Submission, approval and capitalization posting

**Files:**
- Modify: `backend/src/nexus_api/assets/schemas.py`
- Modify: `backend/src/nexus_api/assets/service.py`
- Modify: `backend/src/nexus_api/assets/router.py`
- Modify: `backend/src/nexus_api/accounting/service.py`
- Test: `backend/tests/test_assets.py`

**Interfaces:**
- Produces `POST /assets/{asset_id}/submit`, `/approve`, `/reject` and `/cancel`.
- Produces `post_asset_capitalization(session, context, asset, funding_account_id, reference) -> Journal`.
- Approval accepts an optimistic version, funding account ID, approval note and capitalization reference; it returns the asset with `capitalization_journal_id`.

- [ ] **Step 1: Write failing lifecycle and posting tests.**

  Add `test_asset_capitalization_requires_separation_and_posts_once` covering:
  draft creation, edit, submission, requester self-approval rejection, approval
  by a different finance user, a balanced debit to the snapshotted asset account
  and credit to the selected funding account, and a retry returning the same
  journal. Add assertions that submitted/active financial fields cannot be
  changed and stale versions return `409`.

- [ ] **Step 2: Run the lifecycle test to verify RED.**

  ```bash
  uv run pytest backend/tests/test_assets.py::test_asset_capitalization_requires_separation_and_posts_once -q --no-header -p no:cacheprovider
  ```

  Expected: FAIL because lifecycle commands and capitalization posting are not
  implemented.

- [ ] **Step 3: Implement lifecycle transitions and capitalization.**

  Lock the asset row for every command. Permit edits only in draft, require
  submitted status for approval/rejection, prohibit requester self-approval,
  and make rejected assets resubmittable while cancelled assets terminal.
  Validate the funding account belongs to the same company, is active and is an
  asset, liability or equity account. Require an open fiscal period covering the
  acquisition date. Create a posted source-keyed journal with debit asset cost
  and credit funding account before marking the asset active. On retry, return
  the existing capitalization journal without creating another journal.

- [ ] **Step 4: Run lifecycle and backend tests to verify GREEN.**

  ```bash
  uv run pytest backend/tests/test_assets.py::test_asset_capitalization_requires_separation_and_posts_once -q --no-header -p no:cacheprovider
  uv run pytest backend/tests -q --no-header -p no:cacheprovider
  ```

  Expected: the focused lifecycle test and the complete backend suite pass.

- [ ] **Step 5: Commit the capitalization increment.**

  ```bash
  git add backend
  git commit -m "Add fixed asset capitalization workflow"
  ```

### Task 3: Monthly depreciation and disposal accounting

**Files:**
- Modify: `backend/src/nexus_api/assets/schemas.py`
- Modify: `backend/src/nexus_api/assets/service.py`
- Modify: `backend/src/nexus_api/assets/router.py`
- Modify: `backend/src/nexus_api/accounting/service.py`
- Test: `backend/tests/test_assets.py`

**Interfaces:**
- Produces `GET /assets/{asset_id}/depreciation` and `POST /assets/{asset_id}/depreciate`.
- Produces `POST /assets/{asset_id}/dispose`, `/dispose/approve` and `/dispose/reject`.
- Produces `post_asset_depreciation(session, context, asset, depreciation) -> Journal` and `post_asset_disposal(session, context, asset, disposal) -> Journal`.

- [ ] **Step 1: Write failing depreciation and disposal tests.**

  Add `test_asset_depreciation_is_straight_line_rounded_and_idempotent` using a
  cost of `120000.00`, residual value `12000.00` and twelve months. Assert a
  `9000.00` monthly amount, final-period remainder, no amount beyond the
  depreciable base, calendar-boundary validation, pre-service rejection and a
  repeated period returning one journal.

  Add `test_asset_disposal_posts_gain_and_loss_with_separation` covering one
  disposal below net book value and one above it. Assert accumulated depreciation
  is cleared, the asset cost is credited, proceeds are posted to the selected
  account, the gain/loss sign is correct, the journal balances, requester
  self-approval is rejected and retry does not duplicate the disposal journal.

- [ ] **Step 2: Run the focused tests to verify RED.**

  ```bash
  uv run pytest backend/tests/test_assets.py::test_asset_depreciation_is_straight_line_rounded_and_idempotent backend/tests/test_assets.py::test_asset_disposal_posts_gain_and_loss_with_separation -q --no-header -p no:cacheprovider
  ```

  Expected: FAIL because depreciation and disposal commands do not exist.

- [ ] **Step 3: Implement locked straight-line depreciation.**

  Require an active asset, calendar-month `period_start`/`period_end`, an open
  fiscal period and a period on or after the in-service month. Sum posted prior
  depreciation under the asset lock, calculate the monthly straight-line amount,
  cap the result at the remaining depreciable base, and reject zero remaining
  value. Insert one depreciation row with a unique asset/month key, then post a
  source-keyed journal debiting expense and crediting accumulated depreciation.
  Return the existing row/journal for an idempotent repeat.

- [ ] **Step 4: Implement disposal request, approval and posting.**

  Validate disposal date, proceeds account type and open fiscal period. Create a
  pending disposal request only for active assets. Require a different user for
  approval, lock the asset and disposal rows, calculate net book value from
  posted depreciation, and post proceeds, accumulated depreciation clearing,
  asset-cost clearing and signed gain/loss lines. Mark the asset disposed only
  after the journal flush succeeds. Rejection returns the asset to active and
  repeated approval returns the existing disposal journal.

- [ ] **Step 5: Run the full backend verification.**

  ```bash
  uv run pytest backend/tests -q --no-header -p no:cacheprovider
  uv run ruff format --check backend && uv run ruff check backend && uv run pyright
  ```

  Expected: all backend tests pass; formatting, lint and strict type checking are
  clean.

- [ ] **Step 6: Commit the depreciation/disposal increment.**

  ```bash
  git add backend
  git commit -m "Add asset depreciation and disposal posting"
  ```

### Task 4: Desktop contracts and Assets workspace

**Files:**
- Modify: `desktop/src/nexus_desktop/api/models.py`
- Modify: `desktop/src/nexus_desktop/api/client.py`
- Create: `desktop/src/nexus_desktop/ui/pages/assets.py`
- Modify: `desktop/src/nexus_desktop/ui/main_window.py`
- Modify: `desktop/src/nexus_desktop/ui/common.py`
- Modify: `desktop/tests/fake_api.py`
- Modify: `desktop/tests/test_client.py`
- Modify: `desktop/tests/test_ui_smoke.py`

**Interfaces:**
- Produces typed `AssetCategory`, `FixedAsset`, `AssetDepreciation` and disposal models plus paginated register models.
- Produces client methods for category CRUD, asset CRUD, lifecycle commands, depreciation listing/posting and disposal commands.
- Produces `AssetsPage` registered behind `asset.view` with permission-gated category, approval, depreciation and disposal controls.

- [ ] **Step 1: Write failing client and offscreen UI tests.**

  Add client assertions for category mappings, asset register fields, lifecycle
  action payloads, depreciation rows and disposal responses. Extend the main
  window smoke test to assert an Assets page, category/account rendering, asset
  cost, accumulated depreciation, net book value and disabled controls when the
  corresponding permissions are absent.

- [ ] **Step 2: Run the focused desktop tests to verify RED.**

  ```bash
  uv run pytest desktop/tests/test_client.py desktop/tests/test_ui_smoke.py -q --no-header -p no:cacheprovider
  ```

  Expected: collection failures for missing asset models/page or assertion
  failures for missing navigation and fake routes.

- [ ] **Step 3: Implement typed REST contracts and deterministic fake routes.**

  Mirror the backend response fields exactly. All action methods must send the
  current version and execute through the existing authenticated `_send` path.
  Extend `FakeApi` with stable category, asset, depreciation and disposal
  payloads and action responses; do not add live API or database access to the
  desktop tests.

- [ ] **Step 4: Implement `AssetsPage`.**

  Use the existing ledger-inspired page primitives and background `TaskRunner`.
  Render categories and a paginated register. Dialogs collect asset metadata,
  approval notes, depreciation month/reference and disposal proceeds/account;
  they do not compute depreciation or net book value. Hide or disable actions
  according to `asset.*` permissions and current lifecycle state, and surface
  structured API errors through the page error label.

- [ ] **Step 5: Run desktop verification.**

  ```bash
  uv run pytest desktop/tests -q --no-header -p no:cacheprovider
  uv run ruff format --check desktop && uv run ruff check desktop && uv run pyright
  ```

  Expected: all desktop tests pass and the desktop type/lint gates are clean.

- [ ] **Step 6: Commit the desktop increment.**

  ```bash
  git add desktop
  git commit -m "Add desktop fixed assets workspace"
  ```

### Task 5: Documentation, review and final gates

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/development.md` if asset commands need operational notes
- Test: repository-wide existing test suites

**Interfaces:**
- Documents the asset module, lifecycle, accounting invariants, exclusions and
  test coverage without claiming production deployment or external integrations.

- [ ] **Step 1: Update documentation.**

  Add assets to the implemented capabilities and backend module map. Document
  straight-line book depreciation, source-keyed capitalization/depreciation/
  disposal journals, approval separation and explicit exclusions.

- [ ] **Step 2: Run the final verification commands.**

  ```bash
  uv run pytest -q --no-header -p no:cacheprovider
  uv run ruff format --check . && uv run ruff check .
  uv run pyright
  export PATH=/Library/PostgreSQL/18/bin:$PATH
  uv run alembic upgrade head
  uv run alembic current
  ```

  Expected: the full suite passes, all quality gates pass, and Alembic reports
  the new assets migration as `head`.

- [ ] **Step 3: Perform live readiness smoke verification.**

  Start the API with the repository command and request
  `/api/v1/health/ready`. Record HTTP `200` with `database: true`. If GitHub
  Actions is not configured, report remote CI as unexecuted rather than implying
  that local equivalence ran remotely.

- [ ] **Step 4: Review the final diff and commit documentation.**

  Check tenant isolation, source-key uniqueness, lifecycle immutability,
  Decimal rounding, permission boundaries, no new suppressions and no
  placeholder code. Then commit:

  ```bash
  git add README.md docs/architecture.md docs/development.md
  git commit -m "Document fixed asset accounting workflows"
  git status --short
  ```
