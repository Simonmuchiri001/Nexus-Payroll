"""Tenant-scoped user, membership and role administration."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response

from nexus_api.api.deps import AuthDep, PaginationDep, SessionDep
from nexus_api.identity import service
from nexus_api.identity.schemas import (
    MembershipCreate,
    MembershipPage,
    MembershipResponse,
    MembershipUpdate,
    RoleGrantCreate,
    RoleResponse,
)

router = APIRouter(tags=["Identity administration"])


@router.get("/users", response_model=MembershipPage, summary="List organisation memberships")
async def users(
    session: SessionDep,
    context: AuthDep,
    pagination: PaginationDep,
    q: Annotated[str | None, Query(max_length=120)] = None,
    status: Annotated[str | None, Query(pattern="^(active|suspended)$")] = None,
) -> MembershipPage:
    items, total = await service.list_memberships(
        session,
        context,
        page=pagination.page,
        page_size=pagination.page_size,
        search=q,
        status=status,
    )
    return MembershipPage(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.post("/users", response_model=MembershipResponse, status_code=201)
async def create_user(
    payload: MembershipCreate,
    response: Response,
    session: SessionDep,
    context: AuthDep,
) -> MembershipResponse:
    membership = await service.create_membership(session, context, payload)
    response.headers["Location"] = f"/api/v1/users/{membership.id}"
    return membership


@router.get("/users/{membership_id}", response_model=MembershipResponse)
async def user_detail(
    membership_id: uuid.UUID, session: SessionDep, context: AuthDep
) -> MembershipResponse:
    return await service.get_membership(session, context, membership_id)


@router.patch("/users/{membership_id}", response_model=MembershipResponse)
async def update_user(
    membership_id: uuid.UUID,
    payload: MembershipUpdate,
    session: SessionDep,
    context: AuthDep,
) -> MembershipResponse:
    return await service.update_membership(session, context, membership_id, payload)


@router.post(
    "/users/{membership_id}/role-assignments",
    response_model=MembershipResponse,
    status_code=201,
)
async def assign_role(
    membership_id: uuid.UUID,
    payload: RoleGrantCreate,
    session: SessionDep,
    context: AuthDep,
) -> MembershipResponse:
    return await service.add_role_assignment(session, context, membership_id, payload)


@router.delete(
    "/users/{membership_id}/role-assignments/{assignment_id}",
    response_model=MembershipResponse,
)
async def remove_role(
    membership_id: uuid.UUID,
    assignment_id: uuid.UUID,
    session: SessionDep,
    context: AuthDep,
) -> MembershipResponse:
    return await service.remove_role_assignment(session, context, membership_id, assignment_id)


@router.get("/roles", response_model=list[RoleResponse], summary="List built-in roles")
async def roles(session: SessionDep, context: AuthDep) -> list[RoleResponse]:
    return [
        RoleResponse.model_validate(role) for role in await service.list_roles(session, context)
    ]
