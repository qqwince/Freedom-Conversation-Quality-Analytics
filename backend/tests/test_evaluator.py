"""Оценка целиком с подставной моделью: повтор при ошибках, голосование, итог, журнал."""

from __future__ import annotations

import json
from typing import Any

from cqa.evaluator import Evaluator
from cqa.llm import ChatTurn, LLMResult
from cqa.models import LLMEvaluation
from cqa.transcript import extract_facts
from cqa.validation import validate

GOOD = {"C1": "yes", "C2": "yes", "C3": "yes", "C4": "15", "C5": "yes", "C6": "7", "C7": "yes", "C8": "no"}


def verdict(cid: str, answer: str, evidence: list[int] | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "criterion_id": cid, "checks": [{"q": "?", "a": "ok", "msg": evidence or [5]}], "answer": answer,
        "evidence": [{"msg_idx": i, "proves": "…"} for i in (evidence or [5])],
        "explanation": f"{cid} — {answer}.", **extra,
    }


def response(answers: dict[str, str], **overrides: dict[str, Any]) -> str:
    return json.dumps({"results": [overrides.get(cid, verdict(cid, a)) for cid, a in answers.items()]})


class FakeProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.turns_seen: list[list[ChatTurn]] = []

    def generate_json(self, system: str, turns: list[ChatTurn], schema: type) -> LLMResult:
        self.turns_seen.append(list(turns))
        return LLMResult(self.responses.pop(0), self.model, 1000, 200, 0.1)


def test_happy_path(checklist, conversation):
    provider = FakeProvider([response(GOOD)])
    ev = Evaluator(provider, votes=1).evaluate(conversation, checklist)
    assert ev["total"] == 90.5          # 10+15+20+15+15+10.5+5
    assert ev["critical"] == {"flag": False, "subtypes": []}
    assert not ev["needs_review"]
    usage = {k: v for k, v in ev["usage"].items() if k != "cost_usd"}   # цена зависит от настроек
    assert usage == {"calls": 1, "input_tokens": 1000, "output_tokens": 200, "latency_sec": 0.1}
    assert ev["log"]["calls"][0]["response"]   # сырой ответ модели сохранён
    assert len(ev["transcript_sha256"]) == 64


def test_repair_on_invalid_answer(checklist, conversation):
    bad = response(dict(GOOD, C4="12"))           # 12 — не ступень шкалы
    provider = FakeProvider([bad, response(GOOD)])
    ev = Evaluator(provider, votes=1).evaluate(conversation, checklist)
    assert ev["total"] == 90.5
    assert len(provider.turns_seen) == 2
    repair_request = provider.turns_seen[1][-1].text
    assert "C4" in repair_request and "не является ступенью" in repair_request


def test_repair_keeps_valid_answers_from_first_attempt(checklist, conversation):
    first = response(dict(GOOD, C4="12"))
    second = response(dict(GOOD, C1="no", C4="20"))
    ev = Evaluator(FakeProvider([first, second]), votes=1).evaluate(conversation, checklist)
    by_id = {r["criterion_id"]: r for r in ev["results"]}
    assert by_id["C1"]["answer"] == "no"          # второй ответ важнее
    assert by_id["C4"]["answer"] == 20


def test_unrecoverable_answer_leaves_total_empty(checklist, conversation):
    bad = response(dict(GOOD, C1="na"))           # N/A для C1 запрещён
    ev = Evaluator(FakeProvider([bad, bad]), votes=1).evaluate(conversation, checklist)
    assert ev["total"] is None
    assert ev["scoring_error"]
    c1 = next(r for r in ev["results"] if r["criterion_id"] == "C1")
    assert c1["answer"] is None and c1["needs_review"]


def test_schema_error_is_repaired(checklist, conversation):
    provider = FakeProvider(['{"results": "oops"}', response(GOOD)])
    assert Evaluator(provider, votes=1).evaluate(conversation, checklist)["total"] == 90.5


def test_majority_vote_and_agreement(checklist, conversation):
    provider = FakeProvider([response(GOOD), response(dict(GOOD, C6="4")), response(GOOD)])
    ev = Evaluator(provider, votes=3).evaluate(conversation, checklist)
    c6 = next(r for r in ev["results"] if r["criterion_id"] == "C6")
    assert c6["answer"] == 7
    assert c6["votes"] == [7, 4, 7]
    assert c6["agreement"] == 0.67
    assert c6["needs_review"]
    assert ev["total"] == 90.5


def test_scale_without_majority_takes_median_step(checklist, conversation):
    provider = FakeProvider([response(dict(GOOD, C4=v)) for v in ("20", "10", "15")])
    ev = Evaluator(provider, votes=3).evaluate(conversation, checklist)
    assert next(r for r in ev["results"] if r["criterion_id"] == "C4")["answer"] == 15


def test_critical_flag_requires_operator_evidence(checklist, conversation):
    flagged = verdict("C8", "yes", evidence=[0], flag_subtypes=["insult"])   # [0] — реплика клиента
    parsed = LLMEvaluation.model_validate_json(response(dict(GOOD, C8="yes"), C8=flagged))
    result = validate(parsed, checklist, conversation, extract_facts(conversation))
    assert any(i.criterion_id == "C8" and i.severity == "error" for i in result.issues)


def test_identity_rule_warns_without_system_event(checklist):
    from tests.conftest import CHAT, make_conversation
    no_id = make_conversation([row for row in CHAT if "Личность" not in row[2]])
    parsed = LLMEvaluation.model_validate_json(response(GOOD))
    result = validate(parsed, checklist, no_id, extract_facts(no_id))
    assert [i.severity for i in result.issues if i.criterion_id == "C3"] == ["warning"]


def test_evidence_filled_from_checks_when_model_omits_it(checklist, conversation):
    no_evidence = verdict("C1", "yes")
    no_evidence["evidence"] = []
    no_evidence["checks"] = [{"q": "Первая реплика оператора?", "a": "[5]", "msg": [5, 99]}]
    parsed = LLMEvaluation.model_validate_json(response(GOOD, C1=no_evidence))
    result = validate(parsed, checklist, conversation, extract_facts(conversation))
    assert [(e.msg_idx, e.proves) for e in result.verdicts["C1"].evidence] == [(5, "Первая реплика оператора?")]
    assert not [i for i in result.issues if i.criterion_id == "C1"]


def test_partial_evaluation_has_no_total(checklist, conversation):
    provider = FakeProvider([response({"C7": "yes"})])
    ev = Evaluator(provider, votes=1).evaluate(conversation, checklist, criteria_ids=["C7"])
    assert ev["total"] is None
    assert [r["criterion_id"] for r in ev["results"]] == ["C7"]
