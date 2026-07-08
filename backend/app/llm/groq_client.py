"""Groq chat client — the primary LLM backend (llama-3.3-70b-versatile).

Free-tier reality this wrapper is built around: ~30 requests/min and ~12K
tokens/min at the org level, 1,000 requests/day; whichever limit trips first
returns HTTP 429. Policy:

- Exponential backoff with jitter on 429 and 5xx, honoring a Retry-After
  header when Groq sends one. Retries happen inside the synchronous request
  (there are no background workers), so total waiting is bounded: if Groq
  asks us to wait longer than max_retry_after_seconds, or retries are
  exhausted, we raise LLMBackendError — which the pipeline turns into its
  clean "backend is busy" user-facing failure. Never a silent hang, never a
  stack trace to the frontend.
- Non-retryable statuses (401 bad key, 400 bad request, 404 bad model) fail
  immediately with the reason — retrying can't fix a wrong API key.
- Token accounting: every response's usage is parsed into LLMResponse and
  logged per call, so budget discussions are grounded in real numbers.

The httpx transport and the sleep function are injectable so tests exercise
the real retry loop with a mocked endpoint and zero actual waiting.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Callable

import httpx

from app.llm.client import LLMBackendError, LLMResponse

logger = logging.getLogger(__name__)

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class GroqLLMClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        max_retries: int = 4,
        backoff_base_seconds: float = 2.0,
        max_retry_after_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._model = model
        self._max_retries = max_retries
        self._backoff_base = backoff_base_seconds
        self._max_retry_after = max_retry_after_seconds
        self._sleep = sleep
        self._client = httpx.Client(
            timeout=timeout,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    def complete(
        self,
        *,
        system: str,
        user: str,
        json_mode: bool = True,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> LLMResponse:
        payload: dict = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_completion_tokens": max_tokens,
        }
        if json_mode:
            # Server-side JSON enforcement (Groq requires the word "JSON" in
            # the prompt for this mode — every agent prompt instructs JSON).
            payload["response_format"] = {"type": "json_object"}

        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.post(GROQ_CHAT_URL, json=payload)
            except httpx.HTTPError as error:
                # Network-level failure: retryable the same way a 5xx is.
                if attempt == self._max_retries:
                    raise LLMBackendError(f"Groq unreachable: {error}") from error
                self._wait(attempt, retry_after=None, reason=f"network error: {error}")
                continue

            if response.status_code == 200:
                return self._parse(response)

            if response.status_code in RETRYABLE_STATUSES:
                if attempt == self._max_retries:
                    raise LLMBackendError(
                        f"Groq still returning {response.status_code} after "
                        f"{self._max_retries} retries (rate limit or outage)"
                    )
                self._wait(
                    attempt,
                    retry_after=_retry_after_seconds(response),
                    reason=f"HTTP {response.status_code}",
                )
                continue

            # 401/403/400/404/413…: retrying cannot help; fail with the cause.
            raise LLMBackendError(
                f"Groq error {response.status_code}: {response.text[:300]}"
            )

        raise LLMBackendError("unreachable")  # loop always returns or raises

    def _wait(self, attempt: int, retry_after: float | None, reason: str) -> None:
        """Sleep before retry `attempt + 1`. Groq's own Retry-After wins when
        present (it knows its limits); otherwise exponential backoff with
        jitter to avoid thundering-herd on shared org limits."""
        if retry_after is not None:
            if retry_after > self._max_retry_after:
                # Waiting e.g. 20 minutes for a daily-limit reset inside a
                # synchronous HTTP request would just hang the user; fail
                # fast with the honest reason instead.
                raise LLMBackendError(
                    f"Groq rate limit requires waiting {retry_after:.0f}s — "
                    "beyond what a single request can absorb"
                )
            delay = retry_after
        else:
            delay = self._backoff_base * (2**attempt) + random.uniform(0, 1)
        logger.warning("Groq busy (%s); retrying in %.1fs (attempt %d)", reason, delay, attempt + 1)
        self._sleep(delay)

    def _parse(self, response: httpx.Response) -> LLMResponse:
        data = response.json()
        usage = data.get("usage", {})
        result = LLMResponse(
            text=data["choices"][0]["message"]["content"],
            model=data.get("model", self._model),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )
        logger.info(
            "groq call: prompt=%d completion=%d total=%d tokens",
            result.prompt_tokens,
            result.completion_tokens,
            result.prompt_tokens + result.completion_tokens,
        )
        return result


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None  # HTTP-date form — rare from Groq; fall back to backoff
