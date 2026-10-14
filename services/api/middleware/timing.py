"""Request timing and correlation ids (Requirement 17.2).

Load-bearing rather than decorative: `query_log.latency_ms` is what any later
performance work reads, and it is the only evidence available for the
single-container concurrency risk in Requirement 19.

The orchestrator measures its own pipeline latency precisely (it knows when
the pipeline starts and ends, which for a stream is not when the HTTP
response closes). This middleware covers the whole request, including the
part before and after the pipeline, and surfaces it on a header for client
and proxy visibility.
"""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
RESPONSE_TIME_HEADER = "X-Response-Time-Ms"


class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:12]
        request.state.request_id = request_id

        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers[RESPONSE_TIME_HEADER] = str(elapsed_ms)

        logger.info(
            "%s %s -> %s in %sms [%s]",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            request_id,
        )
        return response
