# Nexus Enterprise Assets Design

**Status:** Approved for implementation
**Approved:** 2026-11-05
**Scope:** Fixed-asset register, capitalization, straight-line depreciation and disposal

## Purpose

The assets domain provides a company-scoped fixed-asset subledger whose lifecycle
and accounting entries are owned by the FastAPI backend. The desktop client
displays the register and submits authenticated commands; it never calculates
book values or creates journals locally.

## Decisions

### Asset categories

Each active category maps to four company ledger accounts:

1. Asset cost account — an asset account.
2. Accumulated depreciation account — a contra-asset account represented by an
   asset account with a credit normal balance.
3. Depreciation expense account — an expense account.
4. Gain/loss on disposal account — an income-statement account used for either
   gain or loss postings.

The account IDs are copied onto an asset when it is created. A category account
mapping cannot change after assets have been recorded against that category, so
historical assets and journals retain stable accounting meaning. Categories with
active or pending assets cannot be archived.

### Asset record and lifecycle

An asset contains a tenant and company identity, immutable asset number, name,
description, serial/reference, category, acquisition date, in-service date,
cost, residual value, useful life in months, currency and the snapshotted ledger
account IDs. Assets use the company's base currency.

The lifecycle is:

```text
draft -> submitted -> active -> disposal_pending -> disposed
                  \-> rejected
draft/submitted -> cancelled
disposal_pending -> active (rejected disposal)
```

Draft fields may be edited by the requester or an asset manager. Submission
freezes the financial fields for review. Approval requires a different user
from the requester and posts capitalization. Rejected drafts may be edited and
resubmitted; cancelled records are terminal.

### Capitalization

The approval command requires a funding account selected from an active asset,
liability or equity account. Approval creates one posted journal keyed by
`(company_id, 'asset_capitalization', asset_id)`:

```text
Dr asset cost account       cost
Cr funding account          cost
```

The journal date is the asset's acquisition date and must fall in an open fiscal
period. Retried approval returns the existing journal and does not create a
second capitalization.

### Depreciation

This slice supports book straight-line depreciation only. Monthly depreciation
is calculated as:

```text
(cost - residual value) / useful life in months
```

The final period receives the rounded remaining depreciable amount so total
depreciation never exceeds `cost - residual value`. A depreciation command
contains the asset, period start, period end and reference. Periods must be
calendar-month boundaries, must be open fiscal periods and must not precede the
in-service month or follow disposal. A unique asset/period record makes the
operation idempotent.

Each posted depreciation record has a journal keyed by
`(company_id, 'asset_depreciation', depreciation_id)`:

```text
Dr depreciation expense account       depreciation amount
Cr accumulated depreciation account   depreciation amount
```

The backend locks the asset and its depreciation rows while calculating and
posting. An already-posted period returns its existing result. Fully depreciated
assets remain active until disposed.

### Disposal

The requester submits a disposal date, proceeds amount, proceeds account and
reason. The proceeds account must be active and an asset, cash or revenue
account. A different authorized user approves the disposal. The disposal date
must be on or after the acquisition and in-service dates and must fall in an
open fiscal period.

At approval, the backend computes book value from the asset cost and posted
depreciation, then creates one balanced journal keyed by
`(company_id, 'asset_disposal', asset_id)`:

- Debit proceeds account for cash/proceeds.
- Debit accumulated depreciation to clear the credit balance.
- Debit the gain/loss account for a loss, or credit it for a gain.
- Credit asset cost to remove the asset.

The asset becomes disposed only after the journal is successfully created. A
retry returns the existing disposal journal and does not duplicate proceeds or
gain/loss.

### Security and permissions

The domain adds tenant-scoped permissions:

- `asset.view` — view categories, register and schedules.
- `asset.manage` — manage categories and draft asset records.
- `asset.approve` — approve or reject capitalization submissions.
- `asset.depreciate` — post depreciation periods.
- `asset.dispose` — submit and approve disposals according to separation rules.

The requester cannot approve capitalization or disposal. Company access is
validated on every command and query. Optimistic versions protect concurrent
desktop users. All state transitions and journal IDs are audited.

## API surface

The router is mounted under `/api/v1/assets`:

- `GET/POST /categories`
- `PATCH /categories/{category_id}`
- `GET/POST /`
- `GET/PATCH /{asset_id}`
- `POST /{asset_id}/submit`
- `POST /{asset_id}/approve`
- `POST /{asset_id}/reject`
- `POST /{asset_id}/cancel`
- `GET /{asset_id}/depreciation`
- `POST /{asset_id}/depreciate`
- `POST /{asset_id}/dispose`
- `POST /{asset_id}/dispose/approve`
- `POST /{asset_id}/dispose/reject`

All monetary values are decimal strings in Pydantic responses. List endpoints
are paginated and accept company, status and category filters where applicable.

## Desktop surface

The Assets workspace contains:

- Category administration with account mappings and archive protection.
- Paginated asset register with lifecycle status, cost, accumulated depreciation
  and net book value returned by the API.
- Draft creation/editing and submission.
- Permission-gated approve/reject, depreciation and disposal actions.
- Disposal and depreciation dialogs that collect dates, accounts, references and
  reasons without performing financial calculations locally.

The desktop client uses typed models and background `TaskRunner` operations.
Tests use the existing deterministic `httpx.MockTransport` and Qt offscreen
fixture.

## Verification requirements

Backend integration tests must cover:

- Category account-type validation, duplicate codes, archive protection and
  tenant isolation.
- Asset validation, draft editing, requester/approver separation and immutable
  active fields.
- Balanced capitalization with source-key idempotency.
- Straight-line depreciation, final-period rounding, period boundaries,
  over-depreciation rejection and idempotent retries.
- Disposal gain, disposal loss, accumulated-depreciation clearing and duplicate
  prevention.
- Permission boundaries and migration upgrade/downgrade/re-upgrade.

Desktop tests must cover typed client contracts, fake API lifecycle commands,
page navigation, register rendering and permission-gated actions.

## Explicit exclusions

The implementation does not infer tax depreciation, impairment, revaluation,
component accounting, vendor procurement, inventory receipt, external payment
execution or bank integration. Those are separate domains or jurisdictional
features requiring verified requirements and contracts.
