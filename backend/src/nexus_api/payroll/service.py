"""Payroll creation, deterministic calculation, approval and finalisation."""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_api.api.deps import PaginationParams
from nexus_api.audit.service import record_audit
from nexus_api.benefits.models import BenefitEnrollment, BenefitPlan
from nexus_api.core.errors import (
    ConfigurationError,
    ConflictError,
    IntegrityError,
    NotFoundError,
    ValidationFailureError,
)
from nexus_api.employees.models import Employee, EmployeeCompensation
from nexus_api.identity.permissions import PermissionCode
from nexus_api.identity.service import AuthContext, validate_company_access
from nexus_api.loans.models import EmployeeLoan, LoanInstallment
from nexus_api.organizations.models import Company
from nexus_api.payroll.models import (
    PayGroup,
    PayrollPeriod,
    PayrollResult,
    PayrollRun,
    PayslipLine,
    StatutoryRuleSet,
)
from nexus_api.payroll.schemas import PayGroupCreate, PayrollPeriodCreate, PayrollRunCreate
from nexus_api.payroll.statutory import (
    ENGINE_VERSION,
    KenyaStatutoryRules,
    StatutoryInput,
    calculate_kenya,
    load_kenya_rule_seed,
    money,
)


@dataclass(frozen=True, slots=True)
class CalculationLine:
    line_type: str
    code: str
    description: str
    amount: Decimal
    taxable: bool


@dataclass(frozen=True, slots=True)
class BenefitCharge:
    enrollment_id: uuid.UUID
    plan_id: uuid.UUID
    plan_code: str
    plan_name: str
    employee_amount: Decimal
    employer_amount: Decimal
    effective_from: date
    effective_to: date | None
    prorated_days: int


@dataclass(frozen=True, slots=True)
class EmployeeCalculation:
    employee: Employee
    days_in_period: int
    payable_days: int
    base_salary: Decimal
    housing_allowance: Decimal
    transport_allowance: Decimal
    other_allowance: Decimal
    gross: Decimal
    loan_deduction: Decimal
    loan_principal: Decimal
    loan_interest: Decimal
    benefit_employee_deduction: Decimal
    benefit_employer_contribution: Decimal
    taxable_income: Decimal
    paye: Decimal
    nssf_employee: Decimal
    shif_employee: Decimal
    housing_levy_employee: Decimal
    other_deductions: Decimal
    total_deductions: Decimal
    net_pay: Decimal
    nssf_employer: Decimal
    housing_levy_employer: Decimal
    nita: Decimal
    employer_cost: Decimal
    snapshot: dict[str, Any]
    lines: tuple[CalculationLine, ...]


async def ensure_kenya_rule_set(session: AsyncSession) -> StatutoryRuleSet:
    """Install the verified Kenya rule seed if it is not already present."""

    seed = load_kenya_rule_seed()
    existing = await session.scalar(
        select(StatutoryRuleSet).where(
            StatutoryRuleSet.country_code == "KE",
            StatutoryRuleSet.jurisdiction_code == "KE",
            StatutoryRuleSet.version == seed.version,
        )
    )
    if existing is not None:
        return existing
    rule_set = StatutoryRuleSet(
        country_code="KE",
        jurisdiction_code="KE",
        name=seed.name,
        version=seed.version,
        effective_from=seed.effective_from,
        status="active",
        rules=seed.rules,
        source_urls=seed.source_urls,
        checksum=seed.checksum,
        verified_at=datetime.now(UTC),
    )
    session.add(rule_set)
    await session.flush()
    return rule_set


async def list_rule_sets(session: AsyncSession, context: AuthContext) -> list[StatutoryRuleSet]:
    context.require(PermissionCode.RULE_VIEW)
    return list(
        (
            await session.scalars(
                select(StatutoryRuleSet).order_by(
                    StatutoryRuleSet.country_code,
                    StatutoryRuleSet.effective_from.desc(),
                )
            )
        ).all()
    )


async def create_pay_group(
    session: AsyncSession, context: AuthContext, payload: PayGroupCreate
) -> PayGroup:
    company = await validate_company_access(
        session, context, payload.company_id, PermissionCode.PAYROLL_CALCULATE
    )
    if payload.currency.upper() != company.base_currency:
        raise ValidationFailureError("Pay group currency must match the company base currency.")
    existing = await session.scalar(
        select(PayGroup.id).where(
            PayGroup.company_id == company.id, PayGroup.code == payload.code.upper()
        )
    )
    if existing is not None:
        raise ConflictError("A pay group with that code already exists.")
    group = PayGroup(
        organization_id=context.organization.id,
        company_id=company.id,
        code=payload.code.upper(),
        name=payload.name.strip(),
        frequency="monthly",
        currency=payload.currency.upper(),
        status="active",
    )
    session.add(group)
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="pay_group.created",
        entity_type="pay_group",
        entity_id=group.id,
        metadata={"company_id": str(company.id), "code": group.code},
    )
    return group


