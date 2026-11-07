"""Аналитика: соблюдение критериев, честный рост балла, рейтинг, сводка оператору."""

from __future__ import annotations

from typing import Any

from cqa.analytics import Row, build_dashboard, criterion_share, operator_profile, potential_gain
from cqa.feedback import build_feedback, template_message
from cqa.scoring import score

BASE = {"C1": "yes", "C2": "na", "C3": "na", "C4": 15, "C5": "yes", "C6": 7, "C7": "no", "C8": "no"}


def row(cid: str, operator: str, answers: dict[str, Any], checklist, critical: bool = False) -> Row:
    total = float(score(checklist, answers).total)
    return Row(cid, operator, "ru", "Линия RU", answers, total, critical, ["insult"] if critical else [],
               "оскорбил клиента" if critical else "", False)


def test_criterion_share(checklist):
    c4 = next(c for c in checklist["criteria"] if c["id"] == "C4")
    c8 = next(c for c in checklist["criteria"] if c["id"] == "C8")
    assert criterion_share(c4, 15) == 0.75 and criterion_share(c4, "na") is None
    assert criterion_share(c8, "yes") == 0.0 and criterion_share(c8, "no") == 1.0


def test_potential_gain_is_real_score_difference(checklist):
    r = row("a", "Алина", BASE, checklist)
    # C7 «Нет» → «Да»: +5 из применимых 65 → +7.69 к итогу
    assert potential_gain([r], checklist, "C7") == 7.69
    # N/A не «улучшается»
    assert potential_gain([r], checklist, "C2") == 0.0


def test_operator_profile_and_template(checklist):
    rows = [row("a", "Алина", BASE, checklist), row("b", "Тимур", dict(BASE, C6=10, C7="yes"), checklist)]
    profile = operator_profile(rows, checklist, "Алина")
    assert profile["dialogs"] == 1 and profile["place"] == 2 and profile["of"] == 2
    assert [w["criterion_id"] for w in profile["weak"]] == ["C4", "C7", "C6"]
    assert profile["potential_score"] == 100.0
    lines = template_message(profile).splitlines()
    assert lines[0].startswith("Добрый день, Алина!") and lines[1] == "Что стоит улучшить?"
    assert [line[:2] for line in lines[2:5]] == ["А)", "Б)", "В)"]
    assert "+7.7 к среднему баллу" in lines[3]
    assert lines[-1].startswith("Если поработать над этим, средний балл может вырасти")


def test_feedback_falls_back_to_template_without_model(checklist):
    profile = operator_profile([row("a", "Алина", BASE, checklist)], checklist, "Алина")
    assert build_feedback(profile, None)["source"] == "template"


class _FakeProvider:
    model = "fake"

    def __init__(self, items: list[str]) -> None:
        self.items = items

    def generate_json(self, system, turns, schema):
        import json

        from cqa.llm import LLMResult
        return LLMResult(json.dumps({"greeting": "Здравствуйте, Алина!", "items": self.items}), "fake", 1, 1, 0.1)


def test_feedback_uses_model_wording_but_code_numbers(checklist):
    profile = operator_profile([row("a", "Алина", BASE, checklist)], checklist, "Алина")
    n = len(profile["weak"])
    fb = build_feedback(profile, lambda: _FakeProvider([f"{i + 1}. Совет номер {i + 1}" for i in range(n)]))
    lines = fb["message"].splitlines()
    assert fb["source"] == "fake" and lines[0] == "Здравствуйте, Алина!"
    assert lines[2] == f"А) Совет номер 1 — +{profile['weak'][0]['gain']:.1f} к среднему баллу."
    # «В конце…» не теряет букву: срезается только нумерация «А)», «1.»
    fb = build_feedback(profile, lambda: _FakeProvider(["В конце подводите итог"] + ["А) Совет"] * (n - 1)))
    assert fb["message"].splitlines()[2].startswith("А) В конце подводите итог — +")
    assert fb["message"].splitlines()[3].startswith("Б) Совет — +")
    # модель вернула не столько пунктов — используется шаблон
    assert build_feedback(profile, lambda: _FakeProvider(["один"]))["source"] == "template"


def test_dashboard(checklist):
    rows = [row("a", "Алина", BASE, checklist), row("b", "Тимур", dict(BASE, C8="yes"), checklist, critical=True)]
    kpi = {"quality_targets": {"avg_score": 85, "critical_errors": 0, "criterion_compliance": 90}}
    d = build_dashboard(rows, checklist, kpi, open_appeals=1, total_dialogs=3)
    assert d["summary"]["evaluated"] == 2 and d["summary"]["dialogs"] == 3 and d["summary"]["critical"] == 1
    assert d["critical"][0]["conversation_id"] == "b" and d["critical"][0]["reason"] == "оскорбил клиента"
    assert d["departments"][0]["operators"] == 2 and "workload" not in d["departments"][0]   # только реальные данные
    assert sum(b["count"] for b in d["distribution"]) == 2
    c7 = next(c for c in d["criteria"] if c["criterion_id"] == "C7")
    assert c7["compliance"] == 0.0 and c7["gain"] == 7.69
