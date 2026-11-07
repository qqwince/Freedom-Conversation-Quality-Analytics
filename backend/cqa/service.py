"""Прикладной слой: импорт, проверка чек-листа, оценка и переоценка, апелляции, фоновые задачи.

API (cqa/api.py) только переводит HTTP в вызовы этого слоя.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from cqa.dataset import fetch_raw, normalize, normalize_record
from cqa.evaluator import Evaluator
from cqa.llm import LLMError, LLMProvider
from cqa.models import Conversation
from cqa.scoring import ScoringError, normalize_answer, score
from cqa.storage import NotFound, Storage, now
from cqa.validation import RULES

log = logging.getLogger(__name__)


class ServiceError(ValueError):
    """Ошибка запроса пользователя: неверные данные, недопустимое действие."""


# --- Чек-лист ------------------------------------------------------------------------------

def validate_checklist(checklist: Mapping[str, Any]) -> list[str]:
    """Проверка чек-листа перед сохранением. Пустой список — всё в порядке."""
    errors: list[str] = []
    for key in ("id", "name"):
        if not str(checklist.get(key) or "").strip():
            errors.append(f"не задано поле {key}")
    sections = {s.get("id") for s in checklist.get("sections", [])}
    criteria = checklist.get("criteria") or []
    if not criteria:
        errors.append("нет ни одного критерия")

    seen: set[str] = set()
    for c in criteria:
        cid = c.get("id") or "?"
        prefix = f"{cid}: "
        if cid in seen:
            errors.append(prefix + "повторяющийся id")
        seen.add(cid)
        if not str(c.get("title") or "").strip():
            errors.append(prefix + "нет названия")
        if not str(c.get("description") or "").strip():
            errors.append(prefix + "нет формулировки")
        if c.get("section") not in sections:
            errors.append(prefix + f"неизвестная секция {c.get('section')!r}")
        ctype = c.get("type")
        if ctype not in ("binary", "scale", "flag"):
            errors.append(prefix + f"неизвестный тип {ctype!r}")
        weight = c.get("weight")
        if not isinstance(weight, int | float) or weight < 0:
            errors.append(prefix + "вес должен быть числом ≥ 0")
        if ctype == "flag" and weight:
            errors.append(prefix + "у флага грубой ошибки вес должен быть 0")
        if ctype == "scale":
            values = [s.get("value") for s in c.get("steps") or []]
            if len(values) < 2:
                errors.append(prefix + "у шкалы должно быть минимум две ступени")
            elif not all(isinstance(v, int | float) and v >= 0 for v in values):
                errors.append(prefix + "значения ступеней — числа ≥ 0")
            elif len(set(values)) != len(values):
                errors.append(prefix + "значения ступеней повторяются")
        if c.get("na_allowed") and not str(c.get("na_rule") or "").strip():
            errors.append(prefix + "N/A разрешён, но не описано условие N/A")
        unknown_rules = [r for r in c.get("validators", []) if r not in RULES]
        if unknown_rules:
            errors.append(prefix + f"неизвестные проверки {unknown_rules}")

    if criteria and not any((c.get("weight") or 0) > 0 for c in criteria if c.get("type") != "flag"):
        errors.append("нет ни одного критерия с весом")
    return errors


def rescore(evaluation: dict[str, Any], checklist: Mapping[str, Any],
            overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Итог по ответам оценки (с подменой ответов из overrides). Возвращает поля итога."""
    overrides = overrides or {}
    answers = {r["criterion_id"]: overrides.get(r["criterion_id"], r["answer"]) for r in evaluation["results"]}
    subtypes = {r["criterion_id"]: r.get("flag_subtypes", []) for r in evaluation["results"]}
    if any(a is None for a in answers.values()):
        return {"total": None, "sections": [], "critical": None, "criteria": [],
                "scoring_error": "по некоторым критериям нет допустимого ответа"}
    try:
        result = score(checklist, answers, subtypes).to_dict()
    except ScoringError as exc:
        return {"total": None, "sections": [], "critical": None, "criteria": [], "scoring_error": str(exc)}
    return {**result, "scoring_error": None}


