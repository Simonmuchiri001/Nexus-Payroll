"""Deterministic in-process HTTP API used by desktop tests."""

from __future__ import annotations

import json
from typing import Any, cast

import httpx

COMPANY_ID = "11111111-1111-1111-1111-111111111111"
USER_ID = "22222222-2222-2222-2222-222222222222"
MEMBERSHIP_ID = "33333333-3333-3333-3333-333333333333"
ORGANIZATION_ID = "44444444-4444-4444-4444-444444444444"
PAY_GROUP_ID = "55555555-5555-5555-5555-555555555555"
PERIOD_ID = "66666666-6666-6666-6666-666666666666"
RUN_ID = "77777777-7777-7777-7777-777777777777"
EMPLOYEE_ID = "88888888-8888-8888-8888-888888888888"
RESULT_ID = "99999999-9999-9999-9999-999999999999"
ROLE_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ASSIGNMENT_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
ACCOUNT_ONE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
ACCOUNT_TWO = "dddddddd-dddd-dddd-dddd-dddddddddddd"
JOURNAL_ID = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
LEAVE_TYPE_ID = "ffffffff-ffff-ffff-ffff-ffffffffffff"
HOLIDAY_ID = "12121212-1212-1212-1212-121212121212"
LEAVE_BALANCE_ID = "13131313-1313-1313-1313-131313131313"
LEAVE_REQUEST_ID = "14141414-1414-1414-1414-141414141414"
ATTENDANCE_ID = "15151515-1515-1515-1515-151515151515"
LOAN_ID = "16161616-1616-1616-1616-161616161616"
LOAN_INSTALLMENT_ONE = "17171717-1717-1717-1717-171717171717"
LOAN_INSTALLMENT_TWO = "18181818-1818-1818-1818-181818181818"
BENEFIT_PLAN_ID = "19191919-1919-1919-1919-191919191919"
BENEFIT_ENROLLMENT_ID = "20202020-2020-2020-2020-202020202020"
EXPENSE_CATEGORY_ID = "21212121-2121-2121-2121-212121212121"
EXPENSE_CLAIM_ID = "22222222-2222-2222-2222-222222222222"

ALL_PERMISSIONS = [
    "organization.view",
    "organization.manage",
    "company.view",
    "company.manage",
    "user.view",
    "user.manage",
    "employee.view",
    "employee.create",
    "employee.update",
    "employee.terminate",
    "compensation.view",
    "compensation.manage",
    "leave.view",
    "leave.request",
    "leave.approve",
    "leave.manage",
    "attendance.view",
    "attendance.manage",
    "loan.view",
    "loan.request",
    "loan.approve",
    "loan.disburse",
    "benefit.view",
    "benefit.manage",
    "expense.view",
    "expense.submit",
    "expense.approve",
    "expense.pay",
    "expense.manage",
    "payroll.view",
    "payroll.calculate",
    "payroll.approve",
    "payroll.finalize",
    "payroll.post",
    "accounting.view",
    "accounting.post",
    "audit.view",
    "report.view",
    "rule.view",
    "rule.manage",
]


def _error(status_code: int, code: str, message: str) -> httpx.Response:
    return httpx.Response(
        status_code,
        json={"error": {"code": code, "message": message, "details": {}}},
    )


