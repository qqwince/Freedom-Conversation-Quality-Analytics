"""Сборка промпта из чек-листа, транскрипта и фактов.

Всё содержание оценки (критерии, подпункты, правила N/A, шкалы) берётся из чек-листа,
поэтому новый или изменённый в интерфейсе критерий попадает в промпт без правки кода.
Веса в промпт не передаются: баллы считает код, а модели они только мешают.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from cqa.models import Conversation
from cqa.transcript import ConversationFacts, format_facts, format_transcript

PROMPT_VERSION = "v3"

_ROLE = """Ты — эксперт службы контроля качества брокерской компании Freedom Broker.
Ты оцениваешь работу ОПЕРАТОРА в диалоге с клиентом строго по чек-листу.
Диалоги бывают на русском, казахском и английском; критерии одинаковы для всех языков."""

_OUTPUT_RULES = """Как отвечать:
1. По каждому критерию сначала ответь на все его подпункты (checks) в том же порядке,
   указывая номера реплик, затем выведи итоговый ответ строго по правилу решения.
2. Допустимые ответы указаны у каждого критерия. Для шкалы — только одно из перечисленных значений.
3. N/A ставь только там, где он разрешён, и только если выполнено условие N/A. Заполни na_reason.
4. Поле evidence обязательно для каждого критерия: номера реплик из транскрипта и что каждая подтверждает.
   Номера реплик в тексте объяснения не заменяют evidence.
   Для пропущенного действия заполни expected_from_msg и expected_to_msg — участок, где действие ожидалось.
   Для N/A укажи реплику, которая показывает, что условие неприменимости выполнено.
5. Объяснение — 1–3 предложения на русском по шаблону:
   «<Название критерия> — <Ответ>. <Почему>. Основание — <номера реплик>».
   Начинай с названия критерия словами (например, «Завершение»), а не с его кода (C7).
   В объяснении ответ пиши по-русски: «Да», «Нет», «N/A» или балл («15 из 20»).
6. Реплики БОТА и СИСТЕМЫ — контекст, а не действия оператора. Оцениваешь только ОПЕРАТОРА.
7. «Факты из кода» вычислены автоматически и надёжны; используй их, но решение принимаешь сам.
8. Верни результат по всем критериям чек-листа, ничего не пропуская."""


@dataclass(frozen=True)
class Prompt:
    system: str
    user: str
    version: str = PROMPT_VERSION


def _allowed_answers(criterion: Mapping[str, Any]) -> str:
    ctype = criterion["type"]
    if ctype == "scale":
        values = sorted((s["value"] for s in criterion.get("steps", [])), reverse=True)
        options = [str(v) for v in values]
    else:
        options = ["yes", "no"]
    if criterion.get("na_allowed"):
        options.append("na")
    return " | ".join(options)


def render_criterion(criterion: Mapping[str, Any], section_titles: Mapping[str, str]) -> str:
    lines = [
        f"### {criterion['id']}. {criterion['title']}",
        f"Секция: {section_titles.get(criterion['section'], criterion['section'])}",
        f"Тип: {criterion['type']}. Допустимые ответы: {_allowed_answers(criterion)}",
        f"Описание: {criterion['description']}",
    ]
    if criterion.get("na_allowed") and criterion.get("na_rule"):
        lines.append(f"Условие N/A: {criterion['na_rule']}")
    if criterion["type"] == "scale":
        lines.append("Шкала:")
        lines += [f"  {s['value']} — {s['text']}" for s in sorted(criterion["steps"], key=lambda s: -s["value"])]
    if criterion.get("subtypes"):
        lines.append("Подтипы (укажи найденные в flag_subtypes по id):")
        lines += [f"  {s['id']} — {s['title']}" for s in criterion["subtypes"]]
    if criterion.get("checks"):
        lines.append("Подпункты проверки:")
        lines += [f"  {i}. {q}" for i, q in enumerate(criterion["checks"], 1)]
    if criterion.get("decision"):
        lines.append(f"Правило решения: {criterion['decision']}")
    for example in criterion.get("examples", []):
        lines.append(f"Пример: {example}")
    return "\n".join(lines)


def _bullets(items: Sequence[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def build_prompt(
    checklist: Mapping[str, Any],
    conversation: Conversation,
    facts: ConversationFacts,
    criteria_ids: Sequence[str] | None = None,
) -> Prompt:
    """criteria_ids — оценить только часть критериев (переоценка изменённого критерия)."""
    section_titles = {s["id"]: s["title"] for s in checklist.get("sections", [])}
    criteria = sorted(checklist["criteria"], key=lambda c: c.get("order", 0))
    if criteria_ids is not None:
        wanted = set(criteria_ids)
        criteria = [c for c in criteria if c["id"] in wanted]

    system = "\n\n".join(filter(None, [
        _ROLE,
        "Общие правила:\n" + _bullets(checklist.get("context_rules", [])),
        "Правила доказательств:\n" + _bullets(checklist.get("evidence_rules", [])),
        _OUTPUT_RULES,
    ]))

    channel = "звонок (транскрипт распознанной речи, возможны ошибки распознавания)" if conversation.is_call \
        else "чат"
    user = "\n\n".join([
        "## Чек-лист\n\n" + "\n\n".join(render_criterion(c, section_titles) for c in criteria),
        f"## Диалог\nID: {conversation.conversation_id}. Канал: {channel}. Язык: {conversation.language}.",
        "## Факты из кода\n" + format_facts(facts),
        "## Транскрипт\n" + format_transcript(conversation),
        "Оцени оператора по критериям: " + ", ".join(c["id"] for c in criteria) + ".",
    ])
    return Prompt(system=system, user=user)