# --- Фоновые задачи ------------------------------------------------------------------------

class JobManager:
    """Пакетная оценка в фоне с прогрессом по каждому диалогу."""

    def __init__(self, workers: int) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cqa-eval")
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def submit(self, conversation_ids: Sequence[str], run: Callable[[str], dict[str, Any]],
               meta: Mapping[str, Any]) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        job = {"id": job_id, "status": "running", "created_at": now(), "total": len(conversation_ids),
               "done": 0, "failed": 0, **meta,
               "items": [{"conversation_id": c, "status": "queued", "evaluation_id": None, "error": None,
                          "total": None} for c in conversation_ids]}
        with self._lock:
            self._jobs[job_id] = job
        for item in job["items"]:
            self._pool.submit(self._run_item, job, item, run)
        return self.get(job_id)

    def _run_item(self, job: dict[str, Any], item: dict[str, Any], run: Callable[[str], dict[str, Any]]) -> None:
        self._update(job, item, status="running")
        try:
            evaluation = run(item["conversation_id"])
        except (LLMError, ServiceError, NotFound) as exc:
            self._update(job, item, status="failed", error=str(exc))
        except Exception as exc:  # ошибка одного диалога не должна ронять всю задачу
            log.exception("ошибка оценки %s", item["conversation_id"])
            self._update(job, item, status="failed", error=f"внутренняя ошибка: {exc}")
        else:
            self._update(job, item, status="done", evaluation_id=evaluation["id"], total=evaluation["total"])

    def _update(self, job: dict[str, Any], item: dict[str, Any], **changes: Any) -> None:
        with self._lock:
            item.update(changes)
            job["done"] = sum(i["status"] == "done" for i in job["items"])
            job["failed"] = sum(i["status"] == "failed" for i in job["items"])
            if job["done"] + job["failed"] == job["total"]:
                job["status"] = "finished"
                job["finished_at"] = now()

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if job_id not in self._jobs:
                raise NotFound(f"задача {job_id} не найдена")
            job = self._jobs[job_id]
            return {**job, "items": [dict(i) for i in job["items"]]}

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{k: v for k, v in j.items() if k != "items"} for j in self._jobs.values()]


# --- Сервис --------------------------------------------------------------------------------

