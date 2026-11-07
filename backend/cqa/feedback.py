"""Сводка для оператора на начало смены: приветствие, «Что стоит улучшить?» и пункты А), Б), В).

Что улучшать и сколько это даст баллов, считает код (cqa.analytics). Модель только формулирует
приветствие и короткий совет к каждому пункту — без разбора отдельных диалогов (~300 токенов).
Структуру и цифры собирает код, поэтому формат одинаковый всегда. Без модели — шаблон.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from cqa.llm import ChatTurn, LLMError, LLMProvider

log = logging.getLogger(__name__)

LETTERS = "АБВГД"

# Короткие советы по критериям исходного чек-листа; для новых критериев — их название.
TIPS = {
    "C1": "Здоровайтесь на языке клиента и называйте своё имя в первом же ответе",
    "C2": "Уточняйте недостающие детали до ответа и не переспрашивайте то, что клиент уже написал",
    "C3": "Подтверждайте личность клиента до того, как сообщать данные его счёта",
    "C4": "Закрывайте каждый вопрос клиента или давайте понятный следующий шаг",
    "C5": "Отвечайте на конкретное неудобство клиента, а не шаблонной фразой",
    "C6": "Отвечайте так, чтобы клиенту не приходилось переспрашивать, и предупреждайте об ожидании",
    "C7": "В конце подводите итог или спрашивайте, чем ещё помочь, и прощайтесь",
}


class FeedbackText(BaseModel):
    greeting: str = Field(description="Приветствие по имени и пожелание хорошей смены, одно-два коротких предложения")
    items: list[str] = Field(description="Совет по каждому пункту, в том же порядке, одно предложение без цифр")


def _tip(item: Mapping[str, Any]) -> str:
    return TIPS.get(item["criterion_id"], item["title"])


def format_message(greeting: str, tips: list[str], profile: Mapping[str, Any]) -> str:
    """Единый формат сводки. Цифры роста балла берутся только из расчёта, не из текста модели."""
    weak = profile["weak"]
    if not weak:
        return f"{greeting}\nПо последним оценкам вы выполняете все критерии чек-листа — так держать!"
    lines = [greeting, "Что стоит улучшить?"]
    for letter, item, tip in zip(LETTERS, weak, tips, strict=False):
        lines.append(f"{letter}) {tip.rstrip('.')} — +{item['gain']:.1f} к среднему баллу.")
    if profile.get("avg_score") is not None and profile.get("potential_score") is not None:
        lines.append(f"Если поработать над этим, средний балл может вырасти с {profile['avg_score']:.1f} "
                     f"до {profile['potential_score']:.1f}.")
    return "\n".join(lines)


def template_message(profile: Mapping[str, Any]) -> str:
    greeting = f"Добрый день, {profile['operator']}! Хорошей вам смены."
    return format_message(greeting, [_tip(w) for w in profile["weak"]], profile)


_SYSTEM = ("Ты — наставник операторов поддержки брокерской компании. Пиши по-русски, доброжелательно "
           "и коротко, обращайся на «вы», без упрёков, без разбора конкретных диалогов и без цифр. "
           "Ничего не придумывай.")
# «А)», «Б.», «1)», «2.» в начале совета: нумерацию ставит код
_NUMBERING = re.compile(r"^\s*(?:[А-ДA-E]|\d+)\s*[).:]\s*")


def build_feedback(profile: Mapping[str, Any], provider_factory: Callable[[], LLMProvider] | None) -> dict[str, Any]:
    message, source = template_message(profile), "template"
    weak = profile["weak"]
    if provider_factory is not None and weak:
        points = "\n".join(f"{i + 1}. {w['title']} — смысл совета: {_tip(w)}" for i, w in enumerate(weak))
        prompt = (f"Оператор: {profile['operator']}.\nПункты для улучшения, по порядку:\n{points}\n\n"
                  f"Верни greeting — приветствие по имени и пожелание хорошей смены, и items — ровно {len(weak)} "
                  "советов, по одному на каждый пункт в том же порядке: одно короткое предложение в повелительном "
                  "наклонении, без цифр, без букв и номеров в начале.")
        try:
            result = provider_factory().generate_json(_SYSTEM, [ChatTurn("user", prompt)], FeedbackText)
            text = FeedbackText.model_validate_json(result.text)
            if text.greeting.strip() and len(text.items) == len(weak):
                # модель иногда сама ставит «А)» или «1.» — нумерацию добавляет код
                tips = [_NUMBERING.sub("", raw).strip() or _tip(w) for raw, w in zip(text.items, weak, strict=True)]
                message, source = format_message(text.greeting.strip(), tips, profile), result.model
        except (LLMError, ValueError) as exc:
            log.warning("сводка для %s: модель недоступна, используется шаблон (%s)", profile["operator"], exc)
    return {**profile, "date": date.today().isoformat(), "message": message, "source": source}
