"""Application error types and the uniform API error contract."""

from __future__ import annotations

from typing import Any


class NexusError(Exception):
    """Base class for expected, client-presentable failures."""

    status_code: int = 400
    code: str = "bad_request"
    message: str = "The request could not be completed."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.details = details or {}
        super().__init__(self.message)


class AuthenticationError(NexusError):
    status_code = 401
    code = "authentication_required"
    message = "Valid authentication credentials are required."


class PermissionDeniedError(NexusError):
    status_code = 403
    code = "permission_denied"
    message = "You do not have permission to perform this action."


class NotFoundError(NexusError):
    status_code = 404
    code = "not_found"
    message = "The requested record was not found."


class ConflictError(NexusError):
    status_code = 409
    code = "conflict"
    message = "The request conflicts with the current state of the record."


class ValidationFailureError(NexusError):
    status_code = 422
    code = "validation_failed"
    message = "The submitted data is invalid."


class RateLimitError(NexusError):
    status_code = 429
    code = "rate_limited"
    message = "Too many requests. Try again later."


class IntegrityError(NexusError):
    status_code = 500
    code = "integrity_violation"
    message = "A financial integrity check failed. The operation was not completed."


class ConfigurationError(NexusError):
    status_code = 500
    code = "configuration_error"
    message = "The system is not configured to perform this operation."


class ExternalServiceError(NexusError):
    status_code = 502
    code = "external_service_error"
    message = "An external provider could not complete the operation."
