"""Модели данных: диалог и структурированный ответ модели.

Чек-лист остаётся словарём (его схема — schemas/checklist.schema.json): он редактируется
в интерфейсе и может содержать поля, которые ядру оценки не нужны.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Speaker = Literal["client", "operator", "bot", "system"]


class Message(BaseModel):
    idx: int
    timestamp: str
    offset_sec: float | None = None
    speaker: Speaker
    text: str


class Conversation(BaseModel):
    conversation_id: str
    dataset_group_id: str | None = None
    channel: str
    language: str
    audio_url: str | None = None
    transcript: list[Message]

    @property
    def is_call(self) -> bool:
        return self.channel == "voice call"


# --- Ответ модели -------------------------------------------------------------------------
# Порядок полей важен: модель сначала отвечает на подпункты, потом даёт итоговый ответ.
# Баллы модель не считает.


class CheckAnswer(BaseModel):
    q: str = Field(description="Подпункт проверки (вопрос из checks)")
    a: str = Field(description="Короткий ответ на подпункт")
    msg: list[int] = Field(default_factory=list, description="Номера реплик, на которых основан ответ")


class Evidence(BaseModel):
    msg_idx: int = Field(description="Номер реплики")
    proves: str = Field(description="Что эта реплика подтверждает")


class CriterionVerdict(BaseModel):
    criterion_id: str
    checks: list[CheckAnswer]
    answer: str = Field(description="yes | no | na, для шкалы — значение ступени, например 15")
    flag_subtypes: list[str] = Field(default_factory=list, description="Только для флага: найденные подтипы")
    evidence: list[Evidence] = Field(
        description="Обязательно: реплики, на которых основан ответ (для пропущенного действия — где оно ожидалось)"
    )
    expected_from_msg: int | None = Field(
        default=None, description="Для пропущенного действия: начало участка, где оно ожидалось"
    )
    expected_to_msg: int | None = Field(default=None, description="Конец этого участка")
    na_reason: str | None = Field(default=None, description="Для N/A: какое условие неприменимости выполнено")
    explanation: str = Field(description="«<Название критерия словами> — <Ответ>. <Почему>. Основание — <реплики>»")


class LLMEvaluation(BaseModel):
    results: list[CriterionVerdict]
