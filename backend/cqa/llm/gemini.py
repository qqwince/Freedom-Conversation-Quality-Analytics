"""Провайдер Google Gemini (google-genai) со структурированным JSON-ответом."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Sequence

from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from cqa.llm.base import ChatTurn, LLMError, LLMResult

log = logging.getLogger(__name__)

_RETRYABLE_CODES = {408, 429, 500, 502, 503, 504}


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str, temperature: float = 0.0, timeout_sec: float = 180,
                 max_retries: int = 4, fallback_models: Sequence[str] = ()) -> None:
        self.model = model
        self._models = [model, *(m for m in fallback_models if m != model)]
        self._temperature = temperature
        self._max_retries = max_retries
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=int(timeout_sec * 1000)),
        )

    def generate_json(self, system: str, turns: list[ChatTurn], schema: type[BaseModel]) -> LLMResult:
        """Пробует основную модель с повторами; при перегрузке переходит к запасным.

        В LLMResult.model записывается модель, которая реально ответила.
        """
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=self._temperature,
            response_mime_type="application/json",
            response_schema=schema,
        )
        contents = [types.Content(role=t.role, parts=[types.Part(text=t.text)]) for t in turns]

        last_error: LLMError | None = None
        for model in self._models:
            try:
                return self._generate_with_retries(model, contents, config)
            except _Overloaded as exc:
                last_error = LLMError(str(exc))
                log.warning("%s; переход к запасной модели", exc)
        raise last_error or LLMError("нет доступных моделей")

    def _generate_with_retries(self, model: str, contents: list[types.Content],
                               config: types.GenerateContentConfig) -> LLMResult:
        for attempt in range(self._max_retries + 1):
            started = time.monotonic()
            try:
                response = self._client.models.generate_content(model=model, contents=contents, config=config)
            except errors.APIError as exc:
                if exc.code not in _RETRYABLE_CODES:
                    raise LLMError(f"Gemini {model}: {exc.code} {exc.message}") from exc
                if attempt == self._max_retries:
                    raise _Overloaded(f"Gemini {model}: {exc.code} после {attempt + 1} попыток") from exc
                delay = min(60.0, 2**attempt * 3) + random.uniform(0, 1)
                log.warning("Gemini %s: код %s, повтор через %.0f с", model, exc.code, delay)
                time.sleep(delay)
                continue

            if not response.text:
                raise LLMError(f"Gemini {model}: пустой ответ ({_finish_reason(response)})")
            usage = response.usage_metadata
            return LLMResult(
                text=response.text,
                model=model,
                input_tokens=(usage.prompt_token_count or 0) if usage else 0,
                output_tokens=((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0))
                if usage else 0,
                latency_sec=time.monotonic() - started,
            )
        raise AssertionError("unreachable")


class _Overloaded(LLMError):
    """Модель временно недоступна (перегрузка, лимит) — можно попробовать запасную."""


def _finish_reason(response: types.GenerateContentResponse) -> str:
    if response.candidates and response.candidates[0].finish_reason:
        return str(response.candidates[0].finish_reason)
    return "причина неизвестна"
