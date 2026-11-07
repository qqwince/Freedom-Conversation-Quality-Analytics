"""Чат-наставник для оператора: объясняет, за что сняты баллы и как исправить, на основе
правил чек-листа и обоснований оценок именно этого оператора.

Экономия токенов: в контекст попадают только критерии, где оператор недобрал баллов, по 1–2
обоснования на критерий, и последние реплики переписки; число вопросов в день ограничено.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from cqa.llm import ChatTurn, LLMProvider

DAILY_LIMIT = 30
HISTORY_TURNS = 6
EXAMPLES_PER_CRITERION = 2
MAX_QUESTION_CHARS = 600


class CoachAnswer(BaseModel):
    answer: str = Field(description="Ответ наставника, 2–6 предложений, по-русски, на «вы»")


class CoachLimitError(RuntimeError):
    pass


_SYSTEM = """Ты — наставник операторов поддержки брокерской компании Freedom Broker.
Помогаешь оператору понять оценки качества его диалогов и что делать иначе.
Правила:
- Отвечай по-русски, на «вы», доброжелательно и конкретно, 2–6 предложений.
- Опирайся только на правила чек-листа и обоснования оценок из контекста. Не придумывай факты,
  цифры и реплики. Если в контексте нет ответа, так и скажи и посоветуй обратиться к руководителю
  или подать апелляцию, если оператор не согласен с оценкой.
- Когда объясняешь ошибку, назови диалог и что нужно было сделать, и дай пример хорошей формулировки.
- Отвечай только на вопросы о работе оператора, оценках и чек-листе. На другие темы вежливо откажись
  одной фразой."""


class _Limiter:
    def __init__(self) -> None:
        self._counts: dict[tuple[str, str], int] = defaultdict(int)
        self._lock = threading.Lock()

    def take(self, key: str, limit: int) -> int:
        today = date.today().isoformat()
        with self._lock:
            used = self._counts[(key, today)]
            if used >= limit:
                raise CoachLimitError(f"на сегодня лимит вопросов наставнику исчерпан ({limit}); он обновится завтра")
            self._counts[(key, today)] = used + 1
            return limit - used - 1


limiter = _Limiter()


def build_context(operator: str, profile: Mapping[str, Any], checklist: Mapping[str, Any],
                  evaluations: Sequence[Mapping[str, Any]]) -> str:
    """Короткий контекст: цифры, правила слабых критериев и обоснования по диалогам оператора."""
    criteria = {c["id"]: c for c in checklist["criteria"]}
    lines = [f"Оператор: {operator}. Средний балл: {profile.get('avg_score')}. "
             f"Оценённых диалогов: {profile.get('dialogs')}."]
    lost: dict[str, list[str]] = defaultdict(list)
    for ev in evaluations:
        for r in ev["results"]:
            c = criteria.get(r["criterion_id"])
            if c is None or r.get("answer") in (None, "na"):
                continue
            top = max((s["value"] for s in c.get("steps", [])), default=None)
            failed = (r["answer"] == "no" and c["type"] == "binary") or (c["type"] == "flag" and r["answer"] == "yes") \
                or (c["type"] == "scale" and top is not None and r["answer"] < top)
            if failed and len(lost[c["id"]]) < EXAMPLES_PER_CRITERION:
                lost[c["id"]].append(f"диалог {ev['conversation_id']}: ответ {r['answer']}. {r.get('explanation', '')}")
    if not lost:
        lines.append("По последним оценкам оператор выполняет все критерии.")
    for cid, examples in lost.items():
        c = criteria[cid]
        lines.append(f"\nКритерий «{c['title']}». Правило: {c.get('decision') or c['description']}")
        lines += [f"  - {e}" for e in examples]
    return "\n".join(lines)


def ask(operator: str, question: str, history: Sequence[Mapping[str, str]], context: str,
        provider_factory: Callable[[], LLMProvider]) -> dict[str, Any]:
    question = question.strip()[:MAX_QUESTION_CHARS]
    if not question:
        raise ValueError("напишите вопрос")
    remaining = limiter.take(operator, DAILY_LIMIT)
    system = f"{_SYSTEM}\n\nКонтекст об оценках оператора:\n{context}"
    turns: list[ChatTurn] = []
    for m in list(history)[-HISTORY_TURNS:]:
        if m.get("role") == "user" and m.get("text"):
            turns.append(ChatTurn("user", m["text"]))
        elif m.get("role") == "assistant" and m.get("text"):
            turns.append(ChatTurn("model", CoachAnswer(answer=m["text"]).model_dump_json()))
    turns.append(ChatTurn("user", question))
    result = provider_factory().generate_json(system, turns, CoachAnswer)
    answer = CoachAnswer.model_validate_json(result.text).answer.strip()
    return {"answer": answer, "remaining_today": remaining, "model": result.model}
