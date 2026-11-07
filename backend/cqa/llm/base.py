"""Общий интерфейс провайдера модели."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel


class LLMError(RuntimeError):
    """Модель недоступна или вернула ответ, который нельзя использовать."""


@dataclass(frozen=True)
class ChatTurn:
    role: Literal["user", "model"]
    text: str


@dataclass(frozen=True)
class LLMResult:
    text: str                 # сырой ответ модели (JSON-строка)
    model: str
    input_tokens: int
    output_tokens: int        # включая токены рассуждений, если модель их тратит
    latency_sec: float


class LLMProvider(Protocol):
    name: str
    model: str

    def generate_json(self, system: str, turns: list[ChatTurn], schema: type[BaseModel]) -> LLMResult:
        """Генерирует JSON по схеме. turns — диалог с моделью (для повторного запроса с замечаниями)."""
        ...
