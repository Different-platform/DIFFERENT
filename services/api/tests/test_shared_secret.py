"""The shared-secret gate that keeps a deployed API from being called directly."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from services.api.middleware.shared_secret import SECRET_HEADER, SharedSecretMiddleware

ORIGIN = "https://differ.example.com"


def make_app(secret: str) -> TestClient:
    """A minimal app with the same middleware order as main.py (CORS outermost)."""
    app = FastAPI()

    @app.post("/api/chat")
    async def chat():
        return {"ok": True}

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    app.add_middleware(SharedSecretMiddleware, secret=secret, protected_paths=("/api/chat",))
    app.add_middleware(
        CORSMiddleware, allow_origins=[ORIGIN], allow_methods=["GET", "POST"], allow_headers=["*"]
    )
    return TestClient(app)


def test_chat_without_the_secret_is_rejected():
    response = make_app("s3cret").post("/api/chat")
    assert response.status_code == 401
    assert "DIFFER web app" in response.json()["detail"]


def test_chat_with_a_wrong_secret_is_rejected():
    response = make_app("s3cret").post("/api/chat", headers={SECRET_HEADER: "guess"})
    assert response.status_code == 401


def test_chat_with_the_secret_goes_through():
    response = make_app("s3cret").post("/api/chat", headers={SECRET_HEADER: "s3cret"})
    assert response.status_code == 200 and response.json() == {"ok": True}


def test_health_stays_open_for_the_platform_health_check():
    assert make_app("s3cret").get("/api/health").status_code == 200


def test_gate_is_off_when_no_secret_is_configured():
    """Local dev and the browser-direct UI keep working with no secret set."""
    assert make_app("").post("/api/chat").status_code == 200
    assert make_app("   ").post("/api/chat").status_code == 200


def test_preflight_is_not_blocked():
    response = make_app("s3cret").options(
        "/api/chat",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-api-secret",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN


def test_a_rejection_still_carries_cors_headers():
    """Otherwise the browser hides the 401 behind a bare 'failed to fetch'."""
    response = make_app("s3cret").post("/api/chat", headers={"Origin": ORIGIN})
    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == ORIGIN
