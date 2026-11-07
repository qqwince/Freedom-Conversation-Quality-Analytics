"""Выгрузка результатов в формате организаторов (results.json, «Формат JSON 1.0»).

Правила из приложения к гайду:
- один объект на файл, в results — по записи на каждый диалог набора, без пропусков и повторов;
- коды критериев: contact, discovery, identification, consultation, empathy, dialogue, closure, critical
  (берутся из поля export_id критерия в чек-листе);
- value: true = «Да», false = «Нет», null = N/A, число — ступень шкалы до учёта веса;
- evidence_turns — номера реплик с 1 по исходному массиву транскрипта (у нас idx с 0, поэтому +1);
- final_score — итог 0–100 с двумя знаками, critical_flag совпадает с value критерия critical;
- не удалось оценить — status = "error", пустой criteria, score = null и причина в error.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

SCHEMA_VERSION = "1.0"
MODEL_MODES = ("local", "external_api", "hybrid")

# Допустимые значения по таблице «Правила заполнения».
ALLOWED_VALUES: dict[str, tuple[Any, ...]] = {
    "contact": (True, False),
    "discovery": (True, False, None),
    "identification": (True, False, None),
    "consultation": (0, 5, 10, 15, 20, None),
    "empathy": (True, False),
    "dialogue": (0, 4, 7, 10),
    "closure": (True, False, None),
    "critical": (True, False),
}


class ExportError(ValueError):
    pass


def to_value(answer: Any) -> Any:
    if answer == "yes":
        return True
    if answer == "no":
        return False
    if answer == "na":
        return None
    if isinstance(answer, float) and answer.is_integer():
        return int(answer)
    return answer


def evidence_turns(result: Mapping[str, Any]) -> list[int]:
    """Номера реплик с 1: доказательства, иначе подпункты проверки, иначе проверенный участок."""
    turns = {e["msg_idx"] for e in result.get("evidence") or []}
    if not turns:
        turns = {i for c in result.get("checks") or [] for i in c.get("msg") or []}
    expected = result.get("expected_at")
    if not turns and expected:
        end = expected.get("to_msg_idx") if expected.get("to_msg_idx") is not None else expected["from_msg_idx"]
        turns = set(range(expected["from_msg_idx"], end + 1))
    return sorted(i + 1 for i in turns)


def export_record(conversation_id: str, evaluation: Mapping[str, Any] | None,
                  checklist: Mapping[str, Any] | None, final: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Запись по одному диалогу. final — итог после апелляций (answers, total), если его нужно выгрузить."""
    if evaluation is None or checklist is None:
        return _error(conversation_id, "Диалог не оценён системой.")

    answers = dict(final["answers"]) if final else {r["criterion_id"]: r["answer"] for r in evaluation["results"]}
    total = final["total"] if final else evaluation.get("total")
    by_id = {r["criterion_id"]: r for r in evaluation["results"]}
    criteria = sorted(checklist["criteria"], key=lambda c: c.get("order", 0))

    missing = [c["id"] for c in criteria if answers.get(c["id"]) is None]
    if missing:
        return _error(conversation_id, f"Нет допустимого ответа модели по критериям: {', '.join(missing)}.")
    if evaluation.get("scoring_error") and not final:
        return _error(conversation_id, f"Итог не рассчитан: {evaluation['scoring_error']}")

    out_criteria = []
    critical_flag = False
    for c in criteria:
        code = c.get("export_id") or c["id"]
        value = to_value(answers[c["id"]])
        result = by_id.get(c["id"], {})
        out_criteria.append({
            "criterion_id": code,
            "value": value,
            "rationale": (result.get("explanation") or "").strip(),
            "evidence_turns": evidence_turns(result),
        })
        if c["type"] == "flag" or c.get("is_critical_flag"):
            critical_flag = critical_flag or value is True

    return {
        "conversation_id": conversation_id,
        "status": "ok",
        "criteria": out_criteria,
        "score": {"final_score": None if total is None else round(float(total), 2), "critical_flag": critical_flag},
    }


