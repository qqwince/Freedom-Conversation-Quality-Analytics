"""Выгрузка results.json по формату 1.0 организаторов."""

from __future__ import annotations

import copy
from typing import Any

from cqa.export import build_results, export_record, to_value, validate_results

ANSWERS = {"C1": "yes", "C2": "na", "C3": "na", "C4": 15, "C5": "yes", "C6": 7, "C7": "yes", "C8": "no"}


def evaluation(answers: dict[str, Any] = ANSWERS, total: float | None = 85.38) -> dict[str, Any]:
    return {
        "id": "ev1", "conversation_id": "p1-ru-001", "checklist_id": "chat-default", "checklist_version": 1,
        "model": "deepseek-flash", "total": total, "scoring_error": None,
        "results": [{"criterion_id": cid, "answer": a, "explanation": f"{cid} — объяснение",
                     "evidence": [{"msg_idx": 9, "proves": "…"}], "checks": [], "expected_at": None}
                    for cid, a in answers.items()],
        "log": {"calls": [{"model": "deepseek-flash"}]},
    }


def test_values_mapping():
    assert [to_value(v) for v in ("yes", "no", "na", 15, 7.0)] == [True, False, None, 15, 7]


def test_record_matches_format(checklist):
    record = export_record("p1-ru-001", evaluation(), checklist)
    assert record["status"] == "ok"
    codes = [c["criterion_id"] for c in record["criteria"]]
    assert codes == ["contact", "discovery", "identification", "consultation", "empathy", "dialogue",
                     "closure", "critical"]
    by_code = {c["criterion_id"]: c for c in record["criteria"]}
    assert by_code["discovery"]["value"] is None
    assert by_code["consultation"]["value"] == 15
    assert by_code["contact"]["evidence_turns"] == [10]          # idx 9 → номер реплики 10
    assert record["score"] == {"final_score": 85.38, "critical_flag": False}


def test_evidence_falls_back_to_checks_then_range(checklist):
    ev = evaluation()
    ev["results"][0]["evidence"] = []
    ev["results"][0]["checks"] = [{"q": "?", "a": "!", "msg": [3, 1]}]
    ev["results"][6]["evidence"] = []
    ev["results"][6]["expected_at"] = {"from_msg_idx": 4, "to_msg_idx": 6}
    record = export_record("p1-ru-001", ev, checklist)
    assert record["criteria"][0]["evidence_turns"] == [2, 4]
    assert record["criteria"][6]["evidence_turns"] == [5, 6, 7]


def test_critical_flag_follows_criterion(checklist):
    record = export_record("x", evaluation(dict(ANSWERS, C8="yes")), checklist)
    assert record["score"]["critical_flag"] is True
    assert record["criteria"][-1]["value"] is True


def test_missing_answer_becomes_error(checklist):
    record = export_record("x", evaluation(dict(ANSWERS, C4=None)), checklist)
    assert record == {"conversation_id": "x", "status": "error", "criteria": [], "score": None,
                      "error": "Нет допустимого ответа модели по критериям: C4."}


def test_final_after_appeal_is_exported(checklist):
    final = {"answers": dict(ANSWERS, C6=4), "total": 80.0}
    record = export_record("x", evaluation(), checklist, final)
    assert next(c for c in record["criteria"] if c["criterion_id"] == "dialogue")["value"] == 4
    assert record["score"]["final_score"] == 80.0


def test_build_and_validate(checklist):
    evaluations = {"a": evaluation(), "b": None}
    doc = build_results(["a", "b"], evaluations.get, lambda ev: checklist, lambda ev: None,
                        team_name="CQA Team", model_mode="external_api")
    assert doc["schema_version"] == "1.0" and doc["model"] == {"mode": "external_api", "name": "deepseek-flash"}
    assert [r["status"] for r in doc["results"]] == ["ok", "error"]
    assert validate_results(doc, ["a", "b"]) == []


def test_validate_catches_format_errors(checklist):
    doc = build_results(["a"], {"a": evaluation()}.get, lambda ev: checklist, lambda ev: None,
                        team_name="T", model_mode="external_api")
    bad = copy.deepcopy(doc)
    bad["results"].append(copy.deepcopy(bad["results"][0]))
    bad["results"][0]["criteria"][0]["value"] = 1                 # True ≠ 1 для формата
    bad["results"][0]["criteria"][5]["value"] = 5                 # нет такой ступени у dialogue
    bad["results"][0]["criteria"][1]["evidence_turns"] = [0]      # нумерация с 1
    bad["results"][0]["score"]["critical_flag"] = True
    problems = validate_results(bad, ["a", "c"])
    assert any("повторяющиеся ID" in p for p in problems)
    assert any("нет записей для: ['c']" in p for p in problems)
    assert any("contact = 1" in p for p in problems)
    assert any("dialogue = 5" in p for p in problems)
    assert any("evidence_turns" in p for p in problems)
    assert any("critical_flag" in p for p in problems)


def test_validate_requires_exactly_the_eight_codes(checklist):
    """Заменённый критерий с новым кодом — не формат 1.0: файл с ним не должен уйти в prod молча."""
    doc = build_results(["a"], {"a": evaluation()}.get, lambda ev: checklist, lambda ev: None,
                        team_name="T", model_mode="external_api")
    replaced = copy.deepcopy(doc)
    replaced["results"][0]["criteria"][4]["criterion_id"] = "C9"          # empathy заменили новым критерием
    problems = validate_results(replaced, ["a"])
    assert any("нет: empathy" in p and "лишние: C9" in p for p in problems)
    short = copy.deepcopy(doc)
    del short["results"][0]["criteria"][2]
    assert any("нет: identification" in p for p in validate_results(short, ["a"]))
