"""Import every mapped class so Alembic and metadata creation see the full schema."""

from nexus_api.accounting.models import Account, FiscalPeriod, Journal, JournalLine
from nexus_api.audit.models import AuditEvent
from nexus_api.database.base import Base
from nexus_api.employees.models import Employee, EmployeeBankAccount, EmployeeCompensation
from nexus_api.identity.models import (
    LoginEvent,
    OrganizationMembership,
    Permission,
    RefreshToken,
    Role,
    RoleAssignment,
    RolePermission,
    User,
)
from nexus_api.leave.models import (
    AttendanceRecord,
    CompanyHoliday,
    LeaveBalance,
    LeaveRequest,
    LeaveType,
)
from nexus_api.loans.models import EmployeeLoan, LoanInstallment
from nexus_api.organizations.models import (
    Branch,
    Company,
    CostCentre,
    Department,
    FiscalCalendarMarker,
    Organization,
)
from nexus_api.payroll.models import (
    PayGroup,
    PayrollPeriod,
    PayrollResult,
    PayrollRun,
    PayslipLine,
    StatutoryRuleSet,
)

__all__ = [
    "Account",
    "AttendanceRecord",
    "AuditEvent",
    "Base",
    "Branch",
    "Company",
    "CompanyHoliday",
    "CostCentre",
    "Department",
    "Employee",
    "EmployeeBankAccount",
    "EmployeeCompensation",
    "EmployeeLoan",
    "FiscalCalendarMarker",
    "FiscalPeriod",
    "Journal",
    "JournalLine",
    "LeaveBalance",
    "LeaveRequest",
    "LeaveType",
    "LoanInstallment",
    "LoginEvent",
    "Organization",
    "OrganizationMembership",
    "PayGroup",
    "PayrollPeriod",
    "PayrollResult",
    "PayrollRun",
    "PayslipLine",
    "Permission",
    "RefreshToken",
    "Role",
    "RoleAssignment",
    "RolePermission",
    "StatutoryRuleSet",
    "User",
]
