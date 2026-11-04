"""The audit trail is append-only at both application and database layers."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from nexus_api.audit.models import AuditEvent
from nexus_api.database.session import SessionFactory

from .conftest import AuthenticatedSession

pytestmark = pytest.mark.postgres


async def test_audit_events_cannot_be_updated_or_deleted(
    admin_session: AuthenticatedSession,
) -> None:
    async with SessionFactory() as session:
        events = (
            await session.scalars(
                select(AuditEvent)
                .where(AuditEvent.organization_id == uuid.UUID(admin_session.organization_id))
                .order_by(AuditEvent.created_at)
            )
        ).all()
        assert events
        original = events[0]
        original_id = original.id
        original_action = original.action

    async with SessionFactory() as session:
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(
                text("UPDATE audit_events SET action = :action WHERE id = :event_id"),
                {"action": "tampered", "event_id": original_id},
            )
        await session.rollback()

    async with SessionFactory() as session:
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(
                text("DELETE FROM audit_events WHERE id = :event_id"),
                {"event_id": original_id},
            )
        await session.rollback()

    async with SessionFactory() as session:
        unchanged = await session.get(AuditEvent, original_id)
        assert unchanged is not None
        assert unchanged.action == original_action


async def test_login_and_bootstrap_are_audited(
    admin_session: AuthenticatedSession,
) -> None:
    async with SessionFactory() as session:
        actions = set(
            (
                await session.scalars(
                    select(AuditEvent.action).where(
                        AuditEvent.organization_id == uuid.UUID(admin_session.organization_id)
                    )
                )
            ).all()
        )

    assert "organization.bootstrapped" in actions
    assert "auth.login" in actions
