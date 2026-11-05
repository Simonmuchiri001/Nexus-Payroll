"""Permission catalogue and built-in role definitions."""

from __future__ import annotations

from enum import StrEnum


class PermissionCode(StrEnum):
    ORGANIZATION_VIEW = "organization.view"
    ORGANIZATION_MANAGE = "organization.manage"
    COMPANY_VIEW = "company.view"
    COMPANY_MANAGE = "company.manage"
    USER_VIEW = "user.view"
    USER_MANAGE = "user.manage"
    EMPLOYEE_VIEW = "employee.view"
    EMPLOYEE_CREATE = "employee.create"
    EMPLOYEE_UPDATE = "employee.update"
    EMPLOYEE_TERMINATE = "employee.terminate"
    COMPENSATION_VIEW = "compensation.view"
    COMPENSATION_MANAGE = "compensation.manage"
    LEAVE_VIEW = "leave.view"
    LEAVE_REQUEST = "leave.request"
    LEAVE_APPROVE = "leave.approve"
    LEAVE_MANAGE = "leave.manage"
    ATTENDANCE_VIEW = "attendance.view"
    ATTENDANCE_MANAGE = "attendance.manage"
    LOAN_VIEW = "loan.view"
    LOAN_REQUEST = "loan.request"
    LOAN_APPROVE = "loan.approve"
    LOAN_DISBURSE = "loan.disburse"
    PAYROLL_VIEW = "payroll.view"
    PAYROLL_CALCULATE = "payroll.calculate"
    PAYROLL_APPROVE = "payroll.approve"
    PAYROLL_FINALIZE = "payroll.finalize"
    PAYROLL_POST = "payroll.post"
    ACCOUNTING_VIEW = "accounting.view"
    ACCOUNTING_POST = "accounting.post"
    AUDIT_VIEW = "audit.view"
    REPORT_VIEW = "report.view"
    RULE_VIEW = "rule.view"
    RULE_MANAGE = "rule.manage"


PERMISSION_DESCRIPTIONS: dict[PermissionCode, tuple[str, str]] = {
    PermissionCode.ORGANIZATION_VIEW: ("organization", "View tenant and company settings"),
    PermissionCode.ORGANIZATION_MANAGE: ("organization", "Manage tenant and company settings"),
    PermissionCode.COMPANY_VIEW: ("organization", "View companies and organisation units"),
    PermissionCode.COMPANY_MANAGE: ("organization", "Manage companies, branches and departments"),
    PermissionCode.USER_VIEW: ("identity", "View users, memberships and roles"),
    PermissionCode.USER_MANAGE: ("identity", "Manage users, memberships and role assignments"),
    PermissionCode.EMPLOYEE_VIEW: ("employees", "View employee records"),
    PermissionCode.EMPLOYEE_CREATE: ("employees", "Create employee records"),
    PermissionCode.EMPLOYEE_UPDATE: ("employees", "Update employee records"),
    PermissionCode.EMPLOYEE_TERMINATE: ("employees", "Terminate employees"),
    PermissionCode.COMPENSATION_VIEW: ("compensation", "View compensation records"),
    PermissionCode.COMPENSATION_MANAGE: ("compensation", "Create and change compensation"),
    PermissionCode.LEAVE_VIEW: ("leave", "View leave types, balances and requests"),
    PermissionCode.LEAVE_REQUEST: ("leave", "Record and cancel leave requests"),
    PermissionCode.LEAVE_APPROVE: ("leave", "Approve or reject leave requests"),
    PermissionCode.LEAVE_MANAGE: ("leave", "Manage leave types, holidays and balances"),
    PermissionCode.ATTENDANCE_VIEW: ("attendance", "View attendance records"),
    PermissionCode.ATTENDANCE_MANAGE: ("attendance", "Record and amend attendance"),
    PermissionCode.LOAN_VIEW: ("loans", "View employee loans, advances and schedules"),
    PermissionCode.LOAN_REQUEST: ("loans", "Record and cancel loan and advance applications"),
    PermissionCode.LOAN_APPROVE: ("loans", "Approve or reject loan and advance applications"),
    PermissionCode.LOAN_DISBURSE: ("loans", "Disburse approved loans and post to accounting"),
    PermissionCode.PAYROLL_VIEW: ("payroll", "View payroll runs and results"),
    PermissionCode.PAYROLL_CALCULATE: ("payroll", "Prepare and calculate payroll"),
    PermissionCode.PAYROLL_APPROVE: ("payroll", "Approve calculated payroll"),
    PermissionCode.PAYROLL_FINALIZE: ("payroll", "Finalize approved payroll"),
    PermissionCode.PAYROLL_POST: ("payroll", "Post finalized payroll to accounting"),
    PermissionCode.ACCOUNTING_VIEW: ("accounting", "View chart of accounts and ledgers"),
    PermissionCode.ACCOUNTING_POST: ("accounting", "Create and post journals"),
    PermissionCode.AUDIT_VIEW: ("audit", "View audit and login history"),
    PermissionCode.REPORT_VIEW: ("reporting", "View reports and analytics"),
    PermissionCode.RULE_VIEW: ("statutory", "View statutory rule sets"),
    PermissionCode.RULE_MANAGE: ("statutory", "Manage statutory rule sets"),
}


