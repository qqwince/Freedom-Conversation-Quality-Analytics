"""Сравнение оценок системы с эталоном (gold): точность по критериям, ошибка итога, матрицы ошибок.

Итоги считаются из ответов одним и тем же модулем scoring, поэтому расхождение итога
означает расхождение ответов, а не арифметики.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from cqa.scoring import NA, ScoringError, normalize_answer, score

TOTAL_TOLERANCE = 5.0   # «близкий» итог: отклонение не больше 5 баллов

# (эталон «Да», система «Да») → исход для флага грубой ошибки
_FLAG_OUTCOME = {(True, True): "tp", (False, True): "fp", (True, False): "fn", (False, False): "tn"}


@dataclass
class CriterionStats:
    criterion_id: str
    n: int = 0
    correct: int = 0
    abs_errors: list[float] = field(default_factory=list)   # только шкалы, когда оба ответа числовые
    confusion: Counter[tuple[str, str]] = field(default_factory=Counter)

    @property
    def accuracy(self) -> float | None:
        return self.correct / self.n if self.n else None

    @property
    def mae(self) -> float | None:
        return sum(self.abs_errors) / len(self.abs_errors) if self.abs_errors else None


@dataclass
class Mismatch:
    conversation_id: str
    criterion_id: str
    gold: Any
    predicted: Any


@dataclass
class Report:
    conversations: int
    missing_predictions: list[str]
    criteria: dict[str, CriterionStats]
    total_errors: dict[str, float]              # conversation_id → |итог системы − итог эталона|
    total_undefined_mismatch: list[str]         # у одного итог есть, у другого нет
    by_language: dict[str, dict[str, float | None]]
    critical: dict[str, int]                    # tp / fp / fn / tn
    mismatches: list[Mismatch]

    @property
    def answer_accuracy(self) -> float | None:
        n = sum(s.n for s in self.criteria.values())
        return sum(s.correct for s in self.criteria.values()) / n if n else None

    @property
    def total_mae(self) -> float | None:
        values = list(self.total_errors.values())
        return sum(values) / len(values) if values else None

    @property
    def total_within_tolerance(self) -> float | None:
        values = list(self.total_errors.values())
        return sum(v <= TOTAL_TOLERANCE for v in values) / len(values) if values else None

    def to_dict(self) -> dict[str, Any]:
        def r(x: float | None, d: int = 4) -> float | None:
            return None if x is None else round(x, d)

        return {
            "conversations": self.conversations,
            "missing_predictions": self.missing_predictions,
            "answer_accuracy": r(self.answer_accuracy),
            "total_mae": r(self.total_mae, 2),
            "total_within_5": r(self.total_within_tolerance),
            "total_undefined_mismatch": self.total_undefined_mismatch,
            "critical": self.critical,
            "criteria": {
                cid: {
                    "n": s.n, "accuracy": r(s.accuracy), "mae": r(s.mae, 2),
                    "confusion": {f"{g}→{p}": n for (g, p), n in sorted(s.confusion.items())},
                }
                for cid, s in self.criteria.items()
            },
            "by_language": self.by_language,
            "total_errors": {k: round(v, 2) for k, v in sorted(self.total_errors.items())},
            "mismatches": [m.__dict__ for m in self.mismatches],
        }


def compare(
    checklist: Mapping[str, Any],
    gold: Mapping[str, Mapping[str, Any]],
    predicted: Mapping[str, Mapping[str, Any]],
    languages: Mapping[str, str] | None = None,
) -> Report:
    """gold / predicted: {conversation_id: {criterion_id: ответ}}. Отсутствующий ответ системы — ошибка."""
    criteria = {c["id"]: c for c in checklist["criteria"]}
    stats = {cid: CriterionStats(cid) for cid in criteria}
    mismatches: list[Mismatch] = []
    total_errors: dict[str, float] = {}
    undefined: list[str] = []
    critical = Counter({"tp": 0, "fp": 0, "fn": 0, "tn": 0})
    lang_rows: dict[str, list[tuple[int, int, float | None]]] = defaultdict(list)
    missing = sorted(cid for cid in gold if cid not in predicted)

    for conv_id, gold_answers in gold.items():
        pred_answers = predicted.get(conv_id, {})
        conv_correct = conv_n = 0
        for crit_id, criterion in criteria.items():
            if crit_id not in gold_answers:
                continue
            g = normalize_answer(criterion, gold_answers[crit_id])
            p = _safe_normalize(criterion, pred_answers.get(crit_id))
            s = stats[crit_id]
            s.n += 1
            conv_n += 1
            s.confusion[(_label(g), _label(p))] += 1
            if g == p:
                s.correct += 1
                conv_correct += 1
            else:
                mismatches.append(Mismatch(conv_id, crit_id, g, p))
            if criterion["type"] == "scale" and _is_number(g) and _is_number(p):
                s.abs_errors.append(abs(float(g) - float(p)))
            if criterion["type"] == "flag":
                critical[_FLAG_OUTCOME[(g == "yes", p == "yes")]] += 1

        g_total = _total(checklist, gold_answers)
        p_total = _total(checklist, pred_answers)
        err: float | None = None
        if g_total is not None and p_total is not None:
            err = abs(g_total - p_total)
            total_errors[conv_id] = err
        elif (g_total is None) != (p_total is None):
            undefined.append(conv_id)
        if languages:
            lang_rows[languages.get(conv_id, "?")].append((conv_correct, conv_n, err))

    by_language = {
        lang: {
            "conversations": len(rows),
            "answer_accuracy": round(sum(c for c, _, _ in rows) / max(sum(n for _, n, _ in rows), 1), 4),
            "total_mae": _mean([e for _, _, e in rows if e is not None]),
        }
        for lang, rows in sorted(lang_rows.items())
    }
    return Report(len(gold), missing, stats, total_errors, undefined, by_language, dict(critical), mismatches)


def _safe_normalize(criterion: Mapping[str, Any], answer: Any) -> Any:
    if answer is None:
        return None
    try:
        return normalize_answer(criterion, answer)
    except ScoringError:
        return None


def _total(checklist: Mapping[str, Any], answers: Mapping[str, Any]) -> float | None:
    ids = {c["id"] for c in checklist["criteria"]}
    if set(answers) != ids or any(a is None for a in answers.values()):
        return None
    try:
        result = score(checklist, answers).total
    except ScoringError:
        return None
    return None if result is None else float(result)


def _label(answer: Any) -> str:
    return "—" if answer is None else str(answer)


def _is_number(answer: Any) -> bool:
    return answer is not None and answer != NA and not isinstance(answer, str)


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None
