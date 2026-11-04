"""Audit trail writer. Callers supply the business transaction."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from nexus_api.audit.models import AuditEvent
from nexus_api.core.context import get_request_context


async def record_audit(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID | None,
    actor_user_id: uuid.UUID | None,
    action: str,
    entity_type: str,
    entity_id: str | uuid.UUID,
    result: str = "success",
    metadata: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> AuditEvent:
    context = get_request_context()
    event = AuditEvent(
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        result=result,
        correlation_id=context.request_id if context else "system",
        ip_address=ip_address,
        event_metadata=metadata or {},
    )
    session.add(event)
    await session.flush()
    return event
