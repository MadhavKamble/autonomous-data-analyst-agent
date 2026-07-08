"""Local Ollama chat client — the offline/fallback LLM backend."""

from __future__ import annotations

import httpx

from app.llm.client import LLMBackendError, LLMResponse


class OllamaLLMClient:
    def __init__(self, base_url: str, model: str, timeout: float = 120.0) -> None:
        # Generous timeout: a 3B model on CPU can take tens of seconds for a
        # few hundred tokens. Fine for a dev/fallback path.
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

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
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if json_mode:
            # Constrains decoding to valid JSON server-side — much stronger
            # than asking nicely in the prompt (we still do both).
            payload["format"] = "json"

        try:
            response = httpx.post(
                f"{self._base_url}/api/chat", json=payload, timeout=self._timeout
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMBackendError(f"Ollama request failed: {exc}") from exc

        data = response.json()
        return LLMResponse(
            text=data["message"]["content"],
            model=self._model,
            prompt_tokens=data.get("prompt_eval_count", 0),
            completion_tokens=data.get("eval_count", 0),
        )
