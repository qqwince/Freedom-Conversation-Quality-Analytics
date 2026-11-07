"""Проверка ответа модели по чек-листу и транскрипту.

Ошибки (severity=error) — ответ нельзя использовать как есть: модель переспрашиваем.
Предупреждения (severity=warning) — ответ допустим, но критерий помечается «требует проверки».

Дополнительные правила подключаются к критерию полем `validators` в чек-листе, поэтому
при замене критерия они не срабатывают на чужих данных.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from cqa.models import Conversation, CriterionVerdict, Evidence, LLMEvaluation
from cqa.scoring import NA, NO, YES, Answer, ScoringError, normalize_answer
from cqa.transcript import ConversationFacts, EventType

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Issue:
    criterion_id: str | None
    severity: Severity
    message: str

    def __str__(self) -> str:
        prefix = f"{self.criterion_id}: " if self.criterion_id else ""
        return prefix + self.message


@dataclass(frozen=True)
class ValidationResult:
    verdicts: dict[str, CriterionVerdict]
    answers: dict[str, Answer]          # нормализованные ответы для критериев без ошибок
    issues: list[Issue]

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]


RuleFn = Callable[[CriterionVerdict, Answer, Conversation, ConversationFacts], str | None]


def _rule_identity_event(v: CriterionVerdict, answer: Answer, conv: Conversation,
                         facts: ConversationFacts) -> str | None:
    if answer == YES and not facts.events_of(EventType.IDENTITY_VERIFIED):
        return "«Да», но системного подтверждения личности в диалоге нет — проверить вручную"
    return None


def _rule_closing_interrupted(v: CriterionVerdict, answer: Answer, conv: Conversation,
                              facts: ConversationFacts) -> str | None:
    if answer == NO and facts.ended_by in (EventType.CLIENT_LEFT, EventType.CONNECTION_LOST):
        return "«Нет», но после последней реплики оператора клиент ушёл или связь прервалась — проверить N/A"
    return None


RULES: dict[str, RuleFn] = {
    "identity_event": _rule_identity_event,
    "closing_interrupted": _rule_closing_interrupted,
}


def validate(llm_eval: LLMEvaluation, checklist: Mapping[str, Any], conv: Conversation,
             facts: ConversationFacts, criteria_ids: list[str] | None = None) -> ValidationResult:
    criteria = {c["id"]: c for c in checklist["criteria"]}
    expected = list(criteria_ids) if criteria_ids is not None else list(criteria)
    issues: list[Issue] = []

    counts = Counter(v.criterion_id for v in llm_eval.results)
    for cid, n in counts.items():
        if cid not in expected:
            issues.append(Issue(cid, "error", "критерия нет в списке для оценки"))
        elif n > 1:
            issues.append(Issue(cid, "error", f"критерий оценён {n} раза"))
    verdicts = {v.criterion_id: v for v in llm_eval.results if v.criterion_id in expected}
    issues += [Issue(cid, "error", "нет оценки по критерию") for cid in expected if cid not in verdicts]

    speakers = {m.idx: m.speaker for m in conv.transcript}
    answers: dict[str, Answer] = {}
    for cid, verdict in verdicts.items():
        criterion = criteria[cid]
        try:
            answer = normalize_answer(criterion, verdict.answer)
        except ScoringError as exc:
            issues.append(Issue(cid, "error", str(exc).removeprefix(f"{cid}: ")))
            continue
        _fill_evidence_from_checks(verdict, speakers)
        criterion_issues = _check_verdict(criterion, verdict, answer, speakers)
        for rule_name in criterion.get("validators", []):
            rule = RULES.get(rule_name)
            if rule and (message := rule(verdict, answer, conv, facts)):
                criterion_issues.append(Issue(cid, "warning", message))
        issues += criterion_issues
        if not any(i.severity == "error" for i in criterion_issues):
            answers[cid] = answer

    return ValidationResult(verdicts=verdicts, answers=answers, issues=issues)


def _fill_evidence_from_checks(v: CriterionVerdict, speakers: Mapping[int, str]) -> None:
    """Некоторые модели указывают реплики только в подпунктах. Тогда доказательства берутся оттуда:
    без них в интерфейсе не будет перехода к нужному сообщению."""
    if v.evidence:
        return
    seen: dict[int, str] = {}
    for check in v.checks:
        for idx in check.msg:
            if idx in speakers and idx not in seen:
                seen[idx] = check.q
    v.evidence = [Evidence(msg_idx=idx, proves=q) for idx, q in sorted(seen.items())]


def _check_verdict(criterion: Mapping[str, Any], v: CriterionVerdict, answer: Answer,
                   speakers: Mapping[int, str]) -> list[Issue]:
    cid = criterion["id"]
    issues: list[Issue] = []

    bad_refs = sorted({e.msg_idx for e in v.evidence if e.msg_idx not in speakers}
                      | {i for i in (v.expected_from_msg, v.expected_to_msg) if i is not None and i not in speakers})
    if bad_refs:
        issues.append(Issue(cid, "error", f"ссылки на несуществующие реплики: {bad_refs}"))

    if answer == NA and not (v.na_reason or "").strip():
        issues.append(Issue(cid, "warning", "N/A без объяснения причины"))

    if criterion["type"] == "flag":
        if answer == YES:
            allowed = {s["id"] for s in criterion.get("subtypes", [])}
            unknown = [s for s in v.flag_subtypes if s not in allowed]
            if unknown:
                issues.append(Issue(cid, "error", f"неизвестные подтипы: {unknown}"))
            if allowed and not v.flag_subtypes:
                issues.append(Issue(cid, "error", "флаг «Да» без указания подтипа"))
            if not any(speakers.get(e.msg_idx) == "operator" for e in v.evidence):
                issues.append(Issue(cid, "error", "флаг «Да» без реплики оператора в доказательствах"))
    elif answer != NA and not v.evidence and v.expected_from_msg is None:
        issues.append(Issue(cid, "warning", "нет ни доказательства, ни указанного участка"))

    if not v.explanation.strip():
        issues.append(Issue(cid, "warning", "пустое объяснение"))
    return issues
