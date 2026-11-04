"""Declarative base, naming conventions and shared model mixins."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import DateTime, MetaData, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base for every persisted entity."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def to_dict(self) -> dict[str, Any]:
        """Return column values for audit diffs without lazy relationship loads."""

        return {column.key: getattr(self, column.key) for column in self.__table__.columns}


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class OptimisticLockMixin:
    """Version column that turns concurrent stale writes into a conflict."""

    version: Mapped[int] = mapped_column(nullable=False, default=1, server_default=text("1"))

    __mapper_args__: ClassVar[dict[str, Any]] = {"version_id_col": version}
