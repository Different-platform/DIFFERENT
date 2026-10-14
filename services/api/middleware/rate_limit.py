"""Per-researcher rate limiting (Requirements 18.1, 18.2, 18.3).

Protects LLM spend. A rejected request never reaches the orchestrator, so it
costs nothing in provider quota.

Identity, given there is no authentication in this version: the frontend mints
a UUID into localStorage and sends it as `X-Session-Id`, and the limiter keys
on that, falling back to the client IP when the header is absent. The session
id is trivially forgeable — it is a cost-control measure among 30-40 trusted
researchers, not an access control, and it must not be mistaken for one. A
shared university NAT is exactly why IP alone is the fallback rather than the
primary key: it would throttle a whole department collectively.

State is in-process. With one container (Requirement 19) that is correct; the
moment a second replica exists the limit becomes per-replica and this needs a
shared store.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

SESSION_HEADER = "X-Session-Id"
LIMIT_HEADER = "X-RateLimit-Limit"
REMAINING_HEADER = "X-RateLimit-Remaining"
RESET_HEADER = "X-RateLimit-Reset"


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_after_seconds: int


class RateLimiter:
    """Sliding-window counter with independent state per identity."""

    def __init__(self, limit: int, window_seconds: int):
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if window_seconds < 1:
            raise ValueError("window_seconds must be at least 1")

        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, hits: deque[float], now: float) -> None:
        cutoff = now - self.window_seconds
        while hits and hits[0] <= cutoff:
            hits.popleft()

    def check(self, identity: str, now: float | None = None) -> RateLimitResult:
        """Record an attempt and report whether it is allowed.

        Rejected attempts are not recorded, so a client that keeps hammering
        after a 429 does not extend its own lockout indefinitely.
        """
        moment = time.monotonic() if now is None else now

        with self._lock:
            hits = self._hits[identity]
            self._prune(hits, moment)

            if len(hits) >= self.limit:
                oldest = hits[0]
                reset_after = max(0, int(self.window_seconds - (moment - oldest)) + 1)
                return RateLimitResult(
                    allowed=False,
                    limit=self.limit,
                    remaining=0,
                    reset_after_seconds=reset_after,
                )

            hits.append(moment)
            return RateLimitResult(
                allowed=True,
                limit=self.limit,
                remaining=self.limit - len(hits),
                reset_after_seconds=self.window_seconds,
            )

    def reset(self, identity: str | None = None) -> None:
        with self._lock:
            if identity is None:
                self._hits.clear()
            else:
                self._hits.pop(identity, None)


def identify(request: Request) -> str:
    session_id = request.headers.get(SESSION_HEADER, "").strip()
    if session_id:
        return f"session:{session_id[:64]}"
    client = request.client.host if request.client else "unknown"
    return f"ip:{client}"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Applies the limit to the expensive endpoints only.

    A health check or a variable lookup costs no provider quota, so limiting
    them would only make the UI feel broken without saving anything.
    """

    def __init__(self, app, limiter: RateLimiter, protected_paths: tuple[str, ...]):
        super().__init__(app)
        self.limiter = limiter
        self.protected_paths = protected_paths

    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith(self.protected_paths):
            return await call_next(request)

        identity = identify(request)
        result = self.limiter.check(identity)

        if not result.allowed:
            logger.warning("rate limit exceeded for %s", identity)
            response = JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        f"Rate limit of {result.limit} requests per "
                        f"{self.limiter.window_seconds // 60} minutes exceeded. "
                        f"Try again in about {result.reset_after_seconds} seconds."
                    ),
                    "limit": result.limit,
                    "reset_after_seconds": result.reset_after_seconds,
                },
            )
        else:
            response = await call_next(request)

        response.headers[LIMIT_HEADER] = str(result.limit)
        response.headers[REMAINING_HEADER] = str(result.remaining)
        response.headers[RESET_HEADER] = str(result.reset_after_seconds)
        return response
