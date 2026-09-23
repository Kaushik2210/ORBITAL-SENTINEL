"""Signed, short-lived JWTs (HS256) for the API's own auth. One secret (``JWT_SECRET``), no
external identity provider — this is a portfolio-scale deployment, not a multi-tenant product.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

ALGORITHM = "HS256"
DEFAULT_TTL = timedelta(hours=8)
ROLES = ("viewer", "analyst", "admin")
ROLE_RANK: dict[str, int] = {r: i for i, r in enumerate(ROLES)}


@dataclass(frozen=True, slots=True)
class Principal:
    email: str
    role: str


def issue_token(email: str, role: str, secret: str, *, ttl: timedelta = DEFAULT_TTL) -> str:
    if role not in ROLE_RANK:
        raise ValueError(f"unknown role {role!r}")
    now = datetime.now(UTC)
    payload = {"sub": email, "role": role, "iat": now, "exp": now + ttl}
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_token(token: str, secret: str) -> Principal:
    """Raises ``jwt.PyJWTError`` (expired, malformed, bad signature, ...) on any failure."""
    payload = jwt.decode(token, secret, algorithms=[ALGORITHM])
    role = payload.get("role")
    email = payload.get("sub")
    if role not in ROLE_RANK or not isinstance(email, str):
        raise jwt.InvalidTokenError("token is missing a valid sub/role")
    return Principal(email=email, role=role)
