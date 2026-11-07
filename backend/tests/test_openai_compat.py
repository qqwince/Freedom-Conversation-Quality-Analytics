"""Провайдер OpenAI-совместимого API (DeepSeek) на подставном HTTP."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from cqa.llm import ChatTurn, LLMError, openai_compat, provider_family
from cqa.llm.openai_compat import OpenAICompatibleProvider
from cqa.models import LLMEvaluation


def reply(status: int, content: str = '{"results": []}', model: str = "deepseek-flash") -> httpx.Response:
    body: dict[str, Any] = (
        {"model": model, "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 1200, "completion_tokens": 300}}
        if status == 200 else {"error": {"message": "busy"}}
    )
    return httpx.Response(status, json=body, request=httpx.Request("POST", "https://api.test"))


@pytest.fixture
def calls(monkeypatch):
    log: list[dict[str, Any]] = []
    queue: list[httpx.Response] = []

    def fake_post(url, headers, json, timeout):
        log.append({"url": url, "headers": headers, "json": json})
        return queue.pop(0)

    monkeypatch.setattr(openai_compat.httpx, "post", fake_post)
    monkeypatch.setattr(openai_compat.time, "sleep", lambda s: None)
    return log, queue


def provider(**kw: Any) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(name="deepseek", base_url="https://api.test/", api_key="k",
                                    model="deepseek-flash", **kw)


def test_request_format_and_usage(calls):
    log, queue = calls
    queue.append(reply(200))
    result = provider().generate_json("system", [ChatTurn("user", "u"), ChatTurn("model", "m"),
                                                 ChatTurn("user", "fix")], LLMEvaluation)
    sent = log[0]
    assert sent["url"] == "https://api.test/chat/completions"
    assert sent["headers"]["Authorization"] == "Bearer k"
    assert sent["json"]["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in sent["json"]["messages"]] == ["system", "user", "assistant", "user"]
    schema = sent["json"]["messages"][0]["content"].split("\n", 3)[-1]
    assert json.loads(schema)["title"] == "LLMEvaluation"
    assert (result.input_tokens, result.output_tokens, result.model) == (1200, 300, "deepseek-flash")


def test_retries_then_succeeds(calls):
    _, queue = calls
    queue += [reply(503), reply(429), reply(200)]
    assert provider().generate_json("s", [ChatTurn("user", "u")], LLMEvaluation).text == '{"results": []}'


def test_falls_back_to_next_model(calls):
    log, queue = calls
    queue += [reply(503), reply(503), reply(200, model="deepseek-v4-pro")]
    result = provider(max_retries=1, fallback_models=["deepseek-v4-pro"]).generate_json(
        "s", [ChatTurn("user", "u")], LLMEvaluation)
    assert [c["json"]["model"] for c in log] == ["deepseek-flash", "deepseek-flash", "deepseek-v4-pro"]
    assert result.model == "deepseek-v4-pro"


def test_non_retryable_error(calls):
    _, queue = calls
    queue.append(reply(401))
    with pytest.raises(LLMError, match="401"):
        provider().generate_json("s", [ChatTurn("user", "u")], LLMEvaluation)


def test_empty_answer_is_error(calls):
    _, queue = calls
    queue.append(reply(200, content=""))
    with pytest.raises(LLMError, match="пустой ответ"):
        provider().generate_json("s", [ChatTurn("user", "u")], LLMEvaluation)


def test_provider_family():
    assert provider_family("deepseek-flash") == "deepseek"
    assert provider_family("gemini-3.5-flash") == "gemini"
    assert provider_family("gemma-4-31b-it") == "gemini"
