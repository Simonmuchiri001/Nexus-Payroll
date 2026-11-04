"""Request correlation and tenant context helpers."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Immutable per-request context used for logs and audit correlation."""

    request_id: str
    organization_id: UUID | None = None
    actor_user_id: UUID | None = None


_current_context: ContextVar[RequestContext | None] = ContextVar(
    "nexus_request_context",
    default=None,
)


def set_request_context(context: RequestContext) -> None:
    _current_context.set(context)


def get_request_context() -> RequestContext | None:
    return _current_context.get()
