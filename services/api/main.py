"""FastAPI application (Requirements 13.1, 13.2, 16.3).

Run from the repo root:
    .venv/bin/uvicorn services.api.main:app --reload --port 8000

The embedding model is loaded once at startup and held for the process
lifetime. That is the constraint that rules out serverless hosting: ~420 MB of
weights must be resident to embed a question with the same model that built
the index, and a cold start per request would make the product unusable.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from services.api.api.routes import chat, health, variables, years
from services.api.config import settings
from services.api.middleware.rate_limit import RateLimiter, RateLimitMiddleware
from services.api.middleware.timing import TimingMiddleware
from services.api.orchestrator import Orchestrator
from services.api.store import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger("differ")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from jobs.ingest.embedder import Embedder
    from services.api.llm.gateway import build_gateway

    db.open_pool()

    # Materialize the model now rather than on the first request, so startup
    # pays the cost and the first researcher does not (Requirement 13.2).
    embedder = Embedder()
    logger.info("loading embedding model %s ...", settings.embedding_model)
    embedder.model
    logger.info("embedding model resident")

    gateway = build_gateway()
    if not settings.has_real_llm:
        logger.warning(
            "No GROQ_API_KEY configured — using the stub gateway. Routing, "
            "judging and wording are keyword rules, NOT model output. "
            "Retrieved variables, citations and sentinel flags are still real."
        )

    # No authentication is configured. Deliberate for this version
    # (Requirement 20.2), but it means every endpoint is open to anyone who
    # can reach the process. Do not expose this to the internet as-is;
    # the Vercel proxy and a shared secret land with Task 16.2.
    logger.warning(
        "API has no authentication. Bind to localhost or place it behind a "
        "trusted proxy before exposing it."
    )

    app.state.orchestrator = Orchestrator(gateway=gateway, embedder=embedder)
    app.state.embedder = embedder
    app.state.gateway = gateway

    try:
        yield
    finally:
        db.close_pool()


app = FastAPI(
    title="DIFFER API",
    description=(
        "NHIS research assistant: codebook question answering with citations. "
        "Verified descriptive statistics are a later phase."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

rate_limiter = RateLimiter(
    limit=settings.rate_limit_max,
    window_seconds=settings.rate_limit_window_seconds,
)

# Starlette applies middleware in reverse registration order, so the last
# registered runs outermost. Timing is registered last to wrap everything,
# and rate limiting sits ahead of the orchestrator so a rejected request
# costs no provider quota (Requirement 18.2).
app.add_middleware(
    RateLimitMiddleware,
    limiter=rate_limiter,
    protected_paths=("/api/chat",),
)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(TimingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    # The browser cannot read these unless they are explicitly exposed, and
    # the UI's rate-limit indicator depends on them.
    expose_headers=[
        "X-Request-ID",
        "X-Response-Time-Ms",
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "X-RateLimit-Reset",
    ],
)

app.include_router(health.router)
app.include_router(chat.router)
app.include_router(variables.router)
app.include_router(years.router)


@app.get("/")
async def root() -> dict:
    return {
        "service": "DIFFER API",
        "docs": "/docs",
        "health": "/api/health",
    }
