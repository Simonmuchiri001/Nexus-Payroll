"""Authentication, session rotation, RBAC resolution and identity seeding."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_api.audit.models import AuditEvent
from nexus_api.audit.service import record_audit
from nexus_api.config import Settings
from nexus_api.core.errors import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ValidationFailureError,
)
from nexus_api.database.session import SessionFactory
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
from nexus_api.identity.permissions import (
    PERMISSION_DESCRIPTIONS,
    ROLE_NAMES,
    ROLE_PERMISSIONS,
    PermissionCode,
)
from nexus_api.identity.schemas import (
    CompanySummary,
    LoginRequest,
    MembershipCreate,
    MembershipResponse,
    MembershipUpdate,
    MeResponse,
    RoleAssignmentResponse,
    RoleGrantCreate,
    TokenPair,
    UserSummary,
)
from nexus_api.identity.security import (
    AccessTokenClaims,
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    validate_password_strength,
    verify_password,
)
from nexus_api.organizations.models import Branch, Company, Organization

AUTH_FAILED = "Invalid organisation, email or password."


@dataclass(frozen=True, slots=True)
class PermissionGrant:
    permission: str
    company_id: uuid.UUID | None
    branch_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class AuthContext:
    user: User
    membership: OrganizationMembership
    organization: Organization
    grants: tuple[PermissionGrant, ...]
    accessible_companies: frozenset[uuid.UUID]

    def has_permission(
        self,
        permission: PermissionCode,
        *,
        company_id: uuid.UUID | None = None,
        organization_wide: bool = False,
    ) -> bool:
        if self.user.is_platform_admin:
            return True
        value = permission.value
        for grant in self.grants:
            if grant.permission != value:
                continue
            if organization_wide:
                if grant.company_id is None:
                    return True
                continue
            if company_id is None or grant.company_id is None or grant.company_id == company_id:
                return True
        return False

    def require(
        self,
        permission: PermissionCode,
        *,
        company_id: uuid.UUID | None = None,
        organization_wide: bool = False,
    ) -> None:
        if not self.has_permission(
            permission, company_id=company_id, organization_wide=organization_wide
        ):
            raise PermissionDeniedError(
                f"Permission '{permission.value}' is required for this operation."
            )


async def seed_identity(session: AsyncSession) -> None:
    """Idempotently create the permission and built-in role catalogue."""

    existing_permissions = set((await session.scalars(select(Permission.code))).all())
    for code, (domain, description) in PERMISSION_DESCRIPTIONS.items():
        if code.value not in existing_permissions:
            session.add(Permission(code=code.value, domain=domain, description=description))

    roles_by_code: dict[str, Role] = {
        role.code: role for role in (await session.scalars(select(Role))).all()
    }
    for code, name in ROLE_NAMES.items():
        role = roles_by_code.get(code)
        if role is None:
            role = Role(code=code, name=name, description=f"Built-in {name} role")
            session.add(role)
            roles_by_code[code] = role
            await session.flush()
        existing = set(
            (
                await session.scalars(
                    select(RolePermission.permission_code).where(RolePermission.role_id == role.id)
                )
            ).all()
        )
        for permission in ROLE_PERMISSIONS[code]:
            if permission.value not in existing:
                session.add(RolePermission(role_id=role.id, permission_code=permission.value))


async def _record_login_event(
    *,
    user_id: uuid.UUID | None,
    email: str,
    success: bool,
    failure_reason: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    """Persist security history independently of the request transaction."""

    async with SessionFactory() as session, session.begin():
        session.add(
            LoginEvent(
                user_id=user_id,
                email=email.lower().strip(),
                success=success,
                failure_reason=failure_reason,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        )


async def _enforce_login_rate_limit(
    session: AsyncSession, *, email: str, settings: Settings
) -> None:
    since = datetime.now(UTC) - timedelta(seconds=settings.login_window_seconds)
    failures = await session.scalar(
        select(func.count(LoginEvent.id)).where(
            LoginEvent.email == email.lower().strip(),
            LoginEvent.success.is_(False),
            LoginEvent.created_at >= since,
        )
    )
    if (failures or 0) >= settings.max_login_attempts:
        raise RateLimitError(
            "Too many failed sign-in attempts. Wait before trying again.",
            details={"retry_after_seconds": settings.login_window_seconds},
        )


def _token_response(
    settings: Settings,
    *,
    user_id: uuid.UUID,
    membership_id: uuid.UUID,
    organization_id: uuid.UUID,
    refresh_token: str,
) -> TokenPair:
    access_token, expires_in = create_access_token(
        settings,
        user_id=user_id,
        membership_id=membership_id,
        organization_id=organization_id,
    )
    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        refresh_expires_in=settings.refresh_token_days * 24 * 60 * 60,
    )


async def _store_refresh_token(
    session: AsyncSession,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    membership_id: uuid.UUID,
    user_agent: str | None,
    ip_address: str | None,
) -> tuple[str, RefreshToken]:
    raw = generate_refresh_token()
    token = RefreshToken(
        jti=uuid.uuid4(),
        user_id=user_id,
        membership_id=membership_id,
        token_hash=hash_refresh_token(raw),
        expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_days),
        user_agent=user_agent,
        ip_address=ip_address,
    )
    session.add(token)
    await session.flush()
    return raw, token


async def authenticate(
    session: AsyncSession,
    settings: Settings,
    request: LoginRequest,
    *,
    ip_address: str | None,
    user_agent: str | None,
) -> TokenPair:
    email = request.email.lower().strip()
    await _enforce_login_rate_limit(session, email=email, settings=settings)

    user = await session.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(request.password, user.password_hash):
        await _record_login_event(
            user_id=user.id if user else None,
            email=email,
            success=False,
            failure_reason="invalid_credentials",
            ip_address=ip_address,
            user_agent=user_agent,
        )
        raise AuthenticationError(AUTH_FAILED)
    if not user.is_active:
        await _record_login_event(
            user_id=user.id,
            email=email,
            success=False,
            failure_reason="account_suspended",
            ip_address=ip_address,
            user_agent=user_agent,
        )
        raise AuthenticationError(AUTH_FAILED)

    membership = await session.scalar(
        select(OrganizationMembership)
        .join(Organization, Organization.id == OrganizationMembership.organization_id)
        .where(
            OrganizationMembership.user_id == user.id,
            OrganizationMembership.status == "active",
            Organization.slug == request.organization_slug.lower().strip(),
            Organization.status == "active",
        )
    )
    if membership is None:
        await _record_login_event(
            user_id=user.id,
            email=email,
            success=False,
            failure_reason="no_active_membership",
            ip_address=ip_address,
            user_agent=user_agent,
        )
        raise AuthenticationError(AUTH_FAILED)

    refresh_raw, _ = await _store_refresh_token(
        session,
        settings,
        user_id=user.id,
        membership_id=membership.id,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    user.last_login_at = datetime.now(UTC)
    await _record_login_event(
        user_id=user.id,
        email=email,
        success=True,
        failure_reason=None,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    await record_audit(
        session,
        organization_id=membership.organization_id,
        actor_user_id=user.id,
        action="auth.login",
        entity_type="user",
        entity_id=user.id,
        ip_address=ip_address,
    )
    return _token_response(
        settings,
        user_id=user.id,
        membership_id=membership.id,
        organization_id=membership.organization_id,
        refresh_token=refresh_raw,
    )


async def _load_context_from_claims(
    session: AsyncSession, claims: AccessTokenClaims
) -> AuthContext:
    row = (
        await session.execute(
            select(User, OrganizationMembership, Organization)
            .join(
                OrganizationMembership,
                OrganizationMembership.user_id == User.id,
            )
            .join(
                Organization,
                Organization.id == OrganizationMembership.organization_id,
            )
            .where(
                User.id == claims.user_id,
                OrganizationMembership.id == claims.membership_id,
                Organization.id == claims.organization_id,
            )
        )
    ).one_or_none()
    if row is None:
        raise AuthenticationError("Your membership is no longer available.")
    user, membership, organization = row
    if not user.is_active or membership.status != "active" or organization.status != "active":
        raise AuthenticationError("Your account or organisation is not active.")

    grant_rows = (
        await session.execute(
            select(
                RolePermission.permission_code,
                RoleAssignment.company_id,
                RoleAssignment.branch_id,
            )
            .join(RoleAssignment, RoleAssignment.role_id == RolePermission.role_id)
            .where(RoleAssignment.membership_id == membership.id)
            .distinct()
        )
    ).all()
    grants = tuple(
        PermissionGrant(permission=code, company_id=company_id, branch_id=branch_id)
        for code, company_id, branch_id in grant_rows
    )

    if user.is_platform_admin:
        company_ids = set(
            (
                await session.scalars(
                    select(Company.id).where(Company.organization_id == organization.id)
                )
            ).all()
        )
    else:
        scoped = {grant.company_id for grant in grants if grant.company_id is not None}
        org_wide = any(
            grant.company_id is None and grant.permission == PermissionCode.COMPANY_VIEW.value
            for grant in grants
        )
        if org_wide:
            company_ids = set(
                (
                    await session.scalars(
                        select(Company.id).where(Company.organization_id == organization.id)
                    )
                ).all()
            )
        else:
            company_ids = set(scoped)

    return AuthContext(
        user=user,
        membership=membership,
        organization=organization,
        grants=grants,
        accessible_companies=frozenset(company_ids),
    )


async def get_auth_context(session: AsyncSession, settings: Settings, token: str) -> AuthContext:
    claims = decode_access_token(settings, token)
    return await _load_context_from_claims(session, claims)


async def _revoke_all_for_membership(membership_id: uuid.UUID, *, reason: str) -> None:
    async with SessionFactory() as session, session.begin():
        membership = await session.get(OrganizationMembership, membership_id)
        tokens = (
            await session.scalars(
                select(RefreshToken).where(
                    RefreshToken.membership_id == membership_id,
                    RefreshToken.revoked_at.is_(None),
                )
            )
        ).all()
        now = datetime.now(UTC)
        for token in tokens:
            token.revoked_at = now
        session.add(
            AuditEvent(
                organization_id=membership.organization_id if membership else None,
                actor_user_id=membership.user_id if membership else None,
                action="auth.refresh_reuse_detected",
                entity_type="membership",
                entity_id=str(membership_id),
                result="failure",
                correlation_id="security",
                event_metadata={"reason": reason, "revoked_tokens": len(tokens)},
            )
        )


async def refresh_tokens(
    session: AsyncSession,
    settings: Settings,
    raw_token: str,
    *,
    ip_address: str | None,
    user_agent: str | None,
) -> TokenPair:
    token = await session.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == hash_refresh_token(raw_token))
        .with_for_update()
    )
    if token is None:
        raise AuthenticationError("The refresh token is invalid.")
    now = datetime.now(UTC)
    if token.revoked_at is not None:
        await _revoke_all_for_membership(token.membership_id, reason="refresh_token_reuse")
        raise AuthenticationError("Refresh token reuse detected. All sessions were revoked.")
    if token.expires_at <= now:
        raise AuthenticationError("The refresh token has expired.")

    membership = await session.get(OrganizationMembership, token.membership_id)
    if membership is None or membership.status != "active":
        raise AuthenticationError("The membership is no longer active.")
    user = await session.get(User, token.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("The account is no longer active.")

    refresh_raw, replacement = await _store_refresh_token(
        session,
        settings,
        user_id=user.id,
        membership_id=membership.id,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    token.revoked_at = now
    token.replaced_by_jti = replacement.jti
    await record_audit(
        session,
        organization_id=membership.organization_id,
        actor_user_id=user.id,
        action="auth.refresh",
        entity_type="session",
        entity_id=token.jti,
        ip_address=ip_address,
    )
    return _token_response(
        settings,
        user_id=user.id,
        membership_id=membership.id,
        organization_id=membership.organization_id,
        refresh_token=refresh_raw,
    )


async def logout(session: AsyncSession, raw_token: str) -> None:
    token = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw_token))
    )
    if token is not None and token.revoked_at is None:
        token.revoked_at = datetime.now(UTC)
        await record_audit(
            session,
            organization_id=None,
            actor_user_id=token.user_id,
            action="auth.logout",
            entity_type="session",
            entity_id=token.jti,
        )


async def switch_organization(
    session: AsyncSession,
    settings: Settings,
    context: AuthContext,
    request_organization_slug: str,
    current_refresh_token: str,
    *,
    ip_address: str | None,
    user_agent: str | None,
) -> TokenPair:
    target = await session.scalar(
        select(OrganizationMembership)
        .join(Organization, Organization.id == OrganizationMembership.organization_id)
        .where(
            OrganizationMembership.user_id == context.user.id,
            OrganizationMembership.status == "active",
            Organization.slug == request_organization_slug.lower().strip(),
            Organization.status == "active",
        )
    )
    if target is None:
        raise NotFoundError("No active membership exists for that organisation.")

    current = await session.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == hash_refresh_token(current_refresh_token))
        .with_for_update()
    )
    if current is None or current.user_id != context.user.id or current.revoked_at is not None:
        raise AuthenticationError("The current session could not be rotated.")

    refresh_raw, replacement = await _store_refresh_token(
        session,
        settings,
        user_id=context.user.id,
        membership_id=target.id,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    current.revoked_at = datetime.now(UTC)
    current.replaced_by_jti = replacement.jti
    await record_audit(
        session,
        organization_id=target.organization_id,
        actor_user_id=context.user.id,
        action="auth.switch_organization",
        entity_type="membership",
        entity_id=target.id,
        metadata={"from_organization_id": str(context.organization.id)},
        ip_address=ip_address,
    )
    return _token_response(
        settings,
        user_id=context.user.id,
        membership_id=target.id,
        organization_id=target.organization_id,
        refresh_token=refresh_raw,
    )


async def build_me_response(session: AsyncSession, context: AuthContext) -> MeResponse:
    companies = (
        await session.scalars(
            select(Company)
            .where(
                Company.organization_id == context.organization.id,
                Company.id.in_(context.accessible_companies),
            )
            .order_by(Company.name)
        )
    ).all()
    permissions = sorted({grant.permission for grant in context.grants})
    if context.user.is_platform_admin:
        permissions = sorted(permission.value for permission in PermissionCode)
    return MeResponse(
        user=UserSummary(
            id=context.user.id,
            email=context.user.email,
            full_name=context.user.full_name,
            is_platform_admin=context.user.is_platform_admin,
        ),
        organization_id=context.organization.id,
        organization_slug=context.organization.slug,
        organization_name=context.organization.name,
        membership_id=context.membership.id,
        permissions=permissions,
        companies=[
            CompanySummary(
                id=company.id,
                code=company.code,
                name=company.name,
                base_currency=company.base_currency,
                country_code=company.country_code,
                status=company.status,
            )
            for company in companies
        ],
    )


async def validate_company_access(
    session: AsyncSession,
    context: AuthContext,
    company_id: uuid.UUID,
    permission: PermissionCode,
) -> Company:
    company = await session.scalar(
        select(Company).where(
            Company.id == company_id,
            Company.organization_id == context.organization.id,
        )
    )
    if company is None:
        raise NotFoundError("Company not found.")
    if company.id not in context.accessible_companies:
        raise PermissionDeniedError("You do not have access to that company.")
    context.require(permission, company_id=company.id)
    return company


async def ensure_membership_company(
    session: AsyncSession,
    context: AuthContext,
    membership_id: uuid.UUID,
    company_id: uuid.UUID | None,
) -> None:
    if company_id is None:
        return
    company = await session.scalar(
        select(Company.id).where(
            Company.id == company_id,
            Company.organization_id == context.organization.id,
        )
    )
    if company is None:
        raise ValidationFailureError("The selected company does not belong to the organisation.")
    if membership_id == context.membership.id and company_id not in context.accessible_companies:
        raise PermissionDeniedError("You do not have access to that company.")


async def _load_role_assignments(
    session: AsyncSession, membership_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[RoleAssignmentResponse]]:
    if not membership_ids:
        return {}
    rows = (
        await session.execute(
            select(RoleAssignment, Role)
            .join(Role, Role.id == RoleAssignment.role_id)
            .where(RoleAssignment.membership_id.in_(membership_ids))
            .order_by(Role.name, RoleAssignment.company_id)
        )
    ).all()
    result: dict[uuid.UUID, list[RoleAssignmentResponse]] = {
        membership_id: [] for membership_id in membership_ids
    }
    for assignment, role in rows:
        result[assignment.membership_id].append(
            RoleAssignmentResponse(
                id=assignment.id,
                role_code=role.code,
                role_name=role.name,
                company_id=assignment.company_id,
                branch_id=assignment.branch_id,
            )
        )
    return result


def _membership_response(
    membership: OrganizationMembership,
    user: User,
    assignments: list[RoleAssignmentResponse],
) -> MembershipResponse:
    return MembershipResponse(
        id=membership.id,
        organization_id=membership.organization_id,
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        user_active=user.is_active,
        status=membership.status,
        default_company_id=membership.default_company_id,
        role_assignments=assignments,
        created_at=membership.created_at,
    )


async def _get_membership(
    session: AsyncSession,
    context: AuthContext,
    membership_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> tuple[OrganizationMembership, User]:
    statement = (
        select(OrganizationMembership, User)
        .join(User, User.id == OrganizationMembership.user_id)
        .where(
            OrganizationMembership.id == membership_id,
            OrganizationMembership.organization_id == context.organization.id,
        )
    )
    if for_update:
        statement = statement.with_for_update()
    row = (await session.execute(statement)).one_or_none()
    if row is None:
        raise NotFoundError("User membership not found.")
    return row


async def list_memberships(
    session: AsyncSession,
    context: AuthContext,
    *,
    page: int,
    page_size: int,
    search: str | None = None,
    status: str | None = None,
) -> tuple[list[MembershipResponse], int]:
    context.require(PermissionCode.USER_VIEW, organization_wide=True)
    filters = [OrganizationMembership.organization_id == context.organization.id]
    if status is not None:
        filters.append(OrganizationMembership.status == status)
    if search:
        term = f"%{search.strip().lower()}%"
        filters.append(func.lower(User.email).like(term) | func.lower(User.full_name).like(term))
    total = await session.scalar(
        select(func.count(OrganizationMembership.id))
        .join(User, User.id == OrganizationMembership.user_id)
        .where(*filters)
    )
    rows = (
        await session.execute(
            select(OrganizationMembership, User)
            .join(User, User.id == OrganizationMembership.user_id)
            .where(*filters)
            .order_by(User.full_name, User.email)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    assignments = await _load_role_assignments(session, [membership.id for membership, _ in rows])
    return [
        _membership_response(
            membership,
            user,
            assignments.get(membership.id, []),
        )
        for membership, user in rows
    ], total or 0


async def get_membership(
    session: AsyncSession, context: AuthContext, membership_id: uuid.UUID
) -> MembershipResponse:
    context.require(PermissionCode.USER_VIEW, organization_wide=True)
    membership, user = await _get_membership(session, context, membership_id)
    assignments = await _load_role_assignments(session, [membership.id])
    return _membership_response(membership, user, assignments.get(membership.id, []))


async def list_roles(session: AsyncSession, context: AuthContext) -> list[Role]:
    context.require(PermissionCode.USER_VIEW, organization_wide=True)
    return list((await session.scalars(select(Role).order_by(Role.name))).all())


async def _validate_role_grant(
    session: AsyncSession,
    context: AuthContext,
    grant: RoleGrantCreate,
    roles_by_code: dict[str, Role] | None = None,
) -> Role:
    role = (
        roles_by_code.get(grant.role_code)
        if roles_by_code is not None
        else await session.scalar(select(Role).where(Role.code == grant.role_code))
    )
    if role is None:
        raise ValidationFailureError(f"Unknown role '{grant.role_code}'.")
    if grant.company_id is not None:
        company = await session.scalar(
            select(Company.id).where(
                Company.id == grant.company_id,
                Company.organization_id == context.organization.id,
            )
        )
        if company is None:
            raise ValidationFailureError(
                "A role assignment company does not belong to the organisation."
            )
    if grant.branch_id is not None and grant.company_id is not None:
        branch_company = await session.scalar(
            select(Branch.company_id).where(Branch.id == grant.branch_id)
        )
        if branch_company != grant.company_id:
            raise ValidationFailureError(
                "The selected branch does not belong to the role assignment company."
            )
    return role


async def _validate_default_company(
    session: AsyncSession, context: AuthContext, company_id: uuid.UUID | None
) -> None:
    if company_id is None:
        return
    exists = await session.scalar(
        select(Company.id).where(
            Company.id == company_id,
            Company.organization_id == context.organization.id,
        )
    )
    if exists is None:
        raise ValidationFailureError("The default company does not belong to the organisation.")


async def create_membership(
    session: AsyncSession,
    context: AuthContext,
    payload: MembershipCreate,
) -> MembershipResponse:
    context.require(PermissionCode.USER_MANAGE, organization_wide=True)
    validate_password_strength(payload.password)
    email = str(payload.email).lower().strip()
    if await session.scalar(select(User.id).where(User.email == email)) is not None:
        raise ConflictError("A user with that email address already exists.")
    await _validate_default_company(session, context, payload.default_company_id)

    role_codes = sorted({grant.role_code for grant in payload.role_assignments})
    roles = {
        role.code: role
        for role in (await session.scalars(select(Role).where(Role.code.in_(role_codes)))).all()
    }
    for grant in payload.role_assignments:
        await _validate_role_grant(session, context, grant, roles)

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name.strip(),
        is_platform_admin=False,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    membership = OrganizationMembership(
        organization_id=context.organization.id,
        user_id=user.id,
        default_company_id=payload.default_company_id,
        status="active",
    )
    session.add(membership)
    await session.flush()

    assignments: list[RoleAssignmentResponse] = []
    for grant in payload.role_assignments:
        role = roles[grant.role_code]
        assignment = RoleAssignment(
            organization_id=context.organization.id,
            membership_id=membership.id,
            role_id=role.id,
            company_id=grant.company_id,
            branch_id=grant.branch_id,
            assigned_by=context.user.id,
        )
        session.add(assignment)
        await session.flush()
        assignments.append(
            RoleAssignmentResponse(
                id=assignment.id,
                role_code=role.code,
                role_name=role.name,
                company_id=grant.company_id,
                branch_id=grant.branch_id,
            )
        )
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="membership.created",
        entity_type="organization_membership",
        entity_id=membership.id,
        metadata={
            "user_id": str(user.id),
            "email": email,
            "role_codes": role_codes,
        },
    )
    return _membership_response(membership, user, assignments)


async def _membership_has_role(
    session: AsyncSession, membership_id: uuid.UUID, role_code: str
) -> bool:
    value = await session.scalar(
        select(RoleAssignment.id)
        .join(Role, Role.id == RoleAssignment.role_id)
        .where(
            RoleAssignment.membership_id == membership_id,
            Role.code == role_code,
        )
        .limit(1)
    )
    return value is not None


async def _ensure_not_last_admin(
    session: AsyncSession, organization_id: uuid.UUID, *, excluding_membership_id: uuid.UUID
) -> None:
    remaining = await session.scalar(
        select(func.count(func.distinct(OrganizationMembership.id)))
        .select_from(OrganizationMembership)
        .join(RoleAssignment, RoleAssignment.membership_id == OrganizationMembership.id)
        .join(Role, Role.id == RoleAssignment.role_id)
        .where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.status == "active",
            OrganizationMembership.id != excluding_membership_id,
            Role.code == "org_admin",
        )
    )
    if not remaining:
        raise ConflictError("The organisation must retain at least one active administrator.")


async def _revoke_membership_sessions(session: AsyncSession, membership_id: uuid.UUID) -> int:
    tokens = (
        await session.scalars(
            select(RefreshToken).where(
                RefreshToken.membership_id == membership_id,
                RefreshToken.revoked_at.is_(None),
            )
        )
    ).all()
    now = datetime.now(UTC)
    for token in tokens:
        token.revoked_at = now
    return len(tokens)


async def update_membership(
    session: AsyncSession,
    context: AuthContext,
    membership_id: uuid.UUID,
    payload: MembershipUpdate,
) -> MembershipResponse:
    context.require(PermissionCode.USER_MANAGE, organization_wide=True)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise ValidationFailureError("No membership fields were supplied.")
    membership, user = await _get_membership(session, context, membership_id, for_update=True)
    if "default_company_id" in changes:
        await _validate_default_company(session, context, payload.default_company_id)
        membership.default_company_id = payload.default_company_id
    if payload.status is not None and payload.status != membership.status:
        if membership.id == context.membership.id and payload.status == "suspended":
            raise ConflictError("You cannot suspend your own membership.")
        if payload.status == "suspended":
            if await _membership_has_role(session, membership.id, "org_admin"):
                await _ensure_not_last_admin(
                    session,
                    context.organization.id,
                    excluding_membership_id=membership.id,
                )
            revoked = await _revoke_membership_sessions(session, membership.id)
            await record_audit(
                session,
                organization_id=context.organization.id,
                actor_user_id=context.user.id,
                action="membership.sessions_revoked",
                entity_type="organization_membership",
                entity_id=membership.id,
                metadata={"revoked_tokens": revoked},
            )
        membership.status = payload.status
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="membership.updated",
        entity_type="organization_membership",
        entity_id=membership.id,
        metadata={"fields": sorted(changes)},
    )
    assignments = await _load_role_assignments(session, [membership.id])
    return _membership_response(membership, user, assignments.get(membership.id, []))


async def add_role_assignment(
    session: AsyncSession,
    context: AuthContext,
    membership_id: uuid.UUID,
    payload: RoleGrantCreate,
) -> MembershipResponse:
    context.require(PermissionCode.USER_MANAGE, organization_wide=True)
    membership, user = await _get_membership(session, context, membership_id, for_update=True)
    role = await _validate_role_grant(session, context, payload)
    duplicate = await session.scalar(
        select(RoleAssignment.id).where(
            RoleAssignment.membership_id == membership.id,
            RoleAssignment.role_id == role.id,
            RoleAssignment.company_id.is_not_distinct_from(payload.company_id),
            RoleAssignment.branch_id.is_not_distinct_from(payload.branch_id),
        )
    )
    if duplicate is not None:
        raise ConflictError("That role assignment already exists.")
    assignment = RoleAssignment(
        organization_id=context.organization.id,
        membership_id=membership.id,
        role_id=role.id,
        company_id=payload.company_id,
        branch_id=payload.branch_id,
        assigned_by=context.user.id,
    )
    session.add(assignment)
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="membership.role_assigned",
        entity_type="organization_membership",
        entity_id=membership.id,
        metadata={
            "role_code": role.code,
            "company_id": str(payload.company_id) if payload.company_id else None,
            "branch_id": str(payload.branch_id) if payload.branch_id else None,
        },
    )
    assignments = await _load_role_assignments(session, [membership.id])
    return _membership_response(membership, user, assignments.get(membership.id, []))


async def remove_role_assignment(
    session: AsyncSession,
    context: AuthContext,
    membership_id: uuid.UUID,
    assignment_id: uuid.UUID,
) -> MembershipResponse:
    context.require(PermissionCode.USER_MANAGE, organization_wide=True)
    membership, user = await _get_membership(session, context, membership_id, for_update=True)
    assignment_row = (
        await session.execute(
            select(RoleAssignment, Role)
            .join(Role, Role.id == RoleAssignment.role_id)
            .where(
                RoleAssignment.id == assignment_id,
                RoleAssignment.membership_id == membership.id,
            )
        )
    ).one_or_none()
    if assignment_row is None:
        raise NotFoundError("Role assignment not found.")
    assignment, role = assignment_row
    if role.code == "org_admin" and membership.status == "active":
        await _ensure_not_last_admin(
            session,
            context.organization.id,
            excluding_membership_id=membership.id,
        )
    await session.delete(assignment)
    await session.flush()
    await record_audit(
        session,
        organization_id=context.organization.id,
        actor_user_id=context.user.id,
        action="membership.role_removed",
        entity_type="organization_membership",
        entity_id=membership.id,
        metadata={"role_code": role.code, "assignment_id": str(assignment_id)},
    )
    assignments = await _load_role_assignments(session, [membership.id])
    return _membership_response(membership, user, assignments.get(membership.id, []))