class CQAService:
    def __init__(self, storage: Storage, provider_factory: Callable[[str | None], LLMProvider],
                 api_url: str | None, workers: int = 2, default_votes: int = 1) -> None:
        self.storage = storage
        self._provider_factory = provider_factory
        self._api_url = api_url
        self.default_votes = default_votes
        self.jobs = JobManager(workers)

    # Импорт

    def import_group(self, group_id: str) -> dict[str, Any]:
        if not self._api_url:
            raise ServiceError("не задана ссылка API организаторов (CQA_API_URL)")
        group, conversations, warnings = normalize(fetch_raw(self._api_url, group_id))
        self.storage.upsert_conversations(conversations)
        return {"dataset": group, "imported": len(conversations), "warnings": warnings}

    def import_payload(self, payload: Any, dataset: str = "MANUAL") -> dict[str, Any]:
        """Ручная загрузка: ответ API целиком, одна запись или список записей."""
        if isinstance(payload, dict) and "data" in payload:
            group, conversations, warnings = normalize(payload)
        else:
            records = payload if isinstance(payload, list) else [payload]
            if not records or not all(isinstance(r, dict) and r.get("conversation_id") for r in records):
                raise ServiceError("ожидается запись диалога с conversation_id и transcript")
            group, conversations, warnings = dataset, [], []
            for record in records:
                conv, conv_warnings = normalize_record(record, dataset)
                conversations.append(conv)
                warnings += conv_warnings
        if not any(c.transcript for c in conversations):
            raise ServiceError("в загруженных диалогах нет реплик")
        self.storage.upsert_conversations(conversations)
        return {"dataset": group, "imported": len(conversations), "warnings": warnings,
                "conversation_ids": [c.conversation_id for c in conversations]}

    # Чек-листы

    def save_checklist(self, checklist: dict[str, Any]) -> dict[str, Any]:
        errors = validate_checklist(checklist)
        if errors:
            raise ServiceError("; ".join(errors))
        return self.storage.save_checklist(checklist)

    # Оценка

    def evaluate(self, conversation_id: str, checklist_id: str, checklist_version: int | None = None,
                 criteria_ids: Sequence[str] | None = None, base_evaluation_id: str | None = None,
                 votes: int | None = None, model: str | None = None) -> dict[str, Any]:
        """Оценка диалога. С criteria_ids и base_evaluation_id — переоценка только этих критериев:
        остальные ответы берутся из базовой оценки, итог пересчитывается по новому чек-листу."""
        conversation = self.storage.get_conversation(conversation_id)
        checklist = self.storage.get_checklist(checklist_id, checklist_version)
        evaluator = Evaluator(self._provider_factory(model), votes=votes or self.default_votes)

        if criteria_ids is None:
            return self.storage.save_evaluation(evaluator.evaluate(conversation, checklist))

        if base_evaluation_id is None:
            raise ServiceError("для переоценки части критериев нужна базовая оценка")
        base = self.storage.get_evaluation(base_evaluation_id)
        if base["conversation_id"] != conversation_id:
            raise ServiceError("базовая оценка относится к другому диалогу")
        partial = evaluator.evaluate(conversation, checklist, criteria_ids)
        return self.storage.save_evaluation(self._merge(base, partial, checklist))

    def _merge(self, base: dict[str, Any], partial: dict[str, Any], checklist: Mapping[str, Any]) -> dict[str, Any]:
        fresh = {r["criterion_id"]: r for r in partial["results"]}
        previous = {r["criterion_id"]: r for r in base["results"]}
        results = []
        for c in sorted(checklist["criteria"], key=lambda c: c.get("order", 0)):
            if c["id"] in fresh:
                results.append(fresh[c["id"]])
            elif c["id"] in previous:
                results.append({**previous[c["id"]], "carried_from": base["id"]})
            else:
                raise ServiceError(f"критерий {c['id']} новый — его нужно включить в переоценку")
        merged = {**partial, "results": results, "criteria_ids": [r["criterion_id"] for r in results],
                  "base_evaluation_id": base["id"], "reevaluated": list(fresh)}
        scoring = rescore(merged, checklist)
        by_id = {c["criterion_id"]: c for c in scoring.get("criteria", [])}
        for r in results:
            for key in ("weight", "effective_weight", "points"):
                r.pop(key, None)
                if r["criterion_id"] in by_id:
                    r[key] = by_id[r["criterion_id"]][key]
        merged.update(total=scoring["total"], sections=scoring["sections"], critical=scoring["critical"],
                      scoring_error=scoring["scoring_error"],
                      needs_review=any(r.get("needs_review") for r in results))
        return merged

    def start_batch(self, conversation_ids: Sequence[str], checklist_id: str, checklist_version: int | None = None,
                    criteria_ids: Sequence[str] | None = None, votes: int | None = None,
                    model: str | None = None) -> dict[str, Any]:
        if not conversation_ids:
            raise ServiceError("не выбраны диалоги")
        checklist = self.storage.get_checklist(checklist_id, checklist_version)
        latest = self.storage.latest_evaluations() if criteria_ids else {}

        def run(conversation_id: str) -> dict[str, Any]:
            base = latest.get(conversation_id)
            if criteria_ids and base is None:
                # нет базовой оценки — оцениваем полностью
                return self.evaluate(conversation_id, checklist_id, checklist["version"], votes=votes, model=model)
            return self.evaluate(conversation_id, checklist_id, checklist["version"], criteria_ids,
                                 base["id"] if base else None, votes, model)

        return self.jobs.submit(list(conversation_ids), run, {
            "checklist_id": checklist_id, "checklist_version": checklist["version"],
            "criteria_ids": list(criteria_ids) if criteria_ids else None,
        })

    # Апелляции и итог

    def create_appeal(self, evaluation_id: str, criterion_id: str, comment: str) -> dict[str, Any]:
        evaluation = self.storage.get_evaluation(evaluation_id)
        if criterion_id not in {r["criterion_id"] for r in evaluation["results"]}:
            raise ServiceError(f"в оценке нет критерия {criterion_id}")
        if not comment.strip():
            raise ServiceError("добавьте комментарий к апелляции")
        if any(a["criterion_id"] == criterion_id
               for a in self.storage.list_appeals(status="open", evaluation_id=evaluation_id)):
            raise ServiceError("по этому критерию уже есть открытая апелляция")
        return self.storage.create_appeal(evaluation_id, criterion_id, comment.strip())

    def decide_appeal(self, appeal_id: str, action: str, reason: str, new_answer: Any = None) -> dict[str, Any]:
        appeal = self.storage.get_appeal(appeal_id)
        if appeal["status"] != "open":
            raise ServiceError("апелляция уже рассмотрена")
        if not reason.strip():
            raise ServiceError("укажите причину решения")
        evaluation = self.storage.get_evaluation(appeal["evaluation_id"])
        original = next(r["answer"] for r in evaluation["results"] if r["criterion_id"] == appeal["criterion_id"])
        if action == "confirm":
            return self.storage.decide_appeal(appeal_id, "confirmed",
                                              {"new_answer": original, "reason": reason.strip()})
        if action != "change":
            raise ServiceError("решение: confirm или change")
        checklist = self.storage.get_checklist(evaluation["checklist_id"], evaluation["checklist_version"])
        criterion = next(c for c in checklist["criteria"] if c["id"] == appeal["criterion_id"])
        try:
            answer = normalize_answer(criterion, new_answer)
        except ScoringError as exc:
            raise ServiceError(str(exc)) from exc
        if answer == original:
            raise ServiceError("новая оценка совпадает с исходной — выберите «Подтвердить»")
        return self.storage.decide_appeal(appeal_id, "changed", {"new_answer": answer, "reason": reason.strip()})

    def final_result(self, evaluation_id: str) -> dict[str, Any]:
        """Исходная оценка, решения по апелляциям и финальный итог после них."""
        evaluation = self.storage.get_evaluation(evaluation_id)
        appeals = self.storage.list_appeals(evaluation_id=evaluation_id)
        overrides = {a["criterion_id"]: a["decision"]["new_answer"] for a in appeals if a["status"] == "changed"}
        checklist = self.storage.get_checklist(evaluation["checklist_id"], evaluation["checklist_version"])
        final = rescore(evaluation, checklist, overrides)
        return {
            "evaluation_id": evaluation_id,
            "original": {"total": evaluation["total"], "sections": evaluation["sections"],
                         "critical": evaluation["critical"]},
            "appeals": appeals,
            "final": {
                "total": final["total"], "sections": final["sections"], "critical": final["critical"],
                "scoring_error": final["scoring_error"],
                "answers": {r["criterion_id"]: overrides.get(r["criterion_id"], r["answer"])
                            for r in evaluation["results"]},
                "changed": sorted(overrides),
                "criteria": final.get("criteria", []),
            },
        }


def conversation_summary(conv: Conversation, evaluation: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        "conversation_id": conv.conversation_id,
        "dataset": conv.dataset_group_id,
        "channel": conv.channel,
        "language": conv.language,
        "messages": len(conv.transcript),
        "has_audio": bool(conv.audio_url),
        "evaluation": None if evaluation is None else {
            "id": evaluation["id"], "total": evaluation["total"], "created_at": evaluation["created_at"],
            "checklist_version": evaluation.get("checklist_version"), "model": evaluation.get("model"),
            "needs_review": evaluation.get("needs_review"),
            "critical": bool(evaluation.get("critical") and evaluation["critical"].get("flag")),
        },
    }