def build_results(
    conversation_ids: Sequence[str],
    evaluation_for: Callable[[str], Mapping[str, Any] | None],
    checklist_for: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    final_for: Callable[[Mapping[str, Any]], Mapping[str, Any] | None],
    team_name: str,
    model_mode: str,
) -> dict[str, Any]:
    if model_mode not in MODEL_MODES:
        raise ExportError(f"model.mode должен быть одним из {MODEL_MODES}")
    records, models = [], set()
    for cid in conversation_ids:
        evaluation = evaluation_for(cid)
        if evaluation is None:
            records.append(export_record(cid, None, None))
            continue
        models.update(_models_used(evaluation))
        records.append(export_record(cid, evaluation, checklist_for(evaluation), final_for(evaluation)))
    return {
        "schema_version": SCHEMA_VERSION,
        "team_name": team_name,
        "model": {"mode": model_mode, "name": ", ".join(sorted(models)) or "—"},
        "results": records,
    }


def validate_results(doc: Mapping[str, Any], expected_ids: Sequence[str] | None = None) -> list[str]:
    """Проверка перед push: структура, коды, типы значений, полнота и уникальность ID."""
    problems: list[str] = []
    for key in ("schema_version", "team_name", "model", "results"):
        if key not in doc:
            problems.append(f"нет поля {key}")
    if doc.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version должен быть {SCHEMA_VERSION}")
    if not str(doc.get("team_name") or "").strip():
        problems.append("не задано team_name")
    if (doc.get("model") or {}).get("mode") not in MODEL_MODES:
        problems.append("model.mode — local, external_api или hybrid")

    results = doc.get("results") or []
    ids = [r.get("conversation_id") for r in results]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        problems.append(f"повторяющиеся ID: {duplicates}")
    if expected_ids is not None:
        missing = sorted(set(expected_ids) - set(ids))
        extra = sorted(set(ids) - set(expected_ids))
        if missing:
            problems.append(f"нет записей для: {missing}")
        if extra:
            problems.append(f"лишние записи: {extra}")

    for r in results:
        cid = r.get("conversation_id")
        if r.get("status") == "error":
            if r.get("criteria") != [] or r.get("score") is not None or not r.get("error"):
                problems.append(f"{cid}: для error нужны пустой criteria, score = null и причина")
            continue
        if r.get("status") != "ok":
            problems.append(f"{cid}: status должен быть ok или error")
            continue
        codes = [c.get("criterion_id") for c in r.get("criteria") or []]
        if len(codes) != len(set(codes)):
            problems.append(f"{cid}: критерий указан дважды")
        for c in r.get("criteria") or []:
            code, value = c.get("criterion_id"), c.get("value")
            allowed = ALLOWED_VALUES.get(code)
            if allowed is not None and not _is_allowed(value, allowed):
                problems.append(f"{cid}: {code} = {value!r} — недопустимое значение")
            turns = c.get("evidence_turns")
            if not isinstance(turns, list) or not all(isinstance(t, int) and t >= 1 for t in turns):
                problems.append(f"{cid}: {code} — evidence_turns должны быть номерами с 1")
        score = r.get("score") or {}
        final = score.get("final_score")
        if final is not None and not (0 <= final <= 100):
            problems.append(f"{cid}: final_score вне 0–100")
        critical = next((c.get("value") for c in r.get("criteria") or [] if c.get("criterion_id") == "critical"), None)
        if critical is not None and score.get("critical_flag") != critical:
            problems.append(f"{cid}: critical_flag не совпадает с критерием critical")
    return problems


def _is_allowed(value: Any, allowed: tuple[Any, ...]) -> bool:
    """Сравнение с учётом типа: в Python True == 1, а для формата это разные значения."""
    return any(value is None if a is None else (type(value) is type(a) and value == a) for a in allowed)


def _error(conversation_id: str, reason: str) -> dict[str, Any]:
    return {"conversation_id": conversation_id, "status": "error", "criteria": [], "score": None, "error": reason}


def _models_used(evaluation: Mapping[str, Any]) -> set[str]:
    calls = (evaluation.get("log") or {}).get("calls") or []
    used = {c["model"] for c in calls if c.get("model")}
    return used or ({evaluation["model"]} if evaluation.get("model") else set())