async def list_pay_groups(
    session: AsyncSession, context: AuthContext, company_id: uuid.UUID
) -> list[PayGroup]:
    await validate_company_access(session, context, company_id, PermissionCode.PAYROLL_VIEW)
    return list(
        (
            await session.scalars(
                select(PayGroup).where(PayGroup.company_id == company_id).order_by(PayGroup.name)
            )
        ).all()
    )


async def create_payroll_period(
    session: AsyncSession, context: AuthContext, payload: PayrollPeriodCreate
) -> PayrollPeriod:
    group = await session.get(PayGroup, payload.pay_group_id)
    if group is None:
        raise NotFoundError("Pay group not found.")
    await validate_company_access(
        session, context, group.company_id, PermissionCode.PAYROLL_CALCULATE
    )
    existing = await session.scalar(
        select(PayrollPeriod.id).where(
            PayrollPeriod.pay_group_id == group.id,
            PayrollPeriod.period_start == payload.period_start,
        )
    )
    if existing is not None:
        raise ConflictError("A payroll period with that start date already exists.")
    period = PayrollPeriod(
        pay_group_id=group.id,
        period_start=payload.period_start,
        period_end=payload.period_end,
        payment_date=payload.payment_date,
        status="open",
    )
    session.add(period)
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="payroll_period.created",
        entity_type="payroll_period",
        entity_id=period.id,
        metadata={
            "pay_group_id": str(group.id),
            "period_start": period.period_start.isoformat(),
            "period_end": period.period_end.isoformat(),
        },
    )
    return period


async def list_payroll_periods(
    session: AsyncSession, context: AuthContext, pay_group_id: uuid.UUID
) -> list[PayrollPeriod]:
    group = await session.get(PayGroup, pay_group_id)
    if group is None:
        raise NotFoundError("Pay group not found.")
    await validate_company_access(session, context, group.company_id, PermissionCode.PAYROLL_VIEW)
    return list(
        (
            await session.scalars(
                select(PayrollPeriod)
                .where(PayrollPeriod.pay_group_id == pay_group_id)
                .order_by(PayrollPeriod.period_start.desc())
            )
        ).all()
    )


async def _next_run_number(session: AsyncSession, company_id: uuid.UUID, period_end: date) -> str:
    await session.execute(select(Company.id).where(Company.id == company_id).with_for_update())
    prefix = f"PR-{period_end.strftime('%Y%m')}-"
    count = await session.scalar(
        select(func.count(PayrollRun.id)).where(
            PayrollRun.company_id == company_id,
            PayrollRun.run_number.like(f"{prefix}%"),
        )
    )
    return f"{prefix}{(count or 0) + 1:04d}"


async def create_run(
    session: AsyncSession, context: AuthContext, payload: PayrollRunCreate
) -> PayrollRun:
    company = await validate_company_access(
        session, context, payload.company_id, PermissionCode.PAYROLL_CALCULATE
    )
    group = await session.get(PayGroup, payload.pay_group_id)
    if group is None or group.company_id != company.id:
        raise NotFoundError("Pay group not found for this company.")
    period = await session.get(PayrollPeriod, payload.payroll_period_id)
    if period is None or period.pay_group_id != group.id:
        raise NotFoundError("Payroll period not found for this pay group.")
    if period.status != "open":
        raise ConflictError("The payroll period is closed.")

    existing = await session.scalar(
        select(PayrollRun.id).where(
            PayrollRun.pay_group_id == group.id,
            PayrollRun.period_start == period.period_start,
            PayrollRun.status != "reversed",
        )
    )
    if existing is not None:
        raise ConflictError("A payroll run already exists for this pay group and period.")

    rule_set = await session.scalar(
        select(StatutoryRuleSet)
        .where(
            StatutoryRuleSet.country_code == company.country_code,
            StatutoryRuleSet.jurisdiction_code == company.country_code,
            StatutoryRuleSet.status == "active",
            StatutoryRuleSet.effective_from <= period.period_end,
            or_(
                StatutoryRuleSet.effective_to.is_(None),
                StatutoryRuleSet.effective_to >= period.period_start,
            ),
        )
        .order_by(StatutoryRuleSet.effective_from.desc())
        .limit(1)
    )
    if rule_set is None:
        raise ConfigurationError(
            "No active statutory rule set covers this payroll period. "
            "Install and approve the applicable jurisdiction rules first."
        )
    rules_snapshot: dict[str, Any] = {
        "version": rule_set.version,
        "checksum": rule_set.checksum,
        "effective_from": rule_set.effective_from.isoformat(),
        "rules": rule_set.rules,
    }
    run = PayrollRun(
        organization_id=context.organization.id,
        company_id=company.id,
        pay_group_id=group.id,
        period_start=period.period_start,
        period_end=period.period_end,
        payment_date=period.payment_date,
        run_number=await _next_run_number(session, company.id, period.period_end),
        status="draft",
        currency=group.currency,
        rule_set_id=rule_set.id,
        rules_snapshot=rules_snapshot,
        engine_version=ENGINE_VERSION,
        prepared_by=context.user.id,
    )
    session.add(run)
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="payroll_run.created",
        entity_type="payroll_run",
        entity_id=run.id,
        metadata={
            "run_number": run.run_number,
            "company_id": str(company.id),
            "period_start": run.period_start.isoformat(),
            "period_end": run.period_end.isoformat(),
            "rule_set_version": rule_set.version,
        },
    )
    return run


