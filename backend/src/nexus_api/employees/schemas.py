"""Employee and compensation API schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class CompensationCreate(BaseModel):
    base_salary: Decimal = Field(ge=0, max_digits=20, decimal_places=2)
    housing_allowance: Decimal = Field(
        default=Decimal("0.00"), ge=0, max_digits=20, decimal_places=2
    )
    transport_allowance: Decimal = Field(
        default=Decimal("0.00"), ge=0, max_digits=20, decimal_places=2
    )
    other_allowance: Decimal = Field(default=Decimal("0.00"), ge=0, max_digits=20, decimal_places=2)
    currency: str = Field(default="KES", min_length=3, max_length=3)
    effective_from: date

    @model_validator(mode="after")
    def require_positive_package(self) -> CompensationCreate:
        if (
            self.base_salary
            + self.housing_allowance
            + self.transport_allowance
            + self.other_allowance
            <= 0
        ):
            raise ValueError("The compensation package must contain a positive amount")
        return self


class CompensationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    base_salary: Decimal
    housing_allowance: Decimal
    transport_allowance: Decimal
    other_allowance: Decimal
    currency: str
    effective_from: date
    effective_to: date | None
    status: str
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    created_at: datetime


class EmployeeCreate(BaseModel):
    company_id: uuid.UUID
    employee_number: str | None = Field(default=None, min_length=1, max_length=32)
    first_name: str = Field(min_length=1, max_length=100)
    middle_name: str | None = Field(default=None, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    work_email: EmailStr | None = None
    personal_email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    national_id: str | None = Field(default=None, max_length=40)
    kra_pin: str | None = Field(default=None, max_length=20)
    nssf_number: str | None = Field(default=None, max_length=30)
    shif_number: str | None = Field(default=None, max_length=30)
    date_of_birth: date | None = None
    gender: Literal["female", "male", "other", "undisclosed"] | None = None
    job_title: str = Field(min_length=1, max_length=150)
    hire_date: date
    branch_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    cost_centre_id: uuid.UUID | None = None
    manager_id: uuid.UUID | None = None
    status: Literal["active", "probation"] = "active"
    is_payroll_eligible: bool = True
    initial_compensation: CompensationCreate | None = None


class EmployeeUpdate(BaseModel):
    version: int | None = None
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    middle_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    work_email: EmailStr | None = None
    personal_email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    national_id: str | None = Field(default=None, max_length=40)
    kra_pin: str | None = Field(default=None, max_length=20)
    nssf_number: str | None = Field(default=None, max_length=30)
    shif_number: str | None = Field(default=None, max_length=30)
    date_of_birth: date | None = None
    gender: Literal["female", "male", "other", "undisclosed"] | None = None
    job_title: str | None = Field(default=None, min_length=1, max_length=150)
    branch_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    cost_centre_id: uuid.UUID | None = None
    manager_id: uuid.UUID | None = None
    status: Literal["active", "probation", "suspended"] | None = None
    is_payroll_eligible: bool | None = None


class TerminationRequest(BaseModel):
    termination_date: date
    reason: str = Field(min_length=3, max_length=500)


class EmployeeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    branch_id: uuid.UUID | None
    department_id: uuid.UUID | None
    cost_centre_id: uuid.UUID | None
    manager_id: uuid.UUID | None
    employee_number: str
    first_name: str
    middle_name: str | None
    last_name: str
    work_email: str | None
    personal_email: str | None
    phone: str | None
    national_id: str | None
    kra_pin: str | None
    nssf_number: str | None
    shif_number: str | None
    date_of_birth: date | None
    gender: str | None
    job_title: str
    hire_date: date
    confirmation_date: date | None
    termination_date: date | None
    termination_reason: str | None
    status: str
    is_payroll_eligible: bool
    version: int
    created_at: datetime
    updated_at: datetime


class EmployeePage(BaseModel):
    items: list[EmployeeResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
