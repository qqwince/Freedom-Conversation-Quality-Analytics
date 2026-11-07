"""Общие фикстуры: чек-лист по умолчанию и небольшой диалог по образцу DEV-набора."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from cqa.models import Conversation

ROOT = Path(__file__).resolve().parents[2]

# Сценарий: бот → эскалация → оператор, идентификация, пауза, закрытие чата.
CHAT = [
    ("00:00", "client", "В портфеле не вижу бумагу. Сделок не делал."),
    ("00:03", "bot", "Здравствуйте! Я AIM, AI-ассистент Freedom Broker."),
    ("00:09", "bot", "Если вопрос о конкретной позиции, её нужно проверить отдельно."),
    ("00:54", "client", "Соедините с оператором."),
    ("01:10", "system", "Эскалация на оператора."),
    ("02:38", "operator", "Здравствуйте, я Соня. Уточните, вы смотрите в приложении или в веб-версии?"),
    ("03:18", "client", "В приложении."),
    ("04:07", "operator", "Чтобы проверить позицию, начнём с идентификации."),
    ("04:43", "client", "Готов."),
    ("05:11", "system", "Личность клиента подтверждена."),
    ("05:20", "client", "Ну что там?"),
    ("09:57", "operator", "Спасибо. Передал специалистам вопрос об отображении позиции."),
    ("10:20", "operator", "Могу ещё чем-то помочь? Хорошего вечера!"),
    ("10:30", "system", "Оператор закрыл чат."),
]


def make_conversation(rows: list[tuple[str, str, str]], cid: str = "test-ru-001",
                      channel: str = "agent chat", language: str = "RU") -> Conversation:
    def offset(ts: str) -> float:
        minutes, seconds = ts.split(":")
        return int(minutes) * 60 + int(seconds)

    return Conversation(
        conversation_id=cid, channel=channel, language=language,
        transcript=[{"idx": i, "timestamp": ts, "offset_sec": offset(ts), "speaker": sp, "text": text}
                    for i, (ts, sp, text) in enumerate(rows)],
    )


@pytest.fixture
def checklist() -> dict[str, Any]:
    return json.loads((ROOT / "config" / "checklist.default.json").read_text(encoding="utf-8"))


@pytest.fixture
def conversation() -> Conversation:
    return make_conversation(CHAT)
