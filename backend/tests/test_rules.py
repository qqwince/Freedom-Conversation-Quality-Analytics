"""Детерминированные признаки грубых ошибок и пометка «требует проверки», если модель их пропустила."""

from __future__ import annotations

import json

import pytest

from cqa.evaluator import Evaluator
from cqa.rules import find_critical
from cqa.transcript import extract_facts
from tests.conftest import make_conversation
from tests.test_evaluator import GOOD, FakeProvider, verdict


def hits_for(*operator_lines: str, verified_before: bool = False) -> list[str]:
    rows = [("00:00", "client", "Здравствуйте, вопрос по счёту.")]
    if verified_before:
        rows.append(("00:10", "system", "Личность клиента подтверждена."))
    rows += [(f"00:{20 + i}", "operator", text) for i, text in enumerate(operator_lines)]
    conv = make_conversation(rows)
    return [h.subtype for h in find_critical(conv, extract_facts(conv))]


@pytest.mark.parametrize("line, subtype", [
    ("Вы вообще читать умеете? Бот всё написал.", "insult"),
    ("Can't you read? It's on the screen.", "insult"),
    ("Скиньте деньги мне на карту, я сам вложу.", "bypass"),
    ("Оставьте личный номер, вечером за плату подскажу бумаги.", "bypass"),
    ("Send me your personal number, I charge for private advice.", "bypass"),
    ("Жеке нөміріңізді қалдырсаңыз, ақылы түрде айтып беремін.", "bypass"),
    ("Я посмотрел: на вашем счёте есть ограничение на вывод.", "disclosure"),
    ("I can see a withdrawal restriction on your account.", "disclosure"),
    ("Сіздің шотыңызда ақша шығаруға шектеу қойылғанын көріп тұрмын.", "disclosure"),
    ("Подтверждение пока не нужно.", "disclosure"),
])
def test_finds_each_subtype(line, subtype):
    assert hits_for(line) == [subtype]


@pytest.mark.parametrize("line", [
    "Здравствуйте, я Алина. Тариф указан в разделе «Кабинет».",
    "По этой переписке причину я не вижу. Если нужно проверить именно ваш счёт, понадобится идентификация.",
    "Чтобы проверить ваш счёт, подтвердите личность кодом из SMS.",
    "Leave your phone number and our team will call you back.",
])
def test_ordinary_replies_are_clean(line):
    assert hits_for(line) == []


def test_account_data_after_verification_is_not_disclosure():
    assert hits_for("Я посмотрел: на вашем счёте есть ограничение на вывод.", verified_before=True) == []


def test_model_miss_is_flagged_for_review(checklist):
    conv = make_conversation([
        ("00:00", "client", "Где найти облигации?"),
        ("00:10", "operator", "Здравствуйте, я Марк. Откройте «Котировки»."),
        ("00:30", "operator", "Можете дать личный номер? Вечером за плату объясню, какие бумаги взять."),
    ])
    answer = json.dumps({"results": [verdict(cid, a, [1]) for cid, a in GOOD.items()]})   # реплики диалога 0–2
    evaluation = Evaluator(FakeProvider([answer]), votes=1).evaluate(conv, checklist)
    c8 = next(r for r in evaluation["results"] if r["criterion_id"] == "C8")
    assert c8["answer"] == "no" and c8["needs_review"]                 # ответ модели не меняется
    assert any("услуга в обход компании — реплика 3" in n for n in c8["review_notes"])
