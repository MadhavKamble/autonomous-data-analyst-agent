"""Unit tests for the per-IP rate limiter (app/api/rate_limit.py). Pure and
fast — no HTTP, no database; the limiter's window logic is tested in
isolation using a fake clock."""

from __future__ import annotations

from app.api.rate_limit import FixedWindowRateLimiter


def test_allows_up_to_the_limit_then_blocks() -> None:
    limiter = FixedWindowRateLimiter(max_requests=3, window_seconds=60)
    assert [limiter.allow("1.2.3.4") for _ in range(3)] == [True, True, True]
    assert limiter.allow("1.2.3.4") is False


def test_different_ips_have_independent_budgets() -> None:
    limiter = FixedWindowRateLimiter(max_requests=1, window_seconds=60)
    assert limiter.allow("1.1.1.1") is True
    assert limiter.allow("1.1.1.1") is False
    assert limiter.allow("2.2.2.2") is True  # unaffected by 1.1.1.1's budget


def test_requests_age_out_of_the_window(monkeypatch) -> None:
    limiter = FixedWindowRateLimiter(max_requests=1, window_seconds=10)
    fake_now = [1000.0]
    monkeypatch.setattr("app.api.rate_limit.time.monotonic", lambda: fake_now[0])

    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is False  # still within the window

    fake_now[0] += 10.1  # window has elapsed
    assert limiter.allow("1.2.3.4") is True
