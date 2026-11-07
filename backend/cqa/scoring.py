"""Детерминированный расчёт баллов по чек-листу.

Модель возвращает только ответы по критериям; всё, что связано с числами, считается здесь.

Порядок расчёта (стр. 2 чек-листа):
1. Применимость: N/A только там, где он разрешён.
2. «Да» даёт полный вес, «Нет» — ноль; для шкалы — одна из указанных ступеней.
3. Шкальная оценка пересчитывается пропорционально весу; верхняя ступень даёт полный вес.
4. Веса критериев с N/A перераспределяются пропорционально между применимыми; максимум — 100.
5. Итог округляется до двух знаков. Нет применимых критериев с весом — итог не рассчитывается.
   Грубые ошибки отмечаются отдельно и на итог не влияют.

Внутри используется Decimal, округление ROUND_HALF_UP только для итога.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

Answer = str | int | float  # "yes" | "no" | "na" | значение ступени шкалы

YES, NO, NA = "yes", "no", "na"
BINARY, SCALE, FLAG = "binary", "scale", "flag"


class ScoringError(ValueError):
    """Ответ не соответствует чек-листу (недопустимое значение, N/A там, где он запрещён, и т. п.)."""


@dataclass
class CriterionScore:
    criterion_id: str
    section: str
    type: str
    answer: Answer
    weight: Decimal                    # исходный вес из чек-листа
    applicable: bool                   # участвует в итоге (не N/A, вес > 0, не флаг)
    fraction: Decimal | None           # доля полного веса: 1, 0, value/max; None — N/A или флаг
    effective_weight: Decimal          # вес после перераспределения N/A
    points: Decimal | None             # баллы после перераспределения (без округления)


@dataclass
class SectionScore:
    section_id: str
    title: str
    points: Decimal | None             # None — в секции нет применимых критериев
    max: Decimal


@dataclass
class ScoreResult:
    total: Decimal | None              # None — итог не рассчитывается
    max_total: Decimal
    applicable_weight: Decimal         # сумма исходных весов применимых критериев
    criteria: list[CriterionScore]
    sections: list[SectionScore]
    critical_flag: bool
    critical_subtypes: list[str] = field(default_factory=list)

    def to_dict(self, digits: int = 2) -> dict[str, Any]:
        """Сериализация для API/хранения: числа как float, баллы критериев округлены для показа."""

        def num(x: Decimal | None, d: int = digits) -> float | None:
            return None if x is None else float(_round(x, d))

        return {
            "total": num(self.total),
            "max_total": float(self.max_total),
            "applicable_weight": float(self.applicable_weight),
            "critical": {"flag": self.critical_flag, "subtypes": list(self.critical_subtypes)},
            "criteria": [
                {
                    "criterion_id": c.criterion_id,
                    "section": c.section,
                    "type": c.type,
                    "answer": c.answer,
                    "weight": float(c.weight),
                    "applicable": c.applicable,
                    "fraction": num(c.fraction, 4),
                    "effective_weight": num(c.effective_weight),
                    "points": num(c.points),
                }
                for c in self.criteria
            ],
            "sections": [
                {"section_id": s.section_id, "title": s.title, "points": num(s.points), "max": num(s.max)}
                for s in self.sections
            ],
        }


def _round(x: Decimal, digits: int) -> Decimal:
    return x.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)


def _dec(x: Any) -> Decimal:
    # через str, чтобы 0.1 не превращалось в 0.1000000000000000055…
    return Decimal(str(x))


def normalize_answer(criterion: Mapping[str, Any], answer: Any) -> Answer:
    """Приводит ответ к каноническому виду и проверяет его по описанию критерия."""
    cid = criterion["id"]
    ctype = criterion["type"]

    if isinstance(answer, str):
        a = answer.strip().lower()
        a = {"да": YES, "нет": NO, "n/a": NA, "н/д": NA, "true": YES, "false": NO}.get(a, a)
        if a not in (YES, NO, NA):
            try:  # шкальное значение, пришедшее строкой
                answer = float(a)
            except ValueError:
                raise ScoringError(f"{cid}: непонятный ответ {answer!r}") from None
        else:
            answer = a
    elif isinstance(answer, bool):
        answer = YES if answer else NO
    elif answer is None:
        raise ScoringError(f"{cid}: нет ответа")

    if answer == NA:
        if not criterion.get("na_allowed", False):
            raise ScoringError(f"{cid}: N/A для этого критерия не разрешён")
        return NA

    if ctype in (BINARY, FLAG):
        if answer not in (YES, NO):
            raise ScoringError(f"{cid}: для типа {ctype} допустимы только Да/Нет, получено {answer!r}")
        return answer

    if ctype == SCALE:
        if isinstance(answer, str):
            raise ScoringError(f"{cid}: для шкалы нужно числовое значение ступени, получено {answer!r}")
        values = [_dec(s["value"]) for s in criterion.get("steps") or []]
        if not values:
            raise ScoringError(f"{cid}: у шкального критерия не заданы ступени")
        if _dec(answer) not in values:
            allowed = ", ".join(str(v.normalize()) for v in sorted(values, reverse=True))
            raise ScoringError(f"{cid}: значение {answer} не является ступенью шкалы ({allowed})")
        v = _dec(answer)
        return int(v) if v == v.to_integral_value() else float(v)

    raise ScoringError(f"{cid}: неизвестный тип критерия {ctype!r}")


def _fraction(criterion: Mapping[str, Any], answer: Answer) -> Decimal:
    if criterion["type"] == BINARY:
        return Decimal(1) if answer == YES else Decimal(0)
    top = max(_dec(s["value"]) for s in criterion["steps"])
    bottom = min(_dec(s["value"]) for s in criterion["steps"])
    if top == bottom:
        raise ScoringError(f"{criterion['id']}: у шкалы одна ступень")
    # Ступени чек-листа начинаются с 0, тогда это просто value/top.
    return (_dec(answer) - bottom) / (top - bottom)


def score(
    checklist: Mapping[str, Any],
    answers: Mapping[str, Any],
    critical_subtypes: Mapping[str, list[str]] | None = None,
) -> ScoreResult:
    """Считает итог по чек-листу.

    answers: {criterion_id: ответ} — ответ на каждый критерий чек-листа обязателен.
    critical_subtypes: {criterion_id: [подтипы]} для флаговых критериев (необязательно).
    """
    criteria = sorted(checklist["criteria"], key=lambda c: c.get("order", 0))
    scoring_cfg = checklist.get("scoring", {})
    max_total = _dec(scoring_cfg.get("max_total", 100))
    digits = int(scoring_cfg.get("rounding_digits", 2))
    critical_subtypes = critical_subtypes or {}

    unknown = set(answers) - {c["id"] for c in criteria}
    if unknown:
        raise ScoringError(f"ответы на критерии, которых нет в чек-листе: {', '.join(sorted(unknown))}")

    rows: list[CriterionScore] = []
    flag = False
    subtypes: list[str] = []
    for c in criteria:
        if c["id"] not in answers:
            raise ScoringError(f"{c['id']}: нет ответа")
        a = normalize_answer(c, answers[c["id"]])
        weight = _dec(c.get("weight", 0))
        is_flag = c["type"] == FLAG or c.get("is_critical_flag", False)
        if is_flag:
            if a == YES:
                flag = True
                subtypes.extend(critical_subtypes.get(c["id"], []))
            rows.append(CriterionScore(c["id"], c["section"], c["type"], a, weight, False, None, Decimal(0), None))
            continue
        applicable = a != NA and weight > 0
        frac = _fraction(c, a) if a != NA else None
        rows.append(CriterionScore(c["id"], c["section"], c["type"], a, weight, applicable, frac, Decimal(0), None))

    applicable_weight = sum((r.weight for r in rows if r.applicable), Decimal(0))

    if applicable_weight > 0:
        k = max_total / applicable_weight
        for r in rows:
            if r.applicable:
                r.effective_weight = r.weight * k
                r.points = r.effective_weight * r.fraction
        total: Decimal | None = _round(sum((r.points for r in rows if r.applicable), Decimal(0)), digits)
    else:
        total = None

    sections: list[SectionScore] = []
    for s in sorted(checklist.get("sections", []), key=lambda s: s.get("order", 0)):
        in_section = [r for r in rows if r.section == s["id"]]
        if not in_section or all(r.type == FLAG for r in in_section):
            continue  # секция грубых нарушений баллов не имеет
        appl = [r for r in in_section if r.applicable]
        sections.append(SectionScore(
            section_id=s["id"],
            title=s.get("title", s["id"]),
            points=sum((r.points for r in appl), Decimal(0)) if appl and total is not None else None,
            max=sum((r.effective_weight for r in appl), Decimal(0)),
        ))

    return ScoreResult(total, max_total, applicable_weight, rows, sections, flag, subtypes)


def apply_decisions(answers: Mapping[str, Any], decisions: Mapping[str, Any]) -> dict[str, Any]:
    """Финальные ответы = исходные с подменой по решениям проверяющего. Исходные не меняются."""
    final = dict(answers)
    for cid, new_answer in decisions.items():
        if cid not in final:
            raise ScoringError(f"решение по критерию {cid}, которого нет в оценке")
        final[cid] = new_answer
    return final
