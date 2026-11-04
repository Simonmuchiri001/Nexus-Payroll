"""Organisation, company, branch and department API routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from nexus_api.api.deps import AuthDep, SessionDep
from nexus_api.organizations import service
from nexus_api.organizations.schemas import (
    BranchCreate,
    BranchResponse,
    CompanyCreate,
    CompanyResponse,
    DepartmentCreate,
    DepartmentResponse,
    OrganizationResponse,
)

router = APIRouter(tags=["Organisations"])


@router.get("/organizations/current", response_model=OrganizationResponse)
async def current_organization(session: SessionDep, context: AuthDep) -> OrganizationResponse:
    organization = await service.get_current_organization(session, context)
    return OrganizationResponse.model_validate(organization)


@router.get("/companies", response_model=list[CompanyResponse])
async def companies(session: SessionDep, context: AuthDep) -> list[CompanyResponse]:
    return [
        CompanyResponse.model_validate(company)
        for company in await service.list_companies(session, context)
    ]


@router.post("/companies", response_model=CompanyResponse, status_code=201)
async def create_company(
    payload: CompanyCreate, session: SessionDep, context: AuthDep
) -> CompanyResponse:
    company = await service.create_company(session, context, payload)
    return CompanyResponse.model_validate(company)


@router.get("/companies/{company_id}", response_model=CompanyResponse)
async def company_detail(
    company_id: uuid.UUID, session: SessionDep, context: AuthDep
) -> CompanyResponse:
    company = await service.get_company(session, context, company_id)
    return CompanyResponse.model_validate(company)


@router.get("/companies/{company_id}/branches", response_model=list[BranchResponse])
async def branches(
    company_id: uuid.UUID, session: SessionDep, context: AuthDep
) -> list[BranchResponse]:
    return [
        BranchResponse.model_validate(branch)
        for branch in await service.list_branches(session, context, company_id)
    ]


@router.post("/companies/{company_id}/branches", response_model=BranchResponse, status_code=201)
async def create_branch(
    company_id: uuid.UUID,
    payload: BranchCreate,
    session: SessionDep,
    context: AuthDep,
) -> BranchResponse:
    branch = await service.create_branch(session, context, company_id, payload)
    return BranchResponse.model_validate(branch)


@router.get("/companies/{company_id}/departments", response_model=list[DepartmentResponse])
async def departments(
    company_id: uuid.UUID, session: SessionDep, context: AuthDep
) -> list[DepartmentResponse]:
    return [
        DepartmentResponse.model_validate(department)
        for department in await service.list_departments(session, context, company_id)
    ]


@router.post(
    "/companies/{company_id}/departments",
    response_model=DepartmentResponse,
    status_code=201,
)
async def create_department(
    company_id: uuid.UUID,
    payload: DepartmentCreate,
    session: SessionDep,
    context: AuthDep,
) -> DepartmentResponse:
    department = await service.create_department(session, context, company_id, payload)
    return DepartmentResponse.model_validate(department)
