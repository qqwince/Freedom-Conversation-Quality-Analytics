"""Черновик нового критерия по названию и описанию: модель предлагает тип ответа, шкалу,
правило N/A, подпункты проверки и правило решения — по образцу критериев текущего чек-листа.

Черновик не сохраняется: администратор проверяет его в редакторе и сам сохраняет версию.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field

from cqa.llm import ChatTurn, LLMProvider


class DraftStep(BaseModel):
    value: int = Field(ge=0, description="Значение ступени")
    text: str = Field(description="Когда ставить это значение")


class CriterionDraft(BaseModel):
    type: Literal["binary", "scale", "flag"] = Field(description="binary — Да/Нет; scale — шкала; flag — грубая ошибка")
    section: str = Field(description="id секции чек-листа, к которой относится критерий")
    description: str = Field(description="Формулировка: что считается «Да» (или что оценивает шкала)")
    steps: list[DraftStep] | None = Field(default=None,
                                          description="Только для scale: 3–5 ступеней, верхняя — полный вес")
    na_allowed: bool = Field(description="Бывает ли критерий неприменим к диалогу")
    na_rule: str | None = Field(default=None, description="Когда ставить N/A (если na_allowed)")
    checks: list[str] | None = Field(default=None,
                                     description="3–5 подпунктов проверки: вопросы, на которые отвечают по репликам")
    decision: str = Field(description="Правило: как из ответов на подпункты получить итоговый ответ")


_SYSTEM = """Ты — методолог контроля качества колл-центра брокерской компании.
По названию и описанию нового критерия оформи его в том же формате, что и критерии чек-листа ниже.
Правила:
- Оценивается только работа оператора по транскрипту чата или звонка (RU, KZ, EN).
- Тип: binary, если ответ «выполнено / нет»; scale — если важна степень (дай 3–5 ступеней с понятными
  описаниями, нижняя 0, верхняя — полное выполнение); flag — только для грубого нарушения без баллов.
- N/A разрешай, только если критерий бывает объективно неприменим, и опиши условие.
- Подпункты — конкретные вопросы с опорой на реплики («Укажите реплику»).
- Пиши по-русски, коротко и однозначно, ничего не придумывай про компанию."""


def _examples(checklist: Mapping[str, Any]) -> str:
    keep = ("id", "section", "title", "type", "description", "steps", "na_allowed", "na_rule", "checks", "decision")
    sample = [c for c in checklist["criteria"] if c["type"] == "scale"][:1] + \
             [c for c in checklist["criteria"] if c["type"] == "binary" and c.get("na_allowed")][:1]
    return json.dumps([{k: c.get(k) for k in keep} for c in sample], ensure_ascii=False, indent=1)


def next_criterion_id(checklist: Mapping[str, Any]) -> str:
    used = {c["id"] for c in checklist["criteria"]}
    numbers = [int(m.group(1)) for cid in used if (m := re.fullmatch(r"C(\d+)", cid))]
    n = max(numbers, default=0) + 1
    while f"C{n}" in used:
        n += 1
    return f"C{n}"


def draft_criterion(checklist: Mapping[str, Any], title: str, description: str, weight: float,
                    provider_factory: Callable[[], LLMProvider]) -> dict[str, Any]:
    title, description = title.strip(), description.strip()
    if not title or not description:
        raise ValueError("укажите название и описание критерия")
    sections = {s["id"]: s["title"] for s in checklist.get("sections", [])}
    prompt = (f"Секции чек-листа (id — название): {json.dumps(sections, ensure_ascii=False)}\n\n"
              f"Пример критериев чек-листа:\n{_examples(checklist)}\n\n"
              f"Новый критерий.\nНазвание: {title}\nОписание: {description}")
    result = provider_factory().generate_json(_SYSTEM, [ChatTurn("user", prompt)], CriterionDraft)
    draft = CriterionDraft.model_validate_json(result.text)
    return normalize_draft(checklist, title, weight, draft)


def normalize_draft(checklist: Mapping[str, Any], title: str, weight: float, draft: CriterionDraft) -> dict[str, Any]:
    """Приводит черновик к правилам чек-листа: существующая секция, корректная шкала, вес флага 0."""
    sections = [s["id"] for s in checklist.get("sections", [])]
    section = draft.section if draft.section in sections else next(
        (c["section"] for c in checklist["criteria"] if c["type"] != "flag"), sections[0] if sections else "")
    criterion: dict[str, Any] = {
        "id": next_criterion_id(checklist),
        "section": section,
        "order": max((c.get("order", 0) for c in checklist["criteria"]), default=0) + 1,
        "title": title,
        "type": draft.type,
        "weight": 0 if draft.type == "flag" else weight,
        "na_allowed": draft.na_allowed and draft.type != "flag",
        "na_rule": (draft.na_rule or "").strip() or None,
        "description": draft.description.strip(),
        "checks": [c.strip() for c in draft.checks or [] if c.strip()][:6],
        "decision": draft.decision.strip(),
        "validators": [],
        "examples": [],
        "is_critical_flag": draft.type == "flag",
        "generated": True,
    }
    if draft.type == "scale":
        steps: dict[int, str] = {}
        for s in draft.steps or []:
            steps.setdefault(s.value, s.text.strip())
        if 0 not in steps:
            steps[0] = "Не выполнено"
        if len(steps) < 2:
            steps[max(steps) + 10 if steps else 10] = "Выполнено полностью"
        criterion["steps"] = [{"value": v, "text": t} for v, t in sorted(steps.items(), reverse=True)]
    if criterion["na_allowed"] and not criterion["na_rule"]:
        criterion["na_rule"] = "Критерий неприменим к этому диалогу."
    return criterion
