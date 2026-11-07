"""Оценка диалога: промпт → модель → проверка → (повтор) → голосование → расчёт баллов.

Результат — словарь по контракту Evaluation (docs/contracts.md), готовый для хранения и API,
с журналом всех вызовов модели: по нему видно, что оценка получена на этом транскрипте.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from cqa.config import get_settings
from cqa.llm import ChatTurn, LLMError, LLMProvider, LLMResult
from cqa.models import Conversation, CriterionVerdict, LLMEvaluation
from cqa.prompt import Prompt, build_prompt
from cqa.scoring import Answer, ScoringError, score
from cqa.transcript import ConversationFacts, extract_facts
from cqa.validation import Issue, validate

log = logging.getLogger(__name__)

DEFAULT_WAIT_THRESHOLD_SEC = 120


@dataclass
class CallRecord:
    vote: int
    attempt: int
    model: str
    input_tokens: int
    output_tokens: int
    latency_sec: float
    response: str
    issues: list[str]

    @classmethod
    def from_result(cls, vote: int, attempt: int, result: LLMResult, issues: Sequence[Issue]) -> CallRecord:
        return cls(vote, attempt, result.model, result.input_tokens, result.output_tokens,
                   round(result.latency_sec, 2), result.text, [str(i) for i in issues])


@dataclass
class VoteResult:
    verdicts: dict[str, CriterionVerdict]
    answers: dict[str, Answer]
    warnings: dict[str, list[str]]
    failed: dict[str, list[str]]   # критерии, по которым после повтора остались ошибки


@dataclass
class CriterionOutcome:
    criterion_id: str
    answer: Answer | None              # None — модель так и не дала допустимый ответ
    verdict: CriterionVerdict | None   # объяснение и доказательства из голоса-победителя
    votes: list[Answer]
    agreement: float                   # доля голосов за итоговый ответ
    review_notes: list[str] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return self.answer is None or self.agreement < 1 or bool(self.review_notes)


class Evaluator:
    def __init__(self, provider: LLMProvider, votes: int | None = None, max_repairs: int = 1) -> None:
        self.provider = provider
        self.votes = votes or get_settings().votes
        self.max_repairs = max_repairs

    def evaluate(self, conv: Conversation, checklist: Mapping[str, Any],
                 criteria_ids: Sequence[str] | None = None, extra_context: str | None = None) -> dict[str, Any]:
        """Полная оценка диалога. criteria_ids — оценить только часть критериев (без итога).
        extra_context — дополнительный раздел промпта (второе мнение по апелляции)."""
        facts = extract_facts(conv, _wait_threshold(checklist))
        prompt = build_prompt(checklist, conv, facts, criteria_ids, extra_context)
        ids = list(criteria_ids) if criteria_ids is not None else [c["id"] for c in checklist["criteria"]]

        calls: list[CallRecord] = []
        votes = [self._run_vote(n, prompt, checklist, conv, facts, ids, calls) for n in range(self.votes)]
        outcomes = {cid: aggregate(cid, votes, _criterion(checklist, cid)) for cid in ids}
        return assemble_evaluation(conv, checklist, outcomes, calls, prompt, self.provider.model,
                                   partial=criteria_ids is not None)

    def _run_vote(self, vote: int, prompt: Prompt, checklist: Mapping[str, Any], conv: Conversation,
                  facts: ConversationFacts, ids: list[str], calls: list[CallRecord]) -> VoteResult:
        turns = [ChatTurn("user", prompt.user)]
        best: VoteResult | None = None
        for attempt in range(self.max_repairs + 1):
            result = self.provider.generate_json(prompt.system, turns, LLMEvaluation)
            try:
                parsed = LLMEvaluation.model_validate_json(result.text)
            except ValidationError as exc:
                issues = [Issue(None, "error", f"ответ не соответствует схеме: {exc.error_count()} ошибок")]
                calls.append(CallRecord.from_result(vote, attempt, result, issues))
                turns += [ChatTurn("model", result.text), ChatTurn("user", _repair_message(issues))]
                continue

            checked = validate(parsed, checklist, conv, facts, ids)
            calls.append(CallRecord.from_result(vote, attempt, result, checked.issues))
            current = VoteResult(
                verdicts=checked.verdicts,
                answers=checked.answers,
                warnings=_group(i for i in checked.issues if i.severity == "warning"),
                failed=_group(checked.errors),
            )
            best = _merge(best, current)
            if not best.failed:
                return best
            turns += [ChatTurn("model", result.text), ChatTurn("user", _repair_message(checked.errors))]

        if best is None:
            raise LLMError("модель не вернула ответ в нужном формате")
        log.warning("%s: ошибки после повторов: %s", conv.conversation_id, best.failed)
        return best


def aggregate(cid: str, votes: Sequence[VoteResult], criterion: Mapping[str, Any]) -> CriterionOutcome:
    """Ответ по большинству голосов; для шкалы без большинства — медианная ступень."""
    valid = [(v, v.answers[cid]) for v in votes if cid in v.answers]
    notes = sorted({n for v in votes for n in v.warnings.get(cid, []) + v.failed.get(cid, [])})
    if not valid:
        return CriterionOutcome(cid, None, None, [], 0.0, notes or ["нет допустимого ответа модели"])

    answers = [a for _, a in valid]
    counts = Counter(answers)
    top, top_count = counts.most_common(1)[0]
    if top_count * 2 <= len(answers) and criterion["type"] == "scale":
        numeric = sorted(a for a in answers if not isinstance(a, str))
        if numeric:
            top = numeric[(len(numeric) - 1) // 2]   # нижняя медиана — всегда существующая ступень
    winner = next(v for v, a in valid if a == top)
    agreement = counts[top] / len(votes)
    if len(votes) > 1 and agreement < 1:
        notes.append(f"голоса разошлись: {', '.join(str(a) for a in answers)}")
    return CriterionOutcome(cid, top, winner.verdicts[cid], answers, agreement, notes)


def assemble_evaluation(conv: Conversation, checklist: Mapping[str, Any],
                        outcomes: Mapping[str, CriterionOutcome], calls: Sequence[CallRecord],
                        prompt: Prompt, model: str, partial: bool = False) -> dict[str, Any]:
    answers = {cid: o.answer for cid, o in outcomes.items() if o.answer is not None}
    flag_subtypes = {cid: o.verdict.flag_subtypes for cid, o in outcomes.items() if o.verdict}

    scoring: dict[str, Any] | None = None
    scoring_error: str | None = None
    if partial:
        scoring_error = "частичная оценка: итог считается после объединения с остальными критериями"
    elif len(answers) < len(outcomes):
        scoring_error = "по некоторым критериям нет допустимого ответа — итог не рассчитан"
    else:
        try:
            scoring = score(checklist, answers, flag_subtypes).to_dict()
        except ScoringError as exc:
            scoring_error = str(exc)
    scored = {c["criterion_id"]: c for c in scoring["criteria"]} if scoring else {}

    results = []
    for cid, o in outcomes.items():
        v = o.verdict
        results.append({
            "criterion_id": cid,
            "answer": o.answer,
            "checks": [c.model_dump() for c in v.checks] if v else [],
            "evidence": [e.model_dump() for e in v.evidence] if v else [],
            "expected_at": ({"from_msg_idx": v.expected_from_msg, "to_msg_idx": v.expected_to_msg}
                            if v and v.expected_from_msg is not None else None),
            "na_reason": v.na_reason if v else None,
            "flag_subtypes": v.flag_subtypes if v else [],
            "explanation": v.explanation if v else "",
            "votes": o.votes,
            "agreement": round(o.agreement, 2),
            "needs_review": o.needs_review,
            "review_notes": o.review_notes,
            **{k: scored[cid][k] for k in ("weight", "effective_weight", "points") if cid in scored},
        })

    settings = get_settings()
    usage = {
        "calls": len(calls),
        "input_tokens": sum(c.input_tokens for c in calls),
        "output_tokens": sum(c.output_tokens for c in calls),
        "latency_sec": round(sum(c.latency_sec for c in calls), 2),
    }
    usage["cost_usd"] = _cost(usage["input_tokens"], usage["output_tokens"],
                              settings.price_input_per_m, settings.price_output_per_m)

    return {
        "id": uuid.uuid4().hex,
        "conversation_id": conv.conversation_id,
        "transcript_sha256": transcript_hash(conv),
        "checklist_id": checklist.get("id"),
        "checklist_version": checklist.get("version"),
        "criteria_ids": list(outcomes),
        "model": model,
        "prompt_version": prompt.version,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "total": scoring["total"] if scoring else None,
        "sections": scoring["sections"] if scoring else [],
        "critical": scoring["critical"] if scoring else None,
        "scoring_error": scoring_error,
        "needs_review": any(o.needs_review for o in outcomes.values()),
        "results": results,
        "usage": usage,
        "log": {
            "system_prompt": prompt.system,
            "user_prompt": prompt.user,
            "calls": [c.__dict__ for c in calls],
        },
    }


def transcript_hash(conv: Conversation) -> str:
    payload = json.dumps([m.model_dump() for m in conv.transcript], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _criterion(checklist: Mapping[str, Any], cid: str) -> Mapping[str, Any]:
    return next(c for c in checklist["criteria"] if c["id"] == cid)


def _wait_threshold(checklist: Mapping[str, Any]) -> float:
    values = [c["wait_threshold_seconds"] for c in checklist["criteria"] if c.get("wait_threshold_seconds")]
    return float(min(values)) if values else DEFAULT_WAIT_THRESHOLD_SEC


def _group(issues: Any) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for issue in issues:
        grouped.setdefault(issue.criterion_id or "*", []).append(issue.message)
    return grouped


def _merge(previous: VoteResult | None, current: VoteResult) -> VoteResult:
    """После повторного запроса оставляем допустимые ответы из обеих попыток (новые важнее)."""
    if previous is None:
        return current

    def source(cid: str) -> VoteResult:
        return current if cid in current.answers else previous

    answers = {**previous.answers, **current.answers}
    verdicts = {cid: source(cid).verdicts[cid] for cid in answers}
    verdicts.update({cid: v for cid, v in current.verdicts.items() if cid not in answers})
    warnings = {cid: w for cid in answers if (w := source(cid).warnings.get(cid))}
    failed = {cid: e for cid, e in current.failed.items() if cid not in answers}
    return VoteResult(verdicts, answers, warnings, failed)


def _repair_message(issues: Sequence[Issue]) -> str:
    listed = "\n".join(f"- {i}" for i in issues)
    return ("В твоём ответе есть ошибки:\n" + listed +
            "\nИсправь их и верни полный JSON по всем критериям заново, соблюдая допустимые ответы.")


def _cost(tokens_in: int, tokens_out: int, price_in: float | None, price_out: float | None) -> float | None:
    if price_in is None or price_out is None:
        return None
    return round(tokens_in / 1e6 * price_in + tokens_out / 1e6 * price_out, 6)
