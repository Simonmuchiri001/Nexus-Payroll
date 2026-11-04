"""Password hashing and JWT/refresh-token primitives."""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

import jwt
from pwdlib import PasswordHash

from nexus_api.config import Settings
from nexus_api.core.errors import AuthenticationError, ValidationFailureError

_password_hash = PasswordHash.recommended()
_dummy_hash = _password_hash.hash("nexus-dummy-password-used-for-timing-only")


def validate_password_strength(password: str, minimum_length: int = 12) -> None:
    if len(password) < minimum_length:
        raise ValidationFailureError(f"Password must contain at least {minimum_length} characters.")
    classes = 0
    classes += bool(re.search(r"[a-z]", password))
    classes += bool(re.search(r"[A-Z]", password))
    classes += bool(re.search(r"[0-9]", password))
    classes += bool(re.search(r"[^A-Za-z0-9]", password))
    if classes < 3:
        raise ValidationFailureError(
            "Password must use at least three of: lowercase, uppercase, digits, symbols."
        )


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Verify a password and use a dummy hash when the account is unknown.

    This keeps the failure path close to constant-time and avoids account
    enumeration through response timing.
    """

    if password_hash is None:
        _password_hash.verify(password, _dummy_hash)
        return False
    try:
        return _password_hash.verify(password, password_hash)
    except Exception:
        return False


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    user_id: uuid.UUID
    membership_id: uuid.UUID
    organization_id: uuid.UUID
    jti: uuid.UUID


def create_access_token(
    settings: Settings,
    *,
    user_id: uuid.UUID,
    membership_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> tuple[str, int]:
    """Return a signed access token and its lifetime in seconds."""

    now = datetime.now(UTC)
    expires = now + timedelta(minutes=settings.access_token_minutes)
    payload: dict[str, object] = {
        "sub": str(user_id),
        "org": str(organization_id),
        "membership": str(membership_id),
        "type": "access",
        "iss": settings.jwt_issuer,
        "aud": "nexus-desktop",
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(  # pyright: ignore[reportUnknownMemberType] - PyJWT key type is unannotated
        payload,
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    return token, settings.access_token_minutes * 60


def decode_access_token(settings: Settings, token: str) -> AccessTokenClaims:
    try:
        decoded = jwt.decode(  # pyright: ignore[reportUnknownMemberType] - PyJWT key type is unannotated
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            audience="nexus-desktop",
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "org", "membership", "type", "jti"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Your session has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("The authentication token is invalid.") from exc

    payload = cast(dict[str, object], decoded)
    if payload.get("type") != "access":
        raise AuthenticationError("The authentication token has the wrong type.")
    try:
        return AccessTokenClaims(
            user_id=uuid.UUID(str(payload["sub"])),
            membership_id=uuid.UUID(str(payload["membership"])),
            organization_id=uuid.UUID(str(payload["org"])),
            jti=uuid.UUID(str(payload["jti"])),
        )
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("The authentication token is malformed.") from exc
