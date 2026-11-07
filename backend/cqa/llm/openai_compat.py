"""Провайдер для API в формате OpenAI Chat Completions: DeepSeek, а также Kimi, OpenRouter, Ollama.

Структура ответа задаётся JSON-режимом (response_format=json_object) и схемой в системном
промпте; соответствие схеме затем проверяет pydantic в evaluator.
"""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Sequence

import httpx
from pydantic import BaseModel

from cqa.llm.base import ChatTurn, LLMError, LLMResult

log = logging.getLogger(__name__)

_RETRYABLE_CODES = {408, 409, 429, 500, 502, 503, 504}


class OpenAICompatibleProvider:
    def __init__(self, name: str, base_url: str, api_key: str, model: str, temperature: float = 0.0,
                 timeout_sec: float = 180, max_retries: int = 4, fallback_models: Sequence[str] = (),
                 max_output_tokens: int = 16384) -> None:
        self.name = name
        self.model = model
        self._models = [model, *(m for m in fallback_models if m != model)]
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._temperature = temperature
        self._timeout = timeout_sec
        self._max_retries = max_retries
        self._max_output_tokens = max_output_tokens

    def generate_json(self, system: str, turns: list[ChatTurn], schema: type[BaseModel]) -> LLMResult:
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
        messages = [{"role": "system", "content": f"{system}\n\nВерни только JSON-объект строго по этой "
                                                   f"JSON Schema, без пояснений вокруг:\n{schema_json}"}]
        messages += [{"role": "assistant" if t.role == "model" else "user", "content": t.text} for t in turns]

        last_error: LLMError | None = None
        for model in self._models:
            try:
                return self._request(model, messages)
            except _Overloaded as exc:
                last_error = LLMError(str(exc))
                log.warning("%s; переход к запасной модели", exc)
        raise last_error or LLMError("нет доступных моделей")

    def _request(self, model: str, messages: list[dict[str, str]]) -> LLMResult:
        body = {
            "model": model,
            "messages": messages,
            "temperature": self._temperature,
            "response_format": {"type": "json_object"},
            "max_tokens": self._max_output_tokens,
        }
        for attempt in range(self._max_retries + 1):
            started = time.monotonic()
            try:
                response = httpx.post(self._url, headers=self._headers, json=body, timeout=self._timeout)
            except httpx.TransportError as exc:
                problem = f"сеть: {exc.__class__.__name__}"
            else:
                if response.status_code == 200:
                    return self._parse(model, response, time.monotonic() - started)
                if response.status_code not in _RETRYABLE_CODES:
                    raise LLMError(f"{self.name} {model}: {response.status_code} {_error_text(response)}")
                problem = f"код {response.status_code}"
            if attempt == self._max_retries:
                raise _Overloaded(f"{self.name} {model}: {problem} после {attempt + 1} попыток")
            delay = min(60.0, 2**attempt * 3) + random.uniform(0, 1)
            log.warning("%s %s: %s, повтор через %.0f с", self.name, model, problem, delay)
            time.sleep(delay)
        raise AssertionError("unreachable")

    def _parse(self, model: str, response: httpx.Response, latency: float) -> LLMResult:
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        if not text.strip():
            raise LLMError(f"{self.name} {model}: пустой ответ ({choice.get('finish_reason')})")
        usage = data.get("usage") or {}
        return LLMResult(
            text=text,
            model=data.get("model") or model,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            latency_sec=latency,
        )


class _Overloaded(LLMError):
    """Сервис временно недоступен — можно попробовать запасную модель."""


def _error_text(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error", {}).get("message") or response.text)[:300]
    except ValueError:
        return response.text[:300]
