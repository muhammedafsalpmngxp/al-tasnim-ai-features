"""Retry behaviour for transient LLM provider failures.

Measured against the live Groq endpoint: a 429 (rate limited) and,
intermittently, a 413 both occurred for a request that succeeded moments
later with the identical payload. These tests assert the client retries a
transient failure before giving up, and never retries a failure that is not
transient (e.g. a permanent client error), so a genuinely broken request still
fails fast rather than being retried pointlessly.
"""

from __future__ import annotations

import httpx
import pytest

from app.config.settings import get_settings
from app.services import llm_service as llm_service_module
from app.services.llm_service import LLMService, LLMUnavailable

EVIDENCE = {"scope": "day", "summary": {"task_count": 1}}


@pytest.fixture(autouse=True)
def _configured_llm(monkeypatch):
    """A minimal, valid LLM configuration so explain() reaches the HTTP call."""
    get_settings.cache_clear()
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.invalid/v1")
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Retries must not actually slow the test suite down."""
    monkeypatch.setattr(llm_service_module.time, "sleep", lambda seconds: None)


def _ok_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": "All tasks are on plan."}}]},
        request=httpx.Request("POST", "https://example.invalid/v1/chat/completions"),
    )


def _error_response(status_code: int, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status_code,
        json={"error": "provider says no"},
        headers=headers or {},
        request=httpx.Request("POST", "https://example.invalid/v1/chat/completions"),
    )


class TestTransientFailureIsRetried:
    def test_a_single_429_then_success_still_returns_the_explanation(self, monkeypatch):
        responses = [_error_response(429), _ok_response()]
        calls = []

        def fake_post(url, *, headers, json, timeout):
            calls.append(1)
            return responses.pop(0)

        monkeypatch.setattr(httpx, "post", fake_post)
        text, model = LLMService().explain(EVIDENCE)

        assert text == "All tasks are on plan."
        assert model == "test-model"
        assert len(calls) == 2

    def test_413_is_retried_the_same_way_as_429(self, monkeypatch):
        responses = [_error_response(413), _ok_response()]
        monkeypatch.setattr(httpx, "post", lambda *a, **k: responses.pop(0))

        text, _ = LLMService().explain(EVIDENCE)
        assert text == "All tasks are on plan."

    def test_retry_after_header_caps_the_wait(self, monkeypatch):
        responses = [_error_response(429, headers={"Retry-After": "9999"}), _ok_response()]
        sleeps = []
        monkeypatch.setattr(llm_service_module.time, "sleep", lambda seconds: sleeps.append(seconds))
        monkeypatch.setattr(httpx, "post", lambda *a, **k: responses.pop(0))

        LLMService().explain(EVIDENCE)
        assert sleeps == [llm_service_module._MAX_RETRY_DELAY_SECONDS]

    def test_every_attempt_failing_still_raises_after_the_retry_budget(self, monkeypatch):
        calls = []

        def fake_post(*args, **kwargs):
            calls.append(1)
            return _error_response(429)

        monkeypatch.setattr(httpx, "post", fake_post)

        with pytest.raises(LLMUnavailable, match="HTTP 429"):
            LLMService().explain(EVIDENCE)
        assert len(calls) == llm_service_module._MAX_ATTEMPTS


class TestNonTransientFailureIsNotRetried:
    def test_a_permanent_client_error_fails_on_the_first_attempt(self, monkeypatch):
        calls = []

        def fake_post(*args, **kwargs):
            calls.append(1)
            return _error_response(401)

        monkeypatch.setattr(httpx, "post", fake_post)

        with pytest.raises(LLMUnavailable, match="HTTP 401"):
            LLMService().explain(EVIDENCE)
        assert len(calls) == 1

    def test_a_transport_error_is_never_retried(self, monkeypatch):
        calls = []

        def fake_post(*args, **kwargs):
            calls.append(1)
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(httpx, "post", fake_post)

        with pytest.raises(LLMUnavailable, match="could not be reached"):
            LLMService().explain(EVIDENCE)
        assert len(calls) == 1