ROLE_PERMISSIONS: dict[str, frozenset[PermissionCode]] = {
    "org_admin": frozenset(PermissionCode),
    "hr_manager": frozenset(
        {
            PermissionCode.ORGANIZATION_VIEW,
            PermissionCode.COMPANY_VIEW,
            PermissionCode.EMPLOYEE_VIEW,
            PermissionCode.EMPLOYEE_CREATE,
            PermissionCode.EMPLOYEE_UPDATE,
            PermissionCode.EMPLOYEE_TERMINATE,
            PermissionCode.COMPENSATION_VIEW,
            PermissionCode.COMPENSATION_MANAGE,
            PermissionCode.LEAVE_VIEW,
            PermissionCode.LEAVE_REQUEST,
            PermissionCode.LEAVE_APPROVE,
            PermissionCode.LEAVE_MANAGE,
            PermissionCode.ATTENDANCE_VIEW,
            PermissionCode.ATTENDANCE_MANAGE,
            PermissionCode.LOAN_VIEW,
            PermissionCode.LOAN_REQUEST,
            PermissionCode.LOAN_APPROVE,
            PermissionCode.PAYROLL_VIEW,
            PermissionCode.REPORT_VIEW,
            PermissionCode.RULE_VIEW,
        }
    ),
    "payroll_manager": frozenset(
        {
            PermissionCode.ORGANIZATION_VIEW,
            PermissionCode.COMPANY_VIEW,
            PermissionCode.EMPLOYEE_VIEW,
            PermissionCode.COMPENSATION_VIEW,
            PermissionCode.LEAVE_VIEW,
            PermissionCode.ATTENDANCE_VIEW,
            PermissionCode.LOAN_VIEW,
            PermissionCode.LOAN_REQUEST,
            PermissionCode.LOAN_APPROVE,
            PermissionCode.PAYROLL_VIEW,
            PermissionCode.PAYROLL_CALCULATE,
            PermissionCode.PAYROLL_APPROVE,
            PermissionCode.PAYROLL_FINALIZE,
            PermissionCode.PAYROLL_POST,
            PermissionCode.ACCOUNTING_VIEW,
            PermissionCode.REPORT_VIEW,
            PermissionCode.RULE_VIEW,
        }
    ),
    "payroll_officer": frozenset(
        {
            PermissionCode.ORGANIZATION_VIEW,
            PermissionCode.COMPANY_VIEW,
            PermissionCode.EMPLOYEE_VIEW,
            PermissionCode.COMPENSATION_VIEW,
            PermissionCode.LEAVE_VIEW,
            PermissionCode.ATTENDANCE_VIEW,
            PermissionCode.LOAN_VIEW,
            PermissionCode.LOAN_REQUEST,
            PermissionCode.PAYROLL_VIEW,
            PermissionCode.PAYROLL_CALCULATE,
            PermissionCode.REPORT_VIEW,
            PermissionCode.RULE_VIEW,
        }
    ),
    "finance_manager": frozenset(
        {
            PermissionCode.ORGANIZATION_VIEW,
            PermissionCode.COMPANY_VIEW,
            PermissionCode.EMPLOYEE_VIEW,
            PermissionCode.COMPENSATION_VIEW,
            PermissionCode.LEAVE_VIEW,
            PermissionCode.ATTENDANCE_VIEW,
            PermissionCode.LOAN_VIEW,
            PermissionCode.LOAN_APPROVE,
            PermissionCode.LOAN_DISBURSE,
            PermissionCode.PAYROLL_VIEW,
            PermissionCode.PAYROLL_APPROVE,
            PermissionCode.PAYROLL_POST,
            PermissionCode.ACCOUNTING_VIEW,
            PermissionCode.ACCOUNTING_POST,
            PermissionCode.REPORT_VIEW,
            PermissionCode.AUDIT_VIEW,
        }
    ),
    "auditor": frozenset(
        {
            PermissionCode.ORGANIZATION_VIEW,
            PermissionCode.COMPANY_VIEW,
            PermissionCode.USER_VIEW,
            PermissionCode.EMPLOYEE_VIEW,
            PermissionCode.COMPENSATION_VIEW,
            PermissionCode.LEAVE_VIEW,
            PermissionCode.ATTENDANCE_VIEW,
            PermissionCode.LOAN_VIEW,
            PermissionCode.PAYROLL_VIEW,
            PermissionCode.ACCOUNTING_VIEW,
            PermissionCode.AUDIT_VIEW,
            PermissionCode.REPORT_VIEW,
            PermissionCode.RULE_VIEW,
        }
    ),
}

ROLE_NAMES: dict[str, str] = {
    "org_admin": "Organisation Administrator",
    "hr_manager": "HR Manager",
    "payroll_manager": "Payroll Manager",
    "payroll_officer": "Payroll Officer",
    "finance_manager": "Finance Manager",
    "auditor": "Auditor",
}
