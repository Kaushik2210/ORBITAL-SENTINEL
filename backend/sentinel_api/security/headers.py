"""Security response headers. The Swagger/ReDoc docs pages load their JS/CSS from a CDN, so a
strict CSP would break them; those paths are exempted rather than weakening the policy for the
actual API responses, which are JSON and never render as a page.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")

_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}
_CSP = "default-src 'none'; frame-ancestors 'none'"


async def security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    for name, value in _HEADERS.items():
        response.headers[name] = value
    if not request.url.path.startswith(_DOCS_PATHS):
        response.headers["Content-Security-Policy"] = _CSP
    return response
