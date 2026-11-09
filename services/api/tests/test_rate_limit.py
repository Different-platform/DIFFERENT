"""Rate limiter tests (Requirements 18.1, 18.2, 18.3), including Property 14."""

from __future__ import annotations

import pytest
from hypothesis import given, settings as hyp_settings
from hypothesis import strategies as st

from services.api.middleware.rate_limit import RateLimiter


def test_allows_up_to_the_limit_then_rejects():
    limiter = RateLimiter(limit=3, window_seconds=60)
    assert [limiter.check("u", now=0.0).allowed for _ in range(3)] == [True] * 3
    assert limiter.check("u", now=0.0).allowed is False


def test_remaining_counts_down():
    limiter = RateLimiter(limit=3, window_seconds=60)
    assert limiter.check("u", now=0.0).remaining == 2
    assert limiter.check("u", now=0.0).remaining == 1
    assert limiter.check("u", now=0.0).remaining == 0


def test_window_slides():
    limiter = RateLimiter(limit=2, window_seconds=10)
    assert limiter.check("u", now=0.0).allowed is True
    assert limiter.check("u", now=1.0).allowed is True
    assert limiter.check("u", now=2.0).allowed is False
    # Once the first two hits age out, capacity returns.
    assert limiter.check("u", now=11.5).allowed is True


def test_identities_are_independent():
    """Req 18.3: one researcher's usage must not reduce another's quota."""
    limiter = RateLimiter(limit=2, window_seconds=60)
    assert limiter.check("a", now=0.0).allowed is True
    assert limiter.check("a", now=0.0).allowed is True
    assert limiter.check("a", now=0.0).allowed is False
    # b is untouched by a hitting its ceiling.
    assert limiter.check("b", now=0.0).allowed is True
    assert limiter.check("b", now=0.0).allowed is True


def test_rejected_attempts_do_not_extend_the_lockout():
    """Hammering after a 429 must not push the reset further out."""
    limiter = RateLimiter(limit=1, window_seconds=10)
    assert limiter.check("u", now=0.0).allowed is True
    for moment in (1.0, 2.0, 3.0):
        assert limiter.check("u", now=moment).allowed is False
    # The single recorded hit ages out on schedule.
    assert limiter.check("u", now=10.5).allowed is True


def test_reset_after_seconds_is_reported_on_rejection():
    limiter = RateLimiter(limit=1, window_seconds=60)
    limiter.check("u", now=0.0)
    rejected = limiter.check("u", now=10.0)
    assert rejected.allowed is False
    assert 0 < rejected.reset_after_seconds <= 61


def test_reset_clears_state():
    limiter = RateLimiter(limit=1, window_seconds=60)
    limiter.check("u", now=0.0)
    limiter.reset("u")
    assert limiter.check("u", now=0.0).allowed is True


@pytest.mark.parametrize(("limit", "window"), [(0, 60), (1, 0), (-1, 60)])
def test_invalid_configuration_is_rejected(limit: int, window: int):
    with pytest.raises(ValueError):
        RateLimiter(limit=limit, window_seconds=window)


# ---------------------------------------------------------------------------
# Property 14: Rate limiter correctness
# ---------------------------------------------------------------------------
@given(
    limit=st.integers(min_value=1, max_value=10),
    request_count=st.integers(min_value=0, max_value=25),
)
@hyp_settings(max_examples=150)
def test_property_14_within_limit_accepted_beyond_rejected(
    limit: int, request_count: int
):
    """Feature: differ-platform, Property 14: Rate limiter correctness.

    Requests within the limit for their window are accepted; requests beyond
    the limit within the same window are rejected.
    """
    limiter = RateLimiter(limit=limit, window_seconds=3600)
    outcomes = [
        limiter.check("u", now=0.0).allowed for _ in range(request_count)
    ]
    assert outcomes == [i < limit for i in range(request_count)]


@given(
    limit=st.integers(min_value=1, max_value=6),
    pattern=st.lists(st.booleans(), min_size=0, max_size=20),
)
@hyp_settings(max_examples=150)
def test_property_14_interleaving_users_does_not_change_outcomes(
    limit: int, pattern: list[bool]
):
    """Feature: differ-platform, Property 14: Rate limiter correctness.

    For any two distinct users with independent request sequences,
    interleaving one user's traffic with the other's must never change either
    user's individual accept/reject outcomes.
    """
    interleaved = RateLimiter(limit=limit, window_seconds=3600)
    a_interleaved: list[bool] = []
    b_interleaved: list[bool] = []
    for goes_to_a in pattern:
        if goes_to_a:
            a_interleaved.append(interleaved.check("a", now=0.0).allowed)
        else:
            b_interleaved.append(interleaved.check("b", now=0.0).allowed)

    a_alone = RateLimiter(limit=limit, window_seconds=3600)
    b_alone = RateLimiter(limit=limit, window_seconds=3600)
    a_isolated = [
        a_alone.check("a", now=0.0).allowed for _ in range(len(a_interleaved))
    ]
    b_isolated = [
        b_alone.check("b", now=0.0).allowed for _ in range(len(b_interleaved))
    ]

    assert a_interleaved == a_isolated
    assert b_interleaved == b_isolated
