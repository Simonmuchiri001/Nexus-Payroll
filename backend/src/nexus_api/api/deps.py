"""FastAPI dependency wiring for authentication, tenancy and permissions."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_api.config import Settings, get_settings
from nexus_api.core.context import RequestContext, get_request_context, set_request_context
from nexus_api.core.errors import AuthenticationError
from nexus_api.database.session import get_session
from nexus_api.identity.permissions import PermissionCode
from nexus_api.identity.service import AuthContext, get_auth_context

_bearer = HTTPBearer(auto_error=False, description="Nexus access token")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def client_ip(request: Request) -> str | None:
    """Return the peer address. Forwarded headers are deliberately not trusted."""

    return request.client.host if request.client else None


def user_agent(request: Request) -> str | None:
    value = request.headers.get("user-agent")
    return value[:500] if value else None


async def current_auth_context(
    request: Request,
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: SettingsDep,
) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError()
    context = await get_auth_context(session, settings, credentials.credentials)
    existing = get_request_context()
    if existing is not None:
        set_request_context(
            RequestContext(
                request_id=existing.request_id,
                organization_id=context.organization.id,
                actor_user_id=context.user.id,
            )
        )
    return context


AuthDep = Annotated[AuthContext, Depends(current_auth_context)]


def require_permission(permission: PermissionCode) -> object:
    """Create a dependency that enforces an organisation-wide permission."""

    async def dependency(context: AuthDep) -> AuthContext:
        context.require(permission, organization_wide=True)
        return context

    return dependency


class PaginationParams:
    def __init__(
        self,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> None:
        self.page = page
        self.page_size = page_size
        self.offset = (page - 1) * page_size


PaginationDep = Annotated[PaginationParams, Depends(PaginationParams)]