async def get_run(session: AsyncSession, context: AuthContext, run_id: uuid.UUID) -> PayrollRun:
    run = await session.get(PayrollRun, run_id)
    if run is None or run.organization_id != context.organization.id:
        raise NotFoundError("Payroll run not found.")
    await validate_company_access(session, context, run.company_id, PermissionCode.PAYROLL_VIEW)
    return run


async def list_runs(
    session: AsyncSession,
    context: AuthContext,
    *,
    company_id: uuid.UUID,
    pagination: PaginationParams,
    status: str | None,
) -> tuple[list[PayrollRun], int]:
    await validate_company_access(session, context, company_id, PermissionCode.PAYROLL_VIEW)
    filters = [PayrollRun.company_id == company_id]
    if status:
        filters.append(PayrollRun.status == status)
    total = await session.scalar(select(func.count(PayrollRun.id)).where(*filters))
    runs = (
        await session.scalars(
            select(PayrollRun)
            .where(*filters)
            .order_by(PayrollRun.period_end.desc(), PayrollRun.run_number.desc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
    ).all()
    return list(runs), total or 0


async def list_results(
    session: AsyncSession, context: AuthContext, run_id: uuid.UUID
) -> list[PayrollResult]:
    run = await get_run(session, context, run_id)
    return list(
        (
            await session.scalars(
                select(PayrollResult)
                .where(PayrollResult.payroll_run_id == run.id)
                .order_by(PayrollResult.employee_id)
            )
        ).all()
    )


async def calculate_run(
    session: AsyncSession, context: AuthContext, run_id: uuid.UUID
) -> PayrollRun:
    run = await get_run(session, context, run_id)
    context.require(PermissionCode.PAYROLL_CALCULATE, company_id=run.company_id)
    run = await session.scalar(select(PayrollRun).where(PayrollRun.id == run.id).with_for_update())
    if run is None:
        raise NotFoundError("Payroll run not found.")
    if run.status not in ("draft", "calculated"):
        raise ConflictError(
            "Only a draft or calculated payroll run can be recalculated. "
            "Approved and finalised runs are immutable."
        )
    if run.prepared_by != context.user.id:
        context.require(PermissionCode.PAYROLL_CALCULATE, company_id=run.company_id)

    from nexus_api.loans.service import release_payroll_installments

    await release_payroll_installments(session, run.id, run.company_id)

    rules_payload = run.rules_snapshot.get("rules")
    if not isinstance(rules_payload, dict):
        raise IntegrityError("The payroll rule snapshot is malformed.")
    rules = KenyaStatutoryRules.model_validate(rules_payload)

    employees = (
        await session.scalars(
            select(Employee)
            .where(
                Employee.company_id == run.company_id,
                Employee.is_payroll_eligible.is_(True),
                Employee.hire_date <= run.period_end,
                or_(
                    Employee.status.in_(["active", "probation"]),
                    (Employee.status == "terminated")
                    & (Employee.termination_date >= run.period_start),
                ),
                or_(
                    Employee.termination_date.is_(None),
                    Employee.termination_date >= run.period_start,
                ),
            )
            .order_by(Employee.employee_number)
        )
    ).all()
    if not employees:
        raise ValidationFailureError("No payroll-eligible employees fall within this period.")

    employee_ids = [employee.id for employee in employees]
    compensations = (
        await session.scalars(
            select(EmployeeCompensation)
            .where(
                EmployeeCompensation.employee_id.in_(employee_ids),
                EmployeeCompensation.status.in_(["active", "superseded"]),
                EmployeeCompensation.effective_from <= run.period_end,
                or_(
                    EmployeeCompensation.effective_to.is_(None),
                    EmployeeCompensation.effective_to >= run.period_start,
                ),
            )
            .order_by(EmployeeCompensation.employee_id, EmployeeCompensation.effective_from)
        )
    ).all()
    by_employee: dict[uuid.UUID, list[EmployeeCompensation]] = defaultdict(list)
    for compensation in compensations:
        by_employee[compensation.employee_id].append(compensation)

    loan_rows = await session.execute(
        select(LoanInstallment, EmployeeLoan)
        .join(EmployeeLoan, EmployeeLoan.id == LoanInstallment.loan_id)
        .where(
            LoanInstallment.company_id == run.company_id,
            LoanInstallment.status == "scheduled",
            LoanInstallment.due_date <= run.payment_date,
            EmployeeLoan.employee_id.in_(employee_ids),
            EmployeeLoan.status == "disbursed",
        )
        .order_by(
            EmployeeLoan.disbursed_at,
            EmployeeLoan.loan_number,
            LoanInstallment.sequence,
        )
        .with_for_update(of=LoanInstallment)
    )
    loans_by_employee: dict[uuid.UUID, list[tuple[LoanInstallment, EmployeeLoan]]] = defaultdict(
        list
    )
    for installment, loan in loan_rows.all():
        loans_by_employee[loan.employee_id].append((installment, loan))

    benefit_rows = await session.execute(
        select(BenefitEnrollment, BenefitPlan)
        .join(BenefitPlan, BenefitPlan.id == BenefitEnrollment.plan_id)
        .where(
            BenefitEnrollment.company_id == run.company_id,
            BenefitEnrollment.status.in_(("active", "ended")),
            BenefitEnrollment.effective_from <= run.period_end,
            or_(
                BenefitEnrollment.effective_to.is_(None),
                BenefitEnrollment.effective_to >= run.period_start,
            ),
            BenefitEnrollment.employee_id.in_(employee_ids),
        )
        .order_by(BenefitEnrollment.employee_id, BenefitPlan.code)
        .with_for_update(of=BenefitEnrollment)
    )
    benefits_by_employee: dict[uuid.UUID, list[tuple[BenefitEnrollment, BenefitPlan]]] = (
        defaultdict(list)
    )
    for enrollment, plan in benefit_rows.all():
        benefits_by_employee[enrollment.employee_id].append((enrollment, plan))

    missing = [employee for employee in employees if not by_employee.get(employee.id)]
    if missing:
        raise ValidationFailureError(
            "Payroll cannot be calculated because some employees have no compensation "
            "covering the period.",
            details={
                "employees": [
                    {
                        "id": str(employee.id),
                        "employee_number": employee.employee_number,
                        "name": f"{employee.first_name} {employee.last_name}",
                    }
                    for employee in missing
                ]
            },
        )

    calculations: list[EmployeeCalculation] = []
    for employee in employees:
        calculations.append(
            _calculate_employee(
                employee,
                by_employee[employee.id],
                run.period_start,
                run.period_end,
                rules,
                run.currency,
                loans_by_employee.get(employee.id, []),
                benefits_by_employee.get(employee.id, []),
            )
        )

    result_ids = select(PayrollResult.id).where(PayrollResult.payroll_run_id == run.id)
    await session.execute(delete(PayslipLine).where(PayslipLine.payroll_result_id.in_(result_ids)))
    await session.execute(delete(PayrollResult).where(PayrollResult.payroll_run_id == run.id))

    gross_total = Decimal("0.00")
    taxable_total = Decimal("0.00")
    paye_total = Decimal("0.00")
    employee_statutory_total = Decimal("0.00")
    employer_statutory_total = Decimal("0.00")
    deductions_total = Decimal("0.00")
    loan_deduction_total = Decimal("0.00")
    benefit_employee_total = Decimal("0.00")
    benefit_employer_total = Decimal("0.00")
    net_total = Decimal("0.00")
    employer_cost_total = Decimal("0.00")

    for calculation in calculations:
        result = PayrollResult(
            organization_id=run.organization_id,
            company_id=run.company_id,
            payroll_run_id=run.id,
            employee_id=calculation.employee.id,
            currency=run.currency,
            days_in_period=calculation.days_in_period,
            payable_days=calculation.payable_days,
            base_salary=calculation.base_salary,
            housing_allowance=calculation.housing_allowance,
            transport_allowance=calculation.transport_allowance,
            other_allowance=calculation.other_allowance,
            gross=calculation.gross,
            taxable_income=calculation.taxable_income,
            paye=calculation.paye,
            nssf_employee=calculation.nssf_employee,
            shif_employee=calculation.shif_employee,
            housing_levy_employee=calculation.housing_levy_employee,
            other_deductions=calculation.other_deductions,
            loan_deduction=calculation.loan_deduction,
            loan_principal=calculation.loan_principal,
            loan_interest=calculation.loan_interest,
            benefit_employee_deduction=calculation.benefit_employee_deduction,
            benefit_employer_contribution=calculation.benefit_employer_contribution,
            total_deductions=calculation.total_deductions,
            net_pay=calculation.net_pay,
            nssf_employer=calculation.nssf_employer,
            housing_levy_employer=calculation.housing_levy_employer,
            nita=calculation.nita,
            employer_cost=calculation.employer_cost,
            calculation_snapshot=calculation.snapshot,
        )
        session.add(result)
        await session.flush()
        for installment, _loan in loans_by_employee.get(calculation.employee.id, []):
            if installment.status != "scheduled":
                raise IntegrityError(
                    "A loan instalment was claimed by another payroll run while calculating."
                )
            installment.status = "reserved"
            installment.payroll_run_id = run.id
            installment.payroll_result_id = result.id
        for sequence, line in enumerate(calculation.lines, start=1):
            session.add(
                PayslipLine(
                    payroll_result_id=result.id,
                    sequence=sequence,
                    line_type=line.line_type,
                    code=line.code,
                    description=line.description,
                    amount=line.amount,
                    taxable=line.taxable,
                )
            )
        gross_total += calculation.gross
        taxable_total += calculation.taxable_income
        paye_total += calculation.paye
        employee_statutory_total += calculation.total_deductions
        employer_statutory_total += (
            calculation.nssf_employer + calculation.housing_levy_employer + calculation.nita
        )
        deductions_total += calculation.total_deductions
        loan_deduction_total += calculation.loan_deduction
        benefit_employee_total += calculation.benefit_employee_deduction
        benefit_employer_total += calculation.benefit_employer_contribution
        net_total += calculation.net_pay
        employer_cost_total += calculation.employer_cost

    run.employee_count = len(calculations)
    run.gross_total = money(gross_total)
    run.taxable_total = money(taxable_total)
    run.paye_total = money(paye_total)
    run.employee_statutory_total = money(employee_statutory_total)
    run.employer_statutory_total = money(employer_statutory_total)
    run.deductions_total = money(deductions_total)
    run.loan_deduction_total = money(loan_deduction_total)
    run.benefit_employee_total = money(benefit_employee_total)
    run.benefit_employer_total = money(benefit_employer_total)
    run.net_total = money(net_total)
    run.employer_cost_total = money(employer_cost_total)
    run.status = "calculated"
    run.calculated_at = datetime.now(UTC)
    if run.net_total < 0:
        raise IntegrityError("Payroll produced a negative net total.")
    if run.gross_total != money(run.net_total + run.deductions_total):
        raise IntegrityError("Payroll totals do not reconcile (gross vs net + deductions).")
    await session.flush()
    await record_audit(
        session,
        organization_id=run.organization_id,
        actor_user_id=context.user.id,
        action="payroll_run.calculated",
        entity_type="payroll_run",
        entity_id=run.id,
        metadata={
            "employee_count": run.employee_count,
            "gross_total": str(run.gross_total),
            "net_total": str(run.net_total),
            "rule_set_id": str(run.rule_set_id),
            "engine_version": run.engine_version,
        },
    )
    return run


def _benefit_charges_for(
    benefits: list[tuple[BenefitEnrollment, BenefitPlan]],
    period_start: date,
    period_end: date,
    termination_date: date | None,
) -> tuple[BenefitCharge, ...]:
    """Prorate monthly contributions by calendar-day overlap with the period.

    Ended enrolments remain chargeable for periods they overlap, and employment
    termination caps the chargeable days even if the enrolment has no end date.
    """

    total_days = (period_end - period_start).days + 1
    charges: list[BenefitCharge] = []
    for enrollment, plan in benefits:
        overlap_start = max(period_start, enrollment.effective_from)
        overlap_end = min(
            period_end,
            enrollment.effective_to or period_end,
            termination_date or period_end,
        )
        if overlap_end < overlap_start:
            continue
        days = (overlap_end - overlap_start).days + 1
        share = Decimal(days) / Decimal(total_days)
        employee_amount = money(enrollment.employee_contribution * share)
        employer_amount = money(enrollment.employer_contribution * share)
        if employee_amount == 0 and employer_amount == 0:
            continue
        charges.append(
            BenefitCharge(
                enrollment_id=enrollment.id,
                plan_id=plan.id,
                plan_code=plan.code,
                plan_name=plan.name,
                employee_amount=employee_amount,
                employer_amount=employer_amount,
                effective_from=enrollment.effective_from,
                effective_to=enrollment.effective_to,
                prorated_days=days,
            )
        )
    return tuple(charges)


def _calculate_employee(
    employee: Employee,
    compensations: list[EmployeeCompensation],
    period_start: date,
    period_end: date,
    rules: KenyaStatutoryRules,
    currency: str,
    loan_due: list[tuple[LoanInstallment, EmployeeLoan]],
    benefit_due: list[tuple[BenefitEnrollment, BenefitPlan]],
) -> EmployeeCalculation:
    total_days = (period_end - period_start).days + 1
    base = Decimal("0.00")
    housing = Decimal("0.00")
    transport = Decimal("0.00")
    other = Decimal("0.00")
    payable_days = 0
    segments: list[dict[str, Any]] = []

    for compensation in compensations:
        if compensation.currency != currency:
            raise ValidationFailureError(
                f"Employee {employee.employee_number} has compensation in "
                f"{compensation.currency}, not {currency}."
            )
        segment_start = max(period_start, compensation.effective_from, employee.hire_date)
        segment_end = min(
            period_end,
            compensation.effective_to or period_end,
            employee.termination_date or period_end,
        )
        if segment_end < segment_start:
            continue
        days = (segment_end - segment_start).days + 1
        share = Decimal(days) / Decimal(total_days)
        prorated_base = money(compensation.base_salary * share)
        prorated_housing = money(compensation.housing_allowance * share)
        prorated_transport = money(compensation.transport_allowance * share)
        prorated_other = money(compensation.other_allowance * share)
        base += prorated_base
        housing += prorated_housing
        transport += prorated_transport
        other += prorated_other
        payable_days += days
        segments.append(
            {
                "from": segment_start.isoformat(),
                "to": segment_end.isoformat(),
                "days": days,
                "compensation_id": str(compensation.id),
                "base_salary": str(prorated_base),
                "housing_allowance": str(prorated_housing),
                "transport_allowance": str(prorated_transport),
                "other_allowance": str(prorated_other),
            }
        )

    if payable_days == 0:
        raise ValidationFailureError(
            f"Employee {employee.employee_number} has no payable days in the period."
        )

    base = money(base)
    housing = money(housing)
    transport = money(transport)
    other = money(other)
    gross = money(base + housing + transport + other)
    statutory = calculate_kenya(rules, StatutoryInput(gross=gross))
    loan_principal = money(
        sum((installment.principal_due for installment, _loan in loan_due), Decimal("0.00"))
    )
    loan_interest = money(
        sum((installment.interest_due for installment, _loan in loan_due), Decimal("0.00"))
    )
    loan_deduction = money(loan_principal + loan_interest)
    for enrollment, _plan in benefit_due:
        if enrollment.currency != currency:
            raise ValidationFailureError(
                f"Employee {employee.employee_number} has a benefit enrolment in "
                f"{enrollment.currency}, not {currency}."
            )
    benefit_charges = _benefit_charges_for(
        benefit_due, period_start, period_end, employee.termination_date
    )
    benefit_employee = money(
        sum((charge.employee_amount for charge in benefit_charges), Decimal("0.00"))
    )
    benefit_employer = money(
        sum((charge.employer_amount for charge in benefit_charges), Decimal("0.00"))
    )
    available_after_statutory = money(gross - statutory.total_employee_statutory)
    required_other_deductions = money(loan_deduction + benefit_employee)
    if required_other_deductions > available_after_statutory:
        raise ValidationFailureError(
            f"Employee {employee.employee_number} has insufficient net pay for the "
            "scheduled loan and benefit deductions.",
            details={
                "employee_id": str(employee.id),
                "employee_number": employee.employee_number,
                "net_available": str(available_after_statutory),
                "loan_deduction_required": str(loan_deduction),
                "benefit_deduction_required": str(benefit_employee),
                "installments": [
                    {
                        "loan_number": loan.loan_number,
                        "sequence": installment.sequence,
                        "due_date": installment.due_date.isoformat(),
                        "amount_due": str(installment.amount_due),
                    }
                    for installment, loan in loan_due
                ],
                "benefits": [
                    {
                        "plan_code": charge.plan_code,
                        "employee_amount": str(charge.employee_amount),
                        "employer_amount": str(charge.employer_amount),
                    }
                    for charge in benefit_charges
                ],
            },
        )
    total_deductions = money(statutory.total_employee_statutory + loan_deduction + benefit_employee)
    net_pay = money(gross - total_deductions)
    if net_pay < 0:
        raise ValidationFailureError(
            f"Employee {employee.employee_number} would have negative net pay."
        )
    employer_cost = money(gross + statutory.total_employer_statutory + benefit_employer)

    lines = _lines_for(
        base,
        housing,
        transport,
        other,
        statutory.paye,
        statutory.nssf_employee,
        statutory.shif,
        statutory.housing_employee,
        statutory.nssf_employer,
        statutory.housing_employer,
        statutory.nita,
        loan_due,
        benefit_charges,
    )
    snapshot: dict[str, Any] = {
        "engine_version": ENGINE_VERSION,
        "employee_number": employee.employee_number,
        "days_in_period": total_days,
        "payable_days": payable_days,
        "segments": segments,
        "gross": str(gross),
        "loans": [
            {
                "loan_id": str(loan.id),
                "loan_number": loan.loan_number,
                "installment_id": str(installment.id),
                "sequence": installment.sequence,
                "principal": str(installment.principal_due),
                "interest": str(installment.interest_due),
                "amount": str(installment.amount_due),
            }
            for installment, loan in loan_due
        ],
        "loan_deduction": str(loan_deduction),
        "benefits": [
            {
                "enrollment_id": str(charge.enrollment_id),
                "plan_id": str(charge.plan_id),
                "plan_code": charge.plan_code,
                "plan_name": charge.plan_name,
                "effective_from": charge.effective_from.isoformat(),
                "effective_to": (charge.effective_to.isoformat() if charge.effective_to else None),
                "prorated_days": charge.prorated_days,
                "employee_amount": str(charge.employee_amount),
                "employer_amount": str(charge.employer_amount),
            }
            for charge in benefit_charges
        ],
        "benefit_employee_deduction": str(benefit_employee),
        "benefit_employer_contribution": str(benefit_employer),
        "statutory": {
            "taxable_income": str(statutory.taxable_income),
            "paye": str(statutory.paye),
            "nssf_employee": str(statutory.nssf_employee),
            "nssf_employer": str(statutory.nssf_employer),
            "shif": str(statutory.shif),
            "housing_employee": str(statutory.housing_employee),
            "housing_employer": str(statutory.housing_employer),
            "nita": str(statutory.nita),
        },
        "net_pay": str(net_pay),
    }
    return EmployeeCalculation(
        employee=employee,
        days_in_period=total_days,
        payable_days=payable_days,
        base_salary=base,
        housing_allowance=housing,
        transport_allowance=transport,
        other_allowance=other,
        gross=gross,
        loan_deduction=loan_deduction,
        loan_principal=loan_principal,
        loan_interest=loan_interest,
        benefit_employee_deduction=benefit_employee,
        benefit_employer_contribution=benefit_employer,
        taxable_income=statutory.taxable_income,
        paye=statutory.paye,
        nssf_employee=statutory.nssf_employee,
        shif_employee=statutory.shif,
        housing_levy_employee=statutory.housing_employee,
        other_deductions=Decimal("0.00"),
        total_deductions=total_deductions,
        net_pay=net_pay,
        nssf_employer=statutory.nssf_employer,
        housing_levy_employer=statutory.housing_employer,
        nita=statutory.nita,
        employer_cost=employer_cost,
        snapshot=snapshot,
        lines=lines,
    )


def _lines_for(
    base: Decimal,
    housing: Decimal,
    transport: Decimal,
    other: Decimal,
    paye: Decimal,
    nssf_employee: Decimal,
    shif: Decimal,
    housing_employee: Decimal,
    nssf_employer: Decimal,
    housing_employer: Decimal,
    nita: Decimal,
    loan_due: list[tuple[LoanInstallment, EmployeeLoan]],
    benefit_charges: tuple[BenefitCharge, ...],
) -> tuple[CalculationLine, ...]:
    candidates = (
        CalculationLine("earning", "BASE", "Basic salary", base, True),
        CalculationLine("earning", "HOUSING", "Housing allowance", housing, True),
        CalculationLine("earning", "TRANSPORT", "Transport allowance", transport, True),
        CalculationLine("earning", "OTHER", "Other allowance", other, True),
        CalculationLine("statutory", "PAYE", "PAYE", paye, False),
        CalculationLine("statutory", "NSSF_EE", "NSSF employee contribution", nssf_employee, False),
        CalculationLine("statutory", "SHIF", "SHIF contribution", shif, False),
        CalculationLine(
            "statutory", "HOUSING_EE", "Affordable Housing Levy (employee)", housing_employee, False
        ),
        CalculationLine(
            "employer_contribution", "NSSF_ER", "NSSF employer contribution", nssf_employer, False
        ),
        CalculationLine(
            "employer_contribution",
            "HOUSING_ER",
            "Affordable Housing Levy (employer)",
            housing_employer,
            False,
        ),
        CalculationLine("employer_contribution", "NITA", "NITA levy", nita, False),
    )
    loan_lines = tuple(
        CalculationLine(
            "deduction",
            "LOAN",
            f"{loan.loan_number} instalment {installment.sequence} of {loan.term_months}",
            installment.amount_due,
            False,
        )
        for installment, loan in loan_due
    )
    benefit_lines = tuple(
        line
        for charge in benefit_charges
        for line in (
            CalculationLine(
                "deduction",
                f"BEN-EE-{charge.plan_code}",
                f"{charge.plan_name} employee contribution",
                charge.employee_amount,
                False,
            ),
            CalculationLine(
                "employer_contribution",
                f"BEN-ER-{charge.plan_code}",
                f"{charge.plan_name} employer contribution",
                charge.employer_amount,
                False,
            ),
        )
        if line.amount > 0
    )
    return tuple(line for line in candidates if line.amount > 0) + loan_lines + benefit_lines


async def approve_run(session: AsyncSession, context: AuthContext, run_id: uuid.UUID) -> PayrollRun:
    run = await get_run(session, context, run_id)
    context.require(PermissionCode.PAYROLL_APPROVE, company_id=run.company_id)
    run = await session.scalar(select(PayrollRun).where(PayrollRun.id == run.id).with_for_update())
    if run is None:
        raise NotFoundError("Payroll run not found.")
    if run.status != "calculated":
        raise ConflictError("Only a calculated payroll run can be approved.")
    if run.prepared_by == context.user.id:
        raise ConflictError("Separation of duties: the preparer cannot approve the payroll run.")
    run.approved_by = context.user.id
    run.approved_at = datetime.now(UTC)
    run.status = "approved"
    await session.flush()
    await record_audit(
        session,
        organization_id=run.organization_id,
        actor_user_id=context.user.id,
        action="payroll_run.approved",
        entity_type="payroll_run",
        entity_id=run.id,
        metadata={"run_number": run.run_number, "net_total": str(run.net_total)},
    )
    return run


async def finalize_run(
    session: AsyncSession, context: AuthContext, run_id: uuid.UUID
) -> PayrollRun:
    run = await get_run(session, context, run_id)
    context.require(PermissionCode.PAYROLL_FINALIZE, company_id=run.company_id)
    run = await session.scalar(select(PayrollRun).where(PayrollRun.id == run.id).with_for_update())
    if run is None:
        raise NotFoundError("Payroll run not found.")
    if run.status != "approved":
        raise ConflictError("Only an approved payroll run can be finalised.")
    if run.prepared_by == context.user.id:
        raise ConflictError("Separation of duties: the preparer cannot finalise the payroll run.")
    from nexus_api.loans.service import finalize_payroll_installments

    run.status = "finalized"
    run.finalized_at = datetime.now(UTC)
    await finalize_payroll_installments(session, context, run)
    await session.flush()
    await record_audit(
        session,
        organization_id=run.organization_id,
        actor_user_id=context.user.id,
        action="payroll_run.finalized",
        entity_type="payroll_run",
        entity_id=run.id,
        metadata={"run_number": run.run_number, "net_total": str(run.net_total)},
    )
    return run


async def post_run(session: AsyncSession, context: AuthContext, run_id: uuid.UUID) -> PayrollRun:
    from nexus_api.accounting.service import post_payroll_run

    run = await get_run(session, context, run_id)
    context.require(PermissionCode.PAYROLL_POST, company_id=run.company_id)
    run = await session.scalar(select(PayrollRun).where(PayrollRun.id == run.id).with_for_update())
    if run is None:
        raise NotFoundError("Payroll run not found.")
    if run.status == "posted":
        return run
    if run.status != "finalized":
        raise ConflictError("Only a finalised payroll run can be posted to accounting.")
    await post_payroll_run(session, context, run)
    await session.flush()
    return run
