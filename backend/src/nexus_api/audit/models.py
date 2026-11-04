"""Append-only audit trail."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from nexus_api.database.base import Base


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_org_created", "organization_id", "created_at"),
        Index("ix_audit_events_entity", "entity_type", "entity_id"),
        Index("ix_audit_events_actor", "actor_user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(80), nullable=False)
    result: Mapped[str] = mapped_column(String(20), nullable=False, default="success")
    correlation_id: Mapped[str] = mapped_column(String(80), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
