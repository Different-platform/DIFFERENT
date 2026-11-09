"""FastAPI application (Requirements 13.1, 13.2, 16.3).

Run from the repo root:
    .venv/bin/uvicorn services.api.main:app --reload --port 8100

Port 8100, not 8000: 8000 is commonly taken by other local tools (a Chroma
container was answering there), and a browser resolving "localhost" reaches
the IPv6 listener first.

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
from services.api.middleware.shared_secret import SharedSecretMiddleware
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

    # There is no user login in this version (Requirement 20.2). The shared
    # secret is what keeps a deployed API from being called directly.
    if settings.api_shared_secret.strip():
        logger.info("/api/chat requires X-Api-Secret (set by the web app's proxy)")
    else:
        logger.warning(
            "API_SHARED_SECRET is not set: /api/chat is open to anyone who can "
            "reach this process. Fine on localhost; set it before deploying."
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
        "NHIS research assistant: codebook question answering with citations, "
        "and verified, population-weighted descriptive statistics."
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
app.add_middleware(
    SharedSecretMiddleware,
    secret=settings.api_shared_secret,
    protected_paths=("/api/chat",),
)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(TimingMiddleware)


def allowed_origins(configured: str) -> list[str]:
    """The UI origins allowed to call the API, from FRONTEND_ORIGIN.

    Accepts a comma-separated list, because a deployed UI is reachable at
    more than one origin (the production domain plus the platform's own URL).
    Each loopback origin also gets its twin: http://localhost:5173 and
    http://127.0.0.1:5173 are different origins to a browser, and opening the
    UI by the "wrong" one fails with a bare "failed to fetch" and no hint why.
    A trailing slash is dropped, since a browser's Origin header never has one.
    """
    origins: list[str] = []
    for raw in configured.split(","):
        origin = raw.strip().rstrip("/")
        if not origin:
            continue
        candidates = [origin]
        for here, there in (("//localhost", "//127.0.0.1"), ("//127.0.0.1", "//localhost")):
            if here in origin:
                candidates.append(origin.replace(here, there))
        for candidate in candidates:
            if candidate not in origins:
                origins.append(candidate)
    return origins


app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(settings.frontend_origin),
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