class FakeApi:
    def __init__(self) -> None:
        self.access_index = 1
        self.access_token = "access-1"
        self.refresh_token = "refresh-1"
        self.reject_access_tokens: set[str] = set()
        self.reject_refresh_tokens: set[str] = set()
        self.error_overrides: dict[str, tuple[int, str, str]] = {}
        self.revoked = False
        self.login_calls = 0
        self.refresh_calls = 0
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _authorized(self, request: httpx.Request) -> bool:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        return header.removeprefix("Bearer ") not in self.reject_access_tokens

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/api/v1")
        if path == "/auth/login":
            self.login_calls += 1
            return httpx.Response(
                200,
                json={
                    "access_token": self.access_token,
                    "refresh_token": self.refresh_token,
                    "token_type": "bearer",
                    "expires_in": 900,
                    "refresh_expires_in": 2_592_000,
                },
            )
        if path == "/auth/refresh":
            return self._refresh(request)
        if not self._authorized(request):
            return _error(401, "authentication_required", "Authentication required.")
        override = self.error_overrides.get(path)
        if override is not None:
            return _error(*override)
        if path == "/auth/logout":
            self.revoked = True
            return httpx.Response(204)
        if path == "/auth/me":
            return httpx.Response(200, json=self._me())
        if path == "/employees":
            return httpx.Response(200, json=self._employee_page())
        if path == "/payroll/pay-groups" and request.method == "POST":
            body = cast(dict[str, Any], json.loads(request.content))
            group = self._pay_group()
            group["code"] = str(body["code"]).upper()
            group["name"] = str(body["name"])
            return httpx.Response(201, json=group)
        if path == "/payroll/pay-groups":
            return httpx.Response(200, json=[self._pay_group()])
        if path == "/payroll/periods" and request.method == "POST":
            return httpx.Response(201, json=self._period())
        if path == "/payroll/periods":
            return httpx.Response(200, json=[self._period()])
        if path == "/payroll/runs":
            return httpx.Response(200, json=[self._run()])
        if path.startswith("/payroll/runs/") and path.endswith("/results"):
            return httpx.Response(200, json=[self._result()])
        if path == "/accounting/accounts":
            return httpx.Response(200, json=[self._account_one(), self._account_two()])
        if path == "/accounting/trial-balance":
            return httpx.Response(200, json=self._trial_balance())
        if path == "/accounting/journals":
            return httpx.Response(200, json=self._journal_page())
        if path == "/leave/requests":
            return httpx.Response(200, json=self._leave_request_page())
        if path == "/leave/types":
            return httpx.Response(200, json=[self._leave_type()])
        if path == "/leave/holidays":
            return httpx.Response(200, json=[self._holiday()])
        if path == "/attendance":
            return httpx.Response(200, json=self._attendance_page())
        if path == "/loans" and request.method == "POST":
            return httpx.Response(201, json=self._loan_from_request(request))
        if path == "/loans":
            return httpx.Response(200, json=self._loan_page())
        if path.startswith("/loans/"):
            parts = path.split("/")
            if len(parts) == 3:
                return httpx.Response(200, json=self._loan_detail())
            if len(parts) == 4 and parts[3] in {"approve", "reject", "cancel", "disburse"}:
                return httpx.Response(200, json=self._loan_detail(parts[3]))
        if path == "/benefits/plans" and request.method == "POST":
            return httpx.Response(201, json=self._benefit_plan_from_request(request))
        if path == "/benefits/plans":
            return httpx.Response(200, json=[self._benefit_plan()])
        if path.startswith("/benefits/plans/") and request.method == "PATCH":
            return httpx.Response(200, json=self._benefit_plan_from_request(request, update=True))
        if path == "/benefits/enrollments" and request.method == "POST":
            return httpx.Response(201, json=self._benefit_enrollment_from_request(request))
        if path == "/benefits/enrollments":
            return httpx.Response(200, json=self._benefit_enrollment_page())
        if path.startswith("/benefits/enrollments/") and path.endswith("/end"):
            return httpx.Response(200, json=self._ended_enrollment(request))
        if path == "/expenses/categories" and request.method == "POST":
            return httpx.Response(201, json=self._expense_category_from_request(request))
        if path == "/expenses/categories":
            return httpx.Response(200, json=[self._expense_category()])
        if path.startswith("/expenses/categories/") and request.method == "PATCH":
            return httpx.Response(
                200, json=self._expense_category_from_request(request, update=True)
            )
        if path == "/expenses/claims" and request.method == "POST":
            return httpx.Response(201, json=self._expense_claim_from_request(request))
        if path == "/expenses/claims":
            return httpx.Response(200, json=self._expense_claim_page())
        if path.startswith("/expenses/claims/"):
            parts = path.split("/")
            if len(parts) == 4 and request.method == "PATCH":
                return httpx.Response(
                    200, json=self._expense_claim_from_request(request, update=True)
                )
            if len(parts) == 4:
                return httpx.Response(200, json=self._expense_claim())
            if len(parts) == 5 and parts[4] == "reimburse":
                return httpx.Response(
                    200,
                    json=self._expense_claim_from_request(request, status="reimbursed"),
                )
            if len(parts) == 5 and parts[4] in {"submit", "approve", "reject", "cancel"}:
                action_status = {"submit": "submitted", "approve": "approved"}
                action_status.update({"reject": "rejected", "cancel": "cancelled"})
                return httpx.Response(
                    200,
                    json=self._expense_claim_from_request(request, status=action_status[parts[4]]),
                )
        if path == "/users":
            return httpx.Response(200, json=self._membership_page())
        if path == "/roles":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": ROLE_ID,
                        "code": "org_admin",
                        "name": "Organisation Administrator",
                        "description": "Full organisation administration.",
                        "is_system": True,
                    }
                ],
            )
        return _error(404, "not_found", "The requested test route does not exist.")

    def _refresh(self, request: httpx.Request) -> httpx.Response:
        self.refresh_calls += 1
        body = cast(dict[str, Any], json.loads(request.content))
        supplied = str(body.get("refresh_token", ""))
        if supplied in self.reject_refresh_tokens or supplied != self.refresh_token:
            return _error(401, "invalid_refresh_token", "The refresh token is invalid.")
        self.access_index += 1
        self.access_token = f"access-{self.access_index}"
        self.refresh_token = f"refresh-{self.access_index}"
        return httpx.Response(
            200,
            json={
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "token_type": "bearer",
                "expires_in": 900,
                "refresh_expires_in": 2_592_000,
            },
        )

    @staticmethod
    def _me() -> dict[str, Any]:
        return {
            "user": {
                "id": USER_ID,
                "email": "admin@nexus.co.ke",
                "full_name": "Amina Admin",
                "is_platform_admin": False,
            },
            "organization_id": ORGANIZATION_ID,
            "organization_slug": "nexus-demo",
            "organization_name": "Nexus Demo",
            "membership_id": MEMBERSHIP_ID,
            "permissions": ALL_PERMISSIONS,
            "companies": [
                {
                    "id": COMPANY_ID,
                    "code": "ND",
                    "name": "Nexus Demo Ltd",
                    "base_currency": "KES",
                    "country_code": "KE",
                    "status": "active",
                }
            ],
        }

    @staticmethod
    def _employee_page() -> dict[str, Any]:
        return {
            "items": [
                {
                    "id": EMPLOYEE_ID,
                    "company_id": COMPANY_ID,
                    "employee_number": "EMP-0001",
                    "first_name": "Achieng",
                    "middle_name": None,
                    "last_name": "Otieno",
                    "work_email": "achieng@nexus.co.ke",
                    "job_title": "Finance Analyst",
                    "hire_date": "2026-01-05",
                    "termination_date": None,
                    "status": "active",
                    "is_payroll_eligible": True,
                    "version": 1,
                }
            ],
            "total": 1,
            "page": 1,
            "page_size": 25,
        }

    @staticmethod
    def _pay_group() -> dict[str, Any]:
        return {
            "id": PAY_GROUP_ID,
            "company_id": COMPANY_ID,
            "code": "MONTHLY",
            "name": "Monthly payroll",
            "frequency": "monthly",
            "currency": "KES",
            "status": "active",
        }

    @staticmethod
    def _period() -> dict[str, Any]:
        return {
            "id": PERIOD_ID,
            "pay_group_id": PAY_GROUP_ID,
            "period_start": "2026-10-01",
            "period_end": "2026-10-31",
            "payment_date": "2026-11-05",
            "status": "open",
        }

    @staticmethod
    def _run() -> dict[str, Any]:
        return {
            "id": RUN_ID,
            "company_id": COMPANY_ID,
            "pay_group_id": PAY_GROUP_ID,
            "period_start": "2026-10-01",
            "period_end": "2026-10-31",
            "payment_date": "2026-11-05",
            "run_number": "PR-202610-0001",
            "status": "draft",
            "currency": "KES",
            "employee_count": 1,
            "gross_total": "100000.00",
            "taxable_total": "91430.00",
            "paye_total": "19812.35",
            "employee_statutory_total": "18570.00",
            "employer_statutory_total": "5820.00",
            "deductions_total": "28382.35",
            "loan_deduction_total": "0.00",
            "benefit_employee_total": "0.00",
            "benefit_employer_total": "0.00",
            "net_total": "71617.65",
            "employer_cost_total": "105820.00",
            "prepared_by": USER_ID,
            "approved_by": None,
            "calculated_at": None,
            "approved_at": None,
            "finalized_at": None,
            "posted_at": None,
            "journal_id": None,
            "version": 1,
            "created_at": "2026-10-22T09:00:00Z",
        }

    @staticmethod
    def _result() -> dict[str, Any]:
        return {
            "id": RESULT_ID,
            "employee_id": EMPLOYEE_ID,
            "employee_number": "EMP-0001",
            "employee_name": "Achieng Otieno",
            "currency": "KES",
            "days_in_period": 31,
            "payable_days": 31,
            "base_salary": "85000.00",
            "gross": "100000.00",
            "taxable_income": "91430.00",
            "paye": "19812.35",
            "nssf_employee": "2400.00",
            "shif_employee": "2740.00",
            "housing_levy_employee": "1500.00",
            "loan_deduction": "0.00",
            "benefit_employee_deduction": "0.00",
            "benefit_employer_contribution": "0.00",
            "total_deductions": "28382.35",
            "net_pay": "71617.65",
            "nssf_employer": "2400.00",
            "housing_levy_employer": "1500.00",
            "nita": "0.00",
            "employer_cost": "105820.00",
        }

    @staticmethod
    def _account_one() -> dict[str, Any]:
        return {
            "id": ACCOUNT_ONE,
            "company_id": COMPANY_ID,
            "code": "5000",
            "name": "Payroll expense",
            "account_type": "expense",
            "normal_balance": "debit",
            "is_system": True,
            "is_active": True,
        }

    @staticmethod
    def _account_two() -> dict[str, Any]:
        return {
            "id": ACCOUNT_TWO,
            "company_id": COMPANY_ID,
            "code": "2200",
            "name": "Net pay payable",
            "account_type": "liability",
            "normal_balance": "credit",
            "is_system": True,
            "is_active": True,
        }

    @staticmethod
    def _trial_balance() -> dict[str, Any]:
        return {
            "company_id": COMPANY_ID,
            "as_of": "2026-11-04",
            "currency": "KES",
            "rows": [
                {
                    "account_id": ACCOUNT_ONE,
                    "code": "5000",
                    "name": "Payroll expense",
                    "account_type": "expense",
                    "debit": "105820.00",
                    "credit": "0.00",
                    "balance": "105820.00",
                },
                {
                    "account_id": ACCOUNT_TWO,
                    "code": "2200",
                    "name": "Net pay payable",
                    "account_type": "liability",
                    "debit": "0.00",
                    "credit": "105820.00",
                    "balance": "-105820.00",
                },
            ],
            "total_debit": "105820.00",
            "total_credit": "105820.00",
            "balanced": True,
        }

    @staticmethod
    def _journal_page() -> dict[str, Any]:
        return {
            "items": [
                {
                    "id": JOURNAL_ID,
                    "company_id": COMPANY_ID,
                    "journal_number": "JE-2026-0001",
                    "journal_date": "2026-10-31",
                    "description": "Payroll posting PR-202610-0001",
                    "reference": "PR-202610-0001",
                    "status": "posted",
                    "currency": "KES",
                    "total_debit": "105820.00",
                    "total_credit": "105820.00",
                    "source_type": "payroll_run",
                    "created_at": "2026-10-31T18:00:00Z",
                }
            ],
            "total": 1,
            "page": 1,
            "page_size": 100,
        }

    @staticmethod
    def _leave_type() -> dict[str, Any]:
        return {
            "id": LEAVE_TYPE_ID,
            "company_id": COMPANY_ID,
            "code": "ANNUAL",
            "name": "Annual leave",
            "description": "Paid annual leave",
            "is_paid": True,
            "requires_balance": True,
            "annual_entitlement_days": "21.00",
            "max_carryover_days": "5.00",
            "status": "active",
            "version": 1,
        }

    @staticmethod
    def _holiday() -> dict[str, Any]:
        return {
            "id": HOLIDAY_ID,
            "company_id": COMPANY_ID,
            "holiday_date": "2026-01-05",
            "name": "Company Founders Day",
        }

    @staticmethod
    def _leave_request_page() -> dict[str, Any]:
        return {
            "items": [
                {
                    "id": LEAVE_REQUEST_ID,
                    "company_id": COMPANY_ID,
                    "employee_id": EMPLOYEE_ID,
                    "employee_number": "EMP-0001",
                    "employee_name": "Achieng Otieno",
                    "leave_type_id": LEAVE_TYPE_ID,
                    "leave_type_code": "ANNUAL",
                    "leave_type_name": "Annual leave",
                    "leave_year": 2026,
                    "start_date": "2026-02-02",
                    "end_date": "2026-02-06",
                    "requested_days": "5.00",
                    "balance_tracked": True,
                    "reason": "Family travel",
                    "status": "pending",
                    "requested_by": USER_ID,
                    "requested_by_name": "Amina Admin",
                    "decided_by": None,
                    "decided_by_name": None,
                    "decided_at": None,
                    "decision_note": None,
                    "cancelled_by": None,
                    "cancelled_at": None,
                    "created_at": "2026-01-20T10:00:00Z",
                    "version": 1,
                }
            ],
            "total": 1,
            "page": 1,
            "page_size": 100,
        }

    @staticmethod
    def _attendance_page() -> dict[str, Any]:
        return {
            "items": [
                {
                    "id": ATTENDANCE_ID,
                    "company_id": COMPANY_ID,
                    "employee_id": EMPLOYEE_ID,
                    "employee_number": "EMP-0001",
                    "employee_name": "Achieng Otieno",
                    "work_date": "2026-11-02",
                    "status": "present",
                    "source": "manual",
                    "leave_request_id": None,
                    "note": "On site",
                    "created_at": "2026-11-02T08:00:00Z",
                    "version": 1,
                }
            ],
            "total": 1,
            "page": 1,
            "page_size": 100,
        }

    @staticmethod
    def _loan(status: str = "disbursed") -> dict[str, Any]:
        return {
            "id": LOAN_ID,
            "organization_id": ORGANIZATION_ID,
            "company_id": COMPANY_ID,
            "employee_id": EMPLOYEE_ID,
            "employee_number": "EMP-0001",
            "employee_name": "Achieng Otieno",
            "loan_number": "LN-2026-0001",
            "kind": "loan",
            "principal_amount": "60000.00",
            "currency": "KES",
            "interest_rate_annual": "12.0000",
            "term_months": 12,
            "purpose": "Medical cover and business costs",
            "status": status,
            "requested_by": USER_ID,
            "requested_by_name": "Amina Admin",
            "decided_by": USER_ID,
            "decided_by_name": "Amina Admin",
            "decided_at": "2026-09-15T09:00:00Z",
            "decision_note": "Approved within policy.",
            "cancelled_by": None,
            "cancelled_at": None,
            "disbursed_by": USER_ID,
            "disbursed_on": "2026-09-20",
            "disbursed_at": "2026-09-20T10:00:00Z",
            "disbursement_reference": "DISB-LN-2026-0001",
            "first_due_date": "2026-09-30",
            "journal_id": JOURNAL_ID,
            "closed_at": None,
            "total_repayable": "63900.00",
            "outstanding_principal": "40000.00",
            "installments_paid": 4,
            "installments_total": 12,
            "created_at": "2026-09-14T08:00:00Z",
            "version": 2,
        }

    def _loan_page(self) -> dict[str, Any]:
        return {
            "items": [self._loan()],
            "total": 1,
            "page": 1,
            "page_size": 100,
        }

    def _loan_detail(self, action: str | None = None) -> dict[str, Any]:
        status = {
            "approve": "approved",
            "reject": "rejected",
            "cancel": "cancelled",
            "disburse": "disbursed",
        }.get(action or "", "disbursed")
        loan = self._loan(status)
        if status == "approved":
            installments: list[dict[str, Any]] = []
        else:
            installments = [
                {
                    "id": LOAN_INSTALLMENT_ONE,
                    "loan_id": LOAN_ID,
                    "sequence": 1,
                    "due_date": "2026-09-30",
                    "principal_due": "8000.00",
                    "interest_due": "200.00",
                    "amount_due": "8200.00",
                    "status": "deducted",
                    "payroll_run_id": RUN_ID,
                    "payroll_result_id": RESULT_ID,
                    "deducted_at": "2026-09-30T18:00:00Z",
                },
                {
                    "id": LOAN_INSTALLMENT_TWO,
                    "loan_id": LOAN_ID,
                    "sequence": 2,
                    "due_date": "2026-10-31",
                    "principal_due": "8000.00",
                    "interest_due": "150.00",
                    "amount_due": "8150.00",
                    "status": "scheduled",
                    "payroll_run_id": None,
                    "payroll_result_id": None,
                    "deducted_at": None,
                },
            ]
        return {"loan": loan, "installments": installments}

    def _loan_from_request(self, request: httpx.Request) -> dict[str, Any]:
        body = cast(dict[str, Any], json.loads(request.content))
        loan = self._loan("pending")
        loan["employee_id"] = body["employee_id"]
        loan["kind"] = body["kind"]
        loan["principal_amount"] = body["principal_amount"]
        loan["interest_rate_annual"] = body["interest_rate_annual"]
        loan["term_months"] = body["term_months"]
        loan["purpose"] = body["purpose"]
        loan["total_repayable"] = body["principal_amount"]
        loan["outstanding_principal"] = body["principal_amount"]
        loan["installments_paid"] = 0
        loan["installments_total"] = 0
        loan["loan_number"] = "ADV-2026-0001" if body["kind"] == "advance" else "LN-2026-0002"
        loan["decision_note"] = None
        loan["disbursed_on"] = None
        loan["disbursement_reference"] = None
        loan["first_due_date"] = None
        loan["journal_id"] = None
        loan["version"] = 1
        return loan

    @staticmethod
    def _benefit_plan(status: str = "active") -> dict[str, Any]:
        return {
            "id": BENEFIT_PLAN_ID,
            "organization_id": ORGANIZATION_ID,
            "company_id": COMPANY_ID,
            "code": "MED",
            "name": "Comprehensive medical cover",
            "category": "medical",
            "employee_contribution": "2500.00",
            "employer_contribution": "5000.00",
            "currency": "KES",
            "description": "Voluntary staff medical scheme",
            "status": status,
            "created_at": "2026-01-02T08:00:00Z",
            "version": 1,
        }

    @staticmethod
    def _benefit_enrollment(status: str = "active") -> dict[str, Any]:
        return {
            "id": BENEFIT_ENROLLMENT_ID,
            "organization_id": ORGANIZATION_ID,
            "company_id": COMPANY_ID,
            "employee_id": EMPLOYEE_ID,
            "employee_number": "EMP-0001",
            "employee_name": "Achieng Otieno",
            "plan_id": BENEFIT_PLAN_ID,
            "plan_code": "MED",
            "plan_name": "Comprehensive medical cover",
            "plan_category": "medical",
            "effective_from": "2026-01-01",
            "effective_to": "2026-10-31" if status == "ended" else None,
            "employee_contribution": "2500.00",
            "employer_contribution": "5000.00",
            "currency": "KES",
            "status": status,
            "notes": "Employee elected family cover.",
            "ended_by": USER_ID if status == "ended" else None,
            "ended_at": "2026-10-31T12:00:00Z" if status == "ended" else None,
            "end_note": "Cover ended at the employee's request." if status == "ended" else None,
            "created_at": "2026-01-02T09:00:00Z",
            "version": 1,
        }

    def _benefit_enrollment_page(self) -> dict[str, Any]:
        return {
            "items": [self._benefit_enrollment()],
            "total": 1,
            "page": 1,
            "page_size": 25,
        }

    def _benefit_plan_from_request(
        self, request: httpx.Request, *, update: bool = False
    ) -> dict[str, Any]:
        body = cast(dict[str, Any], json.loads(request.content))
        plan = self._benefit_plan(str(body.get("status", "active")))
        if update:
            for field in (
                "name",
                "category",
                "employee_contribution",
                "employer_contribution",
                "description",
            ):
                if field in body:
                    plan[field] = body[field]
            plan["version"] = 2
        else:
            plan["code"] = str(body["code"]).upper()
            plan["name"] = str(body["name"])
            plan["category"] = body["category"]
            plan["employee_contribution"] = body["employee_contribution"]
            plan["employer_contribution"] = body["employer_contribution"]
            plan["description"] = body["description"]
        return plan

    def _benefit_enrollment_from_request(self, request: httpx.Request) -> dict[str, Any]:
        body = cast(dict[str, Any], json.loads(request.content))
        enrollment = self._benefit_enrollment("active")
        enrollment["employee_id"] = body["employee_id"]
        enrollment["plan_id"] = body["plan_id"]
        enrollment["effective_from"] = body["effective_from"]
        enrollment["effective_to"] = body["effective_to"]
        enrollment["employee_contribution"] = body["employee_contribution"]
        enrollment["employer_contribution"] = body["employer_contribution"]
        enrollment["notes"] = body["notes"]
        enrollment["version"] = 1
        return enrollment

    def _ended_enrollment(self, request: httpx.Request) -> dict[str, Any]:
        body = cast(dict[str, Any], json.loads(request.content))
        enrollment = self._benefit_enrollment("ended")
        enrollment["effective_to"] = body["effective_to"]
        enrollment["end_note"] = body["note"]
        enrollment["version"] = 2
        return enrollment

    @staticmethod
    def _expense_category(status: str = "active") -> dict[str, Any]:
        return {
            "id": EXPENSE_CATEGORY_ID,
            "organization_id": ORGANIZATION_ID,
            "company_id": COMPANY_ID,
            "code": "TRAVEL",
            "name": "Business travel",
            "description": "Approved business travel expenses.",
            "expense_account_id": ACCOUNT_ONE,
            "expense_account_code": "5000",
            "expense_account_name": "Payroll expense",
            "status": status,
            "created_at": "2026-01-02T08:00:00Z",
            "version": 1,
        }

    @staticmethod
    def _expense_claim(status: str = "draft") -> dict[str, Any]:
        return {
            "id": EXPENSE_CLAIM_ID,
            "organization_id": ORGANIZATION_ID,
            "company_id": COMPANY_ID,
            "employee_id": EMPLOYEE_ID,
            "employee_number": "EMP-0001",
            "employee_name": "Achieng Otieno",
            "category_id": EXPENSE_CATEGORY_ID,
            "category_code": "TRAVEL",
            "category_name": "Business travel",
            "expense_account_id": ACCOUNT_ONE,
            "expense_account_code": "5000",
            "expense_account_name": "Payroll expense",
            "claim_number": "EXP-2026-0001",
            "expense_date": "2026-11-03",
            "description": "Client workshop transport",
            "amount": "2750.00",
            "currency": "KES",
            "receipt_number": "RCT-2026-1103",
            "receipt_date": "2026-11-03",
            "receipt_merchant": "Nairobi Shuttle Co.",
            "status": status,
            "requested_by": USER_ID,
            "requested_by_name": "Amina Admin",
            "submitted_at": "2026-11-04T09:00:00Z" if status != "draft" else None,
            "decided_by": USER_ID if status in {"approved", "rejected"} else None,
            "decided_by_name": "Amina Admin" if status in {"approved", "rejected"} else None,
            "decided_at": "2026-11-04T10:00:00Z" if status in {"approved", "rejected"} else None,
            "decision_note": "Business purpose verified." if status == "approved" else None,
            "paid_by": USER_ID if status == "reimbursed" else None,
            "paid_by_name": "Amina Admin" if status == "reimbursed" else None,
            "paid_on": "2026-11-05" if status == "reimbursed" else None,
            "paid_at": "2026-11-05T11:00:00Z" if status == "reimbursed" else None,
            "payment_reference": "BANK-EXP-0001" if status == "reimbursed" else None,
            "journal_id": JOURNAL_ID if status == "reimbursed" else None,
            "cancelled_by": USER_ID if status == "cancelled" else None,
            "cancelled_by_name": "Amina Admin" if status == "cancelled" else None,
            "cancelled_at": "2026-11-04T12:00:00Z" if status == "cancelled" else None,
            "created_at": "2026-11-04T08:00:00Z",
            "version": 1,
        }

    @staticmethod
    def _expense_claim_page() -> dict[str, Any]:
        return {
            "items": [FakeApi._expense_claim()],
            "total": 1,
            "page": 1,
            "page_size": 25,
        }

    def _expense_category_from_request(
        self, request: httpx.Request, *, update: bool = False
    ) -> dict[str, Any]:
        body = cast(dict[str, Any], json.loads(request.content))
        category = self._expense_category(str(body.get("status", "active")))
        if update:
            for field in ("name", "expense_account_id", "description"):
                if field in body:
                    category[field] = body[field]
            category["version"] = 2
        else:
            category["code"] = str(body["code"]).upper()
            category["name"] = str(body["name"])
            category["expense_account_id"] = body["expense_account_id"]
            category["description"] = body.get("description")
        return category

    def _expense_claim_from_request(
        self,
        request: httpx.Request,
        *,
        status: str | None = None,
        update: bool = False,
    ) -> dict[str, Any]:
        body = cast(dict[str, Any], json.loads(request.content))
        claim = self._expense_claim(status or "draft")
        for field in (
            "employee_id",
            "category_id",
            "expense_date",
            "description",
            "amount",
            "receipt_number",
            "receipt_date",
            "receipt_merchant",
        ):
            if field in body:
                claim[field] = body[field]
        claim["version"] = 2 if update else 1
        return claim

    @staticmethod
    def _membership_page() -> dict[str, Any]:
        return {
            "items": [
                {
                    "id": MEMBERSHIP_ID,
                    "organization_id": ORGANIZATION_ID,
                    "user_id": USER_ID,
                    "email": "admin@nexus.co.ke",
                    "full_name": "Amina Admin",
                    "user_active": True,
                    "status": "active",
                    "default_company_id": COMPANY_ID,
                    "role_assignments": [
                        {
                            "id": ASSIGNMENT_ID,
                            "role_code": "org_admin",
                            "role_name": "Organisation Administrator",
                            "company_id": None,
                            "branch_id": None,
                        }
                    ],
                    "created_at": "2026-01-01T08:00:00Z",
                }
            ],
            "total": 1,
            "page": 1,
            "page_size": 25,
        }
