"""Identity API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class LoginRequest(BaseModel):
    organization_slug: str = Field(min_length=2, max_length=80)
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=512)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=512)


class SwitchOrganizationRequest(BaseModel):
    organization_slug: str = Field(min_length=2, max_length=80)
    refresh_token: str = Field(min_length=32, max_length=512)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth token type label, not a credential
    expires_in: int
    refresh_expires_in: int


class UserSummary(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    is_platform_admin: bool


class CompanySummary(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    base_currency: str
    country_code: str
    status: str


class MeResponse(BaseModel):
    user: UserSummary
    organization_id: uuid.UUID
    organization_slug: str
    organization_name: str
    membership_id: uuid.UUID
    permissions: list[str]
    companies: list[CompanySummary]


class LoginEventResponse(BaseModel):
    id: uuid.UUID
    email: str
    success: bool
    failure_reason: str | None
    ip_address: str | None
    created_at: datetime


class RoleGrantCreate(BaseModel):
    role_code: str = Field(min_length=2, max_length=50, pattern=r"^[a-z][a-z0-9_]*$")
    company_id: uuid.UUID | None = None
    branch_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def branch_requires_company(self) -> RoleGrantCreate:
        if self.branch_id is not None and self.company_id is None:
            raise ValueError("A branch-scoped role assignment also requires a company")
        return self


class RoleAssignmentResponse(BaseModel):
    id: uuid.UUID
    role_code: str
    role_name: str
    company_id: uuid.UUID | None
    branch_id: uuid.UUID | None


class RoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str
    is_system: bool


class MembershipCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=12, max_length=256)
    default_company_id: uuid.UUID | None = None
    role_assignments: list[RoleGrantCreate] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def distinct_role_scopes(self) -> MembershipCreate:
        scopes = {
            (grant.role_code, grant.company_id, grant.branch_id) for grant in self.role_assignments
        }
        if len(scopes) != len(self.role_assignments):
            raise ValueError("Duplicate role assignments are not permitted")
        return self


class MembershipUpdate(BaseModel):
    status: Literal["active", "suspended"] | None = None
    default_company_id: uuid.UUID | None = None


class MembershipResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    full_name: str
    user_active: bool
    status: str
    default_company_id: uuid.UUID | None
    role_assignments: list[RoleAssignmentResponse]
    created_at: datetime


class MembershipPage(BaseModel):
    items: list[MembershipResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
