"""Черновик критерия по названию и описанию: модель предлагает, код приводит к правилам чек-листа."""

from __future__ import annotations

import json

import pytest

from cqa.criterion_draft import CriterionDraft, draft_criterion, next_criterion_id, normalize_draft
from cqa.llm import LLMResult
from cqa.service import validate_checklist


class FakeProvider:
    model = "fake"

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.prompt = ""

    def generate_json(self, system, turns, schema):
        self.prompt = turns[0].text
        return LLMResult(json.dumps(self.payload), "fake", 10, 10, 0.1)


PROMO = {
    "type": "binary", "section": "S3", "description": "Оператор рассказал о текущей акции",
    "na_allowed": True, "na_rule": "Клиент не спрашивал о тарифах и комиссиях",
    "checks": ["Спрашивал ли клиент о тарифах?", "Упомянул ли оператор акцию? Укажите реплику."],
    "decision": "Да — акция упомянута; Нет — не упомянута; N/A — разговор не о тарифах.",
}


def test_draft_is_a_valid_new_criterion(checklist):
    provider = FakeProvider(PROMO)
    c = draft_criterion(checklist, "Рассказать про промо", "Оператор упомянул акцию", 5, lambda: provider)
    assert c["id"] == "C9" and c["title"] == "Рассказать про промо" and c["weight"] == 5
    assert c["section"] == "S3" and c["na_allowed"] and c["generated"]
    assert "Рассказать про промо" in provider.prompt and '"S1"' in provider.prompt   # секции и пример в промпте
    checklist["criteria"].append(c)
    assert validate_checklist(checklist) == []


def test_scale_steps_are_repaired(checklist):
    draft = CriterionDraft(**{**PROMO, "type": "scale", "steps": [{"value": 4, "text": "полностью"},
                                                                  {"value": 4, "text": "повтор"}]})
    c = normalize_draft(checklist, "Новый", 10, draft)
    assert [s["value"] for s in c["steps"]] == [4, 0]           # дубль убран, нулевая ступень добавлена


def test_flag_has_no_weight_and_no_na(checklist):
    c = normalize_draft(checklist, "Угрозы клиенту", 10, CriterionDraft(**{**PROMO, "type": "flag"}))
    assert c["weight"] == 0 and c["na_allowed"] is False and c["is_critical_flag"] is True


def test_unknown_section_falls_back(checklist):
    c = normalize_draft(checklist, "Новый", 10, CriterionDraft(**{**PROMO, "section": "S99"}))
    assert c["section"] in {s["id"] for s in checklist["sections"]}


def test_empty_input_rejected(checklist):
    with pytest.raises(ValueError):
        draft_criterion(checklist, " ", "описание", 5, lambda: FakeProvider(PROMO))


def test_next_id_skips_used(checklist):
    checklist["criteria"].append({**checklist["criteria"][0], "id": "C12"})
    assert next_criterion_id(checklist) == "C13"
