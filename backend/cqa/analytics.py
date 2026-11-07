"""Аналитика для руководителя и оператора: KPI качества, соблюдение критериев, грубые ошибки,
рейтинг операторов и честный расчёт «сколько баллов даст исправление критерия».

Все цифры считаются из уже выставленных оценок — модель здесь не вызывается.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from cqa.scoring import NA, NO, YES, ScoringError, score

# Распределение итогов: 10 интервалов по 10 баллов (последний включает 100).
BUCKETS = [(lo, lo + 10 if lo < 90 else 100.01, f"{lo}–{lo + 10}") for lo in range(0, 100, 10)]


@dataclass(frozen=True)
class Row:
    """Диалог с последней оценкой (ответы и итог — финальные, с учётом апелляций)."""
    conversation_id: str
    operator: str
    department: str | None
    department_title: str
    answers: Mapping[str, Any]
    total: float | None
    critical: bool
    critical_subtypes: Sequence[str]
    critical_reason: str
    needs_review: bool


def best_answer(criterion: Mapping[str, Any], current: Any) -> Any:
    """Ответ «как надо»: для N/A остаётся N/A — неприменимый критерий нельзя «улучшить»."""
    if current == NA:
        return NA
    if criterion["type"] == "flag":
        return NO
    if criterion["type"] == "scale":
        return max(s["value"] for s in criterion["steps"])
    return YES


def criterion_share(criterion: Mapping[str, Any], answer: Any) -> float | None:
    """Доля выполнения критерия в одном диалоге: 1 — выполнен полностью, None — неприменим."""
    if answer is None or answer == NA:
        return None
    if criterion["type"] == "flag":
        return 0.0 if answer == YES else 1.0
    if criterion["type"] == "scale":
        top = max(s["value"] for s in criterion["steps"])
        return float(answer) / top if top else None
    return 1.0 if answer == YES else 0.0


def potential_gain(rows: Sequence[Row], checklist: Mapping[str, Any], criterion_id: str) -> float:
    """Средний рост итога, если бы в этих диалогах критерий был выполнен идеально (баллы, 0–100)."""
    criterion = next(c for c in checklist["criteria"] if c["id"] == criterion_id)
    gains = []
    for r in rows:
        if r.total is None or criterion_id not in r.answers:
            continue
        improved = dict(r.answers, **{criterion_id: best_answer(criterion, r.answers[criterion_id])})
        try:
            new_total = score(checklist, improved).total
        except ScoringError:
            continue
        if new_total is not None:
            gains.append(float(new_total) - r.total)
    return round(statistics.fmean(gains), 2) if gains else 0.0


def criteria_stats(rows: Sequence[Row], checklist: Mapping[str, Any]) -> list[dict[str, Any]]:
    stats = []
    for c in sorted(checklist["criteria"], key=lambda c: c.get("order", 0)):
        shares = [s for r in rows if (s := criterion_share(c, r.answers.get(c["id"]))) is not None]
        na = sum(1 for r in rows if r.answers.get(c["id"]) == NA)
        stats.append({
            "criterion_id": c["id"], "title": c["title"], "type": c["type"],
            "compliance": round(100 * statistics.fmean(shares), 1) if shares else None,
            "applicable": len(shares), "na": na,
            "gain": None if c["type"] == "flag" else potential_gain(rows, checklist, c["id"]),
        })
    return stats


def _avg(values: Sequence[float]) -> float | None:
    return round(statistics.fmean(values), 2) if values else None


def _group(rows: Sequence[Row], key: str) -> dict[Any, list[Row]]:
    groups: dict[Any, list[Row]] = defaultdict(list)
    for r in rows:
        groups[getattr(r, key)].append(r)
    return groups


def operator_profile(rows: Sequence[Row], checklist: Mapping[str, Any], name: str, top: int = 3) -> dict[str, Any]:
    """Профиль оператора: средний балл, место в рейтинге и слабые критерии с ростом балла."""
    mine = [r for r in rows if r.operator == name]
    scored = [r.total for r in mine if r.total is not None]
    stats = criteria_stats(mine, checklist)
    weak = sorted((s for s in stats if s["type"] != "flag" and s["gain"] and s["gain"] > 0),
                  key=lambda s: -s["gain"])[:top]
    ranking = operators_ranking(rows)
    place = next((i + 1 for i, op in enumerate(ranking) if op["operator"] == name), None)
    avg = _avg(scored)
    return {
        "operator": name,
        "department_title": mine[0].department_title if mine else None,
        "dialogs": len(mine),
        "avg_score": avg,
        "place": place, "of": len(ranking),
        "critical": sum(r.critical for r in mine),
        "weak": [{"criterion_id": s["criterion_id"], "title": s["title"], "compliance": s["compliance"],
                  "gain": s["gain"]} for s in weak],
        "potential_score": round(min(100.0, (avg or 0) + sum(s["gain"] for s in weak)), 2) if avg is not None else None,
    }


def operator_conversations(rows: Sequence[Row], name: str) -> list[dict[str, Any]]:
    """Диалоги оператора: сначала грубые ошибки, затем по возрастанию итога — с чего начать разбор."""
    mine = [r for r in rows if r.operator == name]
    mine.sort(key=lambda r: (not r.critical, r.total if r.total is not None else -1))
    return [{"conversation_id": r.conversation_id, "total": r.total, "critical": r.critical,
             "department_title": r.department_title} for r in mine]


def operators_ranking(rows: Sequence[Row]) -> list[dict[str, Any]]:
    ranking = []
    for name, group in _group(rows, "operator").items():
        if name in ("—", "Имя скрыто"):
            continue
        scored = [r.total for r in group if r.total is not None]
        if not scored:
            continue
        ranking.append({"operator": name, "department_title": group[0].department_title, "dialogs": len(group),
                        "avg_score": _avg(scored), "critical": sum(r.critical for r in group)})
    return sorted(ranking, key=lambda o: (-(o["avg_score"] or 0), o["critical"], o["operator"]))


def build_dashboard(rows: Sequence[Row], checklist: Mapping[str, Any], kpi: Mapping[str, Any],
                    open_appeals: int, total_dialogs: int) -> dict[str, Any]:
    scored = [r.total for r in rows if r.total is not None]
    targets = kpi.get("quality_targets", {})
    criteria = criteria_stats(rows, checklist)
    avg = _avg(scored)
    critical_rows = [r for r in rows if r.critical]
    compliance_target = targets.get("criterion_compliance", 90)

    departments = []
    for dep_id, group in sorted(_group(rows, "department").items(), key=lambda kv: kv[1][0].department_title):
        dep_scores = [r.total for r in group if r.total is not None]
        departments.append({
            "department": dep_id, "title": group[0].department_title, "dialogs": len(group),
            "avg_score": _avg(dep_scores), "critical": sum(r.critical for r in group),
            "operators": len({r.operator for r in group if r.operator}),
            "needs_review": sum(r.needs_review for r in group),
        })

    return {
        "summary": {
            "dialogs": total_dialogs, "evaluated": len(rows), "avg_score": avg,
            "median_score": round(statistics.median(scored), 2) if scored else None,
            "critical": len(critical_rows), "needs_review": sum(r.needs_review for r in rows),
            "open_appeals": open_appeals,
        },
        "kpi": [
            {"id": "avg_score", "title": "Средний балл качества", "value": avg,
             "target": targets.get("avg_score"), "unit": "", "better": "higher"},
            {"id": "critical", "title": "Грубые ошибки", "value": len(critical_rows),
             "target": targets.get("critical_errors"), "unit": "", "better": "lower"},
            {"id": "compliance", "title": f"Критериев с соблюдением ≥ {compliance_target}%",
             "value": sum(1 for c in criteria if c["type"] != "flag" and (c["compliance"] or 0) >= compliance_target),
             "target": sum(1 for c in criteria if c["type"] != "flag"), "unit": "", "better": "higher"},
        ],
        "distribution": [{"label": label, "from": lo, "to": min(hi, 100),
                          "count": sum(1 for s in scored if lo <= s < hi)} for lo, hi, label in BUCKETS],
        "score_target": targets.get("avg_score"),
        "criteria": criteria,
        "departments": departments,
        "operators": operators_ranking(rows),
        "critical": [{"conversation_id": r.conversation_id, "operator": r.operator,
                      "department_title": r.department_title, "subtypes": list(r.critical_subtypes),
                      "reason": r.critical_reason, "total": r.total}
                     for r in critical_rows],
    }
