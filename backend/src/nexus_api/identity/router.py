"""Authentication and current-session API routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from nexus_api.api.deps import AuthDep, SessionDep, SettingsDep, client_ip, user_agent
from nexus_api.identity.schemas import (
    LoginRequest,
    LogoutRequest,
    MeResponse,
    RefreshRequest,
    SwitchOrganizationRequest,
    TokenPair,
)
from nexus_api.identity.service import (
    authenticate,
    build_me_response,
    logout,
    refresh_tokens,
    switch_organization,
)
from nexus_api.shared.schemas import MessageResponse

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", response_model=TokenPair, summary="Authenticate a user")
async def login(
    payload: LoginRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> TokenPair:
    return await authenticate(
        session,
        settings,
        payload,
        ip_address=client_ip(request),
        user_agent=user_agent(request),
    )


@router.post("/refresh", response_model=TokenPair, summary="Rotate a refresh token")
async def refresh(
    payload: RefreshRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> TokenPair:
    return await refresh_tokens(
        session,
        settings,
        payload.refresh_token,
        ip_address=client_ip(request),
        user_agent=user_agent(request),
    )


@router.post("/logout", response_model=MessageResponse, summary="Revoke a session")
async def logout_session(
    payload: LogoutRequest,
    _: AuthDep,
    session: SessionDep,
) -> MessageResponse:
    await logout(session, payload.refresh_token)
    return MessageResponse(message="Session revoked.")


@router.get("/me", response_model=MeResponse, summary="Current user and access scope")
async def me(session: SessionDep, context: AuthDep) -> MeResponse:
    return await build_me_response(session, context)


@router.post(
    "/switch-organization",
    response_model=TokenPair,
    summary="Switch the active tenant membership",
)
async def switch_org(
    payload: SwitchOrganizationRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    context: AuthDep,
) -> TokenPair:
    return await switch_organization(
        session,
        settings,
        context,
        payload.organization_slug,
        payload.refresh_token,
        ip_address=client_ip(request),
        user_agent=user_agent(request),
    )
