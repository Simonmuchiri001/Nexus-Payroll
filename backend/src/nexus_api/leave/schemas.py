"""Leave and attendance API schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

LeaveStatus = Literal["pending", "approved", "rejected", "cancelled"]
AttendanceStatus = Literal["present", "absent", "remote", "holiday"]


class LeaveTypeCreate(BaseModel):
    company_id: uuid.UUID
    code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=2, max_length=150)
    description: str | None = Field(default=None, max_length=500)
    is_paid: bool = True
    requires_balance: bool = True
    annual_entitlement_days: Decimal = Field(
        default=Decimal("0.00"), ge=0, le=366, max_digits=6, decimal_places=2
    )
    max_carryover_days: Decimal = Field(
        default=Decimal("0.00"), ge=0, le=366, max_digits=6, decimal_places=2
    )

    @model_validator(mode="after")
    def carryover_within_entitlement(self) -> LeaveTypeCreate:
        if self.max_carryover_days > self.annual_entitlement_days:
            raise ValueError("Maximum carryover cannot exceed the annual entitlement")
        return self


class LeaveTypeUpdate(BaseModel):
    version: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, min_length=2, max_length=150)
    description: str | None = Field(default=None, max_length=500)
    is_paid: bool | None = None
    requires_balance: bool | None = None
    annual_entitlement_days: Decimal | None = Field(
        default=None, ge=0, le=366, max_digits=6, decimal_places=2
    )
    max_carryover_days: Decimal | None = Field(
        default=None, ge=0, le=366, max_digits=6, decimal_places=2
    )
    status: Literal["active", "archived"] | None = None


class LeaveTypeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    code: str
    name: str
    description: str | None
    is_paid: bool
    requires_balance: bool
    annual_entitlement_days: Decimal
    max_carryover_days: Decimal
    status: str
    version: int


class HolidayCreate(BaseModel):
    company_id: uuid.UUID
    holiday_date: date
    name: str = Field(min_length=2, max_length=150)


class HolidayResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    holiday_date: date
    name: str


class LeaveBalanceInitialize(BaseModel):
    employee_id: uuid.UUID
    leave_year: int = Field(ge=2000, le=2100)


class LeaveBalanceAdjust(BaseModel):
    version: int | None = Field(default=None, ge=1)
    entitled_days: Decimal | None = Field(
        default=None, ge=0, le=366, max_digits=6, decimal_places=2
    )
    carried_over_days: Decimal | None = Field(
        default=None, ge=0, le=366, max_digits=6, decimal_places=2
    )
    reason: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def require_change(self) -> LeaveBalanceAdjust:
        if self.entitled_days is None and self.carried_over_days is None:
            raise ValueError("Supply an entitlement or carryover change")
        return self


class LeaveBalanceResponse(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    employee_id: uuid.UUID
    leave_type_id: uuid.UUID
    leave_type_code: str
    leave_type_name: str
    leave_year: int
    entitled_days: Decimal
    carried_over_days: Decimal
    used_days: Decimal
    pending_days: Decimal
    available_days: Decimal
    version: int


class LeaveRequestCreate(BaseModel):
    employee_id: uuid.UUID
    leave_type_id: uuid.UUID
    start_date: date
    end_date: date
    reason: str = Field(min_length=3, max_length=1000)

    @model_validator(mode="after")
    def validate_dates(self) -> LeaveRequestCreate:
        if self.end_date < self.start_date:
            raise ValueError("The end date must not precede the start date")
        if self.start_date.year != self.end_date.year:
            raise ValueError("A leave request must start and end in the same calendar year")
        if (self.end_date - self.start_date).days > 366:
            raise ValueError("A leave request cannot span more than 366 days")
        return self


class LeaveDecision(BaseModel):
    version: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None, max_length=1000)


class LeaveRequestResponse(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    employee_id: uuid.UUID
    employee_number: str
    employee_name: str
    leave_type_id: uuid.UUID
    leave_type_code: str
    leave_type_name: str
    leave_year: int
    start_date: date
    end_date: date
    requested_days: Decimal
    balance_tracked: bool
    reason: str
    status: str
    requested_by: uuid.UUID | None
    requested_by_name: str | None
    decided_by: uuid.UUID | None
    decided_by_name: str | None
    decided_at: datetime | None
    decision_note: str | None
    cancelled_by: uuid.UUID | None
    cancelled_at: datetime | None
    created_at: datetime
    version: int


class LeaveRequestPage(BaseModel):
    items: list[LeaveRequestResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)


class AttendanceCreate(BaseModel):
    employee_id: uuid.UUID
    work_date: date
    status: AttendanceStatus
    note: str | None = Field(default=None, max_length=500)


class AttendanceUpdate(BaseModel):
    version: int | None = Field(default=None, ge=1)
    status: AttendanceStatus | None = None
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_change(self) -> AttendanceUpdate:
        if self.status is None and self.note is None:
            raise ValueError("Supply a status or note change")
        return self


class AttendanceResponse(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    employee_id: uuid.UUID
    employee_number: str
    employee_name: str
    work_date: date
    status: str
    source: str
    leave_request_id: uuid.UUID | None
    note: str | None
    created_at: datetime
    version: int


class AttendancePage(BaseModel):
    items: list[AttendanceResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
