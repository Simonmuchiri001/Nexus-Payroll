"""Organisation and company API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OrganizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    legal_name: str | None
    country_code: str
    status: str
    created_at: datetime


class CompanyCreate(BaseModel):
    code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=2, max_length=200)
    legal_name: str | None = Field(default=None, max_length=250)
    country_code: str = Field(default="KE", min_length=2, max_length=2)
    base_currency: str = Field(default="KES", min_length=3, max_length=3)
    timezone: str = Field(default="Africa/Nairobi", max_length=64)
    fiscal_year_start_month: int = Field(default=1, ge=1, le=12)


class CompanyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    legal_name: str | None
    country_code: str
    base_currency: str
    timezone: str
    fiscal_year_start_month: int
    status: str
    created_at: datetime


class BranchCreate(BaseModel):
    code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=2, max_length=200)
    address: str | None = Field(default=None, max_length=500)


class BranchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    code: str
    name: str
    address: str | None
    status: str


class DepartmentCreate(BaseModel):
    code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=2, max_length=200)


class DepartmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    code: str
    name: str
    status: str
