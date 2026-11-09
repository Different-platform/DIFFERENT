"""Shared-secret gate for the expensive endpoints (Requirement 16.2, 16.3).

When the API is deployed, its URL is public. Without a gate, anyone who finds
it can call /api/chat directly, skip the frontend, and spend the LLM quota.
The Next.js server-side proxy sends this secret as `X-Api-Secret`; the browser
never sees it, so a direct browser or script call cannot supply it.

Opt-in: with API_SHARED_SECRET empty the gate is off, which keeps local
development (and the polished Vite UI, which calls the API from the browser
and so cannot hold a secret) working unchanged.

Only the protected paths are gated. /api/health stays open so the platform's
health check can reach it.
"""

from __future__ import annotations

import hmac
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

SECRET_HEADER = "X-Api-Secret"


class SharedSecretMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, secret: str, protected_paths: tuple[str, ...]):
        super().__init__(app)
        self.secret = secret.strip()
        self.protected_paths = protected_paths

    async def dispatch(self, request: Request, call_next):
        if (
            not self.secret
            or request.method == "OPTIONS"  # CORS preflight carries no custom headers
            or not request.url.path.startswith(self.protected_paths)
        ):
            return await call_next(request)

        supplied = request.headers.get(SECRET_HEADER, "")
        # Constant-time comparison, so the secret cannot be recovered from
        # response timing.
        if not hmac.compare_digest(supplied.encode(), self.secret.encode()):
            logger.warning("rejected %s: missing or wrong %s", request.url.path, SECRET_HEADER)
            return JSONResponse(
                status_code=401,
                content={"detail": "This API is only reachable through the DIFFER web app."},
            )
        return await call_next(request)
