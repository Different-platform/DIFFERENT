"""Browser-facing CORS behaviour of the API.

A CORS failure is invisible to the person using the UI: the browser reports a
failed fetch with no status, and the UI can only say it "couldn't reach" the
API. These pin the cases that produced exactly that.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.api import main
from services.api.main import allowed_origins


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (
            "http://localhost:5173",
            ["http://localhost:5173", "http://127.0.0.1:5173"],
        ),
        (
            "http://127.0.0.1:5173",
            ["http://127.0.0.1:5173", "http://localhost:5173"],
        ),
        ("https://differ.example.com", ["https://differ.example.com"]),
    ],
)
def test_loopback_twin_is_allowed_but_real_hosts_are_not_widened(configured, expected):
    assert allowed_origins(configured) == expected


@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://127.0.0.1:5173"])
def test_ui_can_call_the_api_from_either_loopback_address(origin):
    client = TestClient(main.app)
    response = client.options(
        "/api/chat",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


def test_a_rate_limited_response_still_carries_cors_headers():
    """Without them the browser hides the 429 and shows 'failed to fetch'.

    CORS is registered last, so it is outermost and wraps the rate limiter.
    """
    client = TestClient(main.app)
    headers = {
        "Origin": "http://localhost:5173",
        # A private session id keeps this test's counter apart from any other.
        "X-Session-Id": "cors-test-session",
    }
    status = None
    for _ in range(main.settings.rate_limit_max + 2):
        response = client.post("/api/chat", json={}, headers=headers)
        status = response.status_code
        if status == 429:
            break
    assert status == 429
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
