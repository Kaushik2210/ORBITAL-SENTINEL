"""FastAPI auth dependencies: extract and validate a bearer token, then enforce a minimum role.

``PUBLIC_DEMO_MODE`` (see ``.env.example``) only ever relaxes a *read* (``GET``) request that
needs no more than the ``viewer`` role — anything that starts a session, calls the agent, or
touches the audit log always needs a real, valid token, demo mode or not.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Mapping
from typing import Any, Protocol

import jwt
from fastapi import HTTPException, Request

from .tokens import ROLE_RANK, Principal, decode_token


class _HasAuth(Protocol):
    @property
    def headers(self) -> Mapping[str, str]: ...
    @property
    def query_params(self) -> Mapping[str, str]: ...


def _bearer_token(conn: _HasAuth) -> str | None:
    auth = conn.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[len("bearer ") :].strip() or None
    # WebSocket/SSE clients in a browser cannot always set headers; accept a query param too.
    return conn.query_params.get("token")


def principal_from(conn: _HasAuth, jwt_secret: str) -> Principal | None:
    """Shared by the REST dependency below and the WebSocket/SSE handlers, which have no
    ``Depends`` support the way plain HTTP routes do."""
    token = _bearer_token(conn)
    if not token:
        return None
    try:
        return decode_token(token, jwt_secret)
    except jwt.PyJWTError:
        return None


async def optional_principal(request: Request) -> Principal | None:
    settings = request.app.state.sentinel.settings
    return principal_from(request, settings.jwt_secret)


def require_role(minimum: str) -> Callable[[Request], Coroutine[Any, Any, Principal]]:
    if minimum not in ROLE_RANK:
        raise ValueError(f"unknown role {minimum!r}")

    async def dependency(request: Request) -> Principal:
        principal = await optional_principal(request)
        settings = request.app.state.sentinel.settings
        if principal is None:
            is_demo_read = (
                settings.public_demo_mode and request.method == "GET" and minimum == "viewer"
            )
            if is_demo_read:
                return Principal(email="anonymous", role="viewer")
            raise HTTPException(401, "authentication required")
        if ROLE_RANK[principal.role] < ROLE_RANK[minimum]:
            raise HTTPException(403, f"requires the {minimum!r} role or higher")
        return principal

    return dependency
