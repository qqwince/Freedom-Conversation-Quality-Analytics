"""Тесты расчёта баллов по правилам чек-листа (стр. 2 PDF).

Запуск из корня проекта:
    python -m unittest discover -s backend/tests -t backend
"""

import copy
import json
import unittest
from decimal import Decimal
from pathlib import Path

from cqa.scoring import ScoringError, apply_decisions, normalize_answer, score

ROOT = Path(__file__).resolve().parents[2]
CHECKLIST = json.loads((ROOT / "config" / "checklist.default.json").read_text(encoding="utf-8"))

ALL_BEST = {"C1": "yes", "C2": "yes", "C3": "yes", "C4": 20, "C5": "yes", "C6": 10, "C7": "yes", "C8": "no"}
ALL_WORST = {"C1": "no", "C2": "no", "C3": "no", "C4": 0, "C5": "no", "C6": 0, "C7": "no", "C8": "no"}
# Пример из плана: C2 и C3 — N/A, применимый вес 65.
EXAMPLE = {"C1": "yes", "C2": "na", "C3": "na", "C4": 15, "C5": "yes", "C6": 7, "C7": "yes", "C8": "no"}


def mini_checklist(criteria, max_total=100):
    return {
        "scoring": {"max_total": max_total, "rounding_digits": 2},
        "sections": [{"id": "S", "title": "S", "order": 1}],
        "criteria": [dict({"section": "S", "na_allowed": True, "order": i}, **c) for i, c in enumerate(criteria)],
    }


class DefaultChecklistTest(unittest.TestCase):
    def test_weights_sum_to_100(self):
        self.assertEqual(sum(c["weight"] for c in CHECKLIST["criteria"]), 100)

    def test_all_best_is_100(self):
        self.assertEqual(score(CHECKLIST, ALL_BEST).total, Decimal("100.00"))

    def test_all_worst_is_0(self):
        self.assertEqual(score(CHECKLIST, ALL_WORST).total, Decimal("0.00"))

    def test_example_with_na_redistribution(self):
        r = score(CHECKLIST, EXAMPLE)
        # 10 + 15 + 15 + 10.5 + 5 = 55.5 из 65 → 85.3846…
        self.assertEqual(r.applicable_weight, Decimal(65))
        self.assertEqual(r.total, Decimal("85.38"))

    def test_na_redistributes_full_weight(self):
        answers = dict(ALL_BEST, C2="na", C3="na", C4="na", C7="na")
        r = score(CHECKLIST, answers)
        self.assertEqual(r.total, Decimal("100.00"))
        eff = sum(c.effective_weight for c in r.criteria if c.applicable)
        self.assertAlmostEqual(float(eff), 100.0, places=9)

    def test_scale_proportional_to_weight(self):
        r = score(CHECKLIST, dict(ALL_BEST, C4=15, C6=7))
        by_id = {c.criterion_id: c for c in r.criteria}
        self.assertEqual(by_id["C4"].points, Decimal(15))   # 15/20 × 20
        self.assertEqual(by_id["C6"].points, Decimal("10.5"))  # 7/10 × 15
        self.assertEqual(r.total, Decimal("90.50"))

    def test_top_step_gives_full_weight(self):
        r = score(CHECKLIST, ALL_BEST)
        by_id = {c.criterion_id: c for c in r.criteria}
        self.assertEqual(by_id["C4"].points, Decimal(20))
        self.assertEqual(by_id["C6"].points, Decimal(15))

    def test_critical_flag_does_not_change_total(self):
        base = score(CHECKLIST, ALL_BEST)
        flagged = score(CHECKLIST, dict(ALL_BEST, C8="yes"), {"C8": ["bypass_service"]})
        self.assertEqual(flagged.total, base.total)
        self.assertTrue(flagged.critical_flag)
        self.assertEqual(flagged.critical_subtypes, ["bypass_service"])
        self.assertFalse(base.critical_flag)

    def test_flag_not_counted_as_applicable(self):
        r = score(CHECKLIST, ALL_BEST)
        c8 = next(c for c in r.criteria if c.criterion_id == "C8")
        self.assertFalse(c8.applicable)
        self.assertIsNone(c8.points)

    def test_sections(self):
        r = score(CHECKLIST, EXAMPLE)
        s = {x.section_id: x for x in r.sections}
        self.assertNotIn("S4", s)  # секция грубых нарушений баллов не имеет
        # S1: C1 = 10 из 10 (C2 — N/A) → после перераспределения 15.38 из 15.38
        self.assertAlmostEqual(float(s["S1"].points), 1000 / 65, places=9)
        self.assertAlmostEqual(float(s["S1"].max), 1000 / 65, places=9)
        # S2: только C4 = 15 из 20 → 23.08 из 30.77
        self.assertAlmostEqual(float(s["S2"].points), 1500 / 65, places=9)
        self.assertAlmostEqual(float(s["S2"].max), 2000 / 65, places=9)
        total_from_sections = sum(x.points for x in r.sections)
        self.assertAlmostEqual(float(total_from_sections), float(r.total), places=2)

    def test_to_dict(self):
        d = score(CHECKLIST, EXAMPLE).to_dict()
        self.assertEqual(d["total"], 85.38)
        self.assertEqual(d["max_total"], 100.0)
        s1 = next(s for s in d["sections"] if s["section_id"] == "S1")
        self.assertEqual(s1["points"], 15.38)
        c4 = next(c for c in d["criteria"] if c["criterion_id"] == "C4")
        self.assertEqual(c4["fraction"], 0.75)
        self.assertEqual(c4["effective_weight"], 30.77)
        json.dumps(d)  # сериализуется


class ValidationTest(unittest.TestCase):
    def test_na_not_allowed(self):
        for cid in ("C1", "C5", "C6", "C8"):
            with self.subTest(cid=cid), self.assertRaises(ScoringError):
                score(CHECKLIST, dict(ALL_BEST, **{cid: "na"}))

    def test_value_not_a_step(self):
        with self.assertRaises(ScoringError):
            score(CHECKLIST, dict(ALL_BEST, C4=12))
        with self.assertRaises(ScoringError):
            score(CHECKLIST, dict(ALL_BEST, C6=5))

    def test_binary_rejects_number(self):
        with self.assertRaises(ScoringError):
            score(CHECKLIST, dict(ALL_BEST, C1=10))

    def test_scale_rejects_yes(self):
        with self.assertRaises(ScoringError):
            score(CHECKLIST, dict(ALL_BEST, C4="yes"))

    def test_missing_answer(self):
        answers = dict(ALL_BEST)
        del answers["C3"]
        with self.assertRaises(ScoringError):
            score(CHECKLIST, answers)

    def test_unknown_criterion(self):
        with self.assertRaises(ScoringError):
            score(CHECKLIST, dict(ALL_BEST, C99="yes"))

    def test_answer_aliases(self):
        c2 = next(c for c in CHECKLIST["criteria"] if c["id"] == "C2")
        c4 = next(c for c in CHECKLIST["criteria"] if c["id"] == "C4")
        self.assertEqual(normalize_answer(c2, "Да"), "yes")
        self.assertEqual(normalize_answer(c2, "НЕТ"), "no")
        self.assertEqual(normalize_answer(c2, "N/A"), "na")
        self.assertEqual(normalize_answer(c2, True), "yes")
        self.assertEqual(normalize_answer(c4, "15"), 15)
        self.assertEqual(normalize_answer(c4, 15.0), 15)


class EdgeCasesTest(unittest.TestCase):
    def test_no_applicable_criteria_total_is_none(self):
        cl = mini_checklist([
            {"id": "A", "type": "binary", "weight": 50},
            {"id": "B", "type": "scale", "weight": 50, "steps": [{"value": 0, "text": ""}, {"value": 10, "text": ""}]},
        ])
        r = score(cl, {"A": "na", "B": "na"})
        self.assertIsNone(r.total)
        self.assertIsNone(r.sections[0].points)
        self.assertIsNone(r.to_dict()["total"])

    def test_only_zero_weight_criteria_total_is_none(self):
        cl = mini_checklist([{"id": "A", "type": "binary", "weight": 0}])
        self.assertIsNone(score(cl, {"A": "yes"}).total)

    def test_round_half_up(self):
        # 100 × 1/32 = 3.125 → 3.13 (банковское округление дало бы 3.12)
        cl = mini_checklist([
            {"id": "A", "type": "binary", "weight": 1},
            {"id": "B", "type": "binary", "weight": 31},
        ])
        self.assertEqual(score(cl, {"A": "yes", "B": "no"}).total, Decimal("3.13"))

    def test_weights_not_summing_to_100_still_max_100(self):
        # Новый критерий добавлен без правки остальных весов: сумма 110.
        cl = copy.deepcopy(CHECKLIST)
        cl["criteria"].append({"id": "C9", "section": "S3", "order": 9, "title": "Новый", "type": "binary",
                               "weight": 10, "na_allowed": False, "description": ""})
        self.assertEqual(score(cl, dict(ALL_BEST, C9="yes")).total, Decimal("100.00"))
        r = score(cl, dict(ALL_BEST, C9="no"))
        self.assertEqual(r.total, Decimal("90.91"))  # 100/110 × 100

    def test_replaced_criterion(self):
        # Сценарий контроля: C5 заменён новым шкальным критерием с другим весом.
        cl = copy.deepcopy(CHECKLIST)
        cl["criteria"] = [c for c in cl["criteria"] if c["id"] != "C5"]
        cl["criteria"].append({"id": "C9", "section": "S3", "order": 5, "title": "Новый", "type": "scale",
                               "weight": 15, "na_allowed": True, "description": "",
                               "steps": [{"value": v, "text": ""} for v in (0, 1, 2, 3)]})
        answers = {k: v for k, v in ALL_BEST.items() if k != "C5"}
        self.assertEqual(score(cl, dict(answers, C9=3)).total, Decimal("100.00"))
        self.assertEqual(score(cl, dict(answers, C9=1)).total, Decimal("90.00"))  # 1/3 × 15 = 5 → 85 + 5
        self.assertEqual(score(cl, dict(answers, C9="na")).total, Decimal("100.00"))

    def test_scale_with_nonzero_bottom(self):
        cl = mini_checklist([{"id": "A", "type": "scale", "weight": 10,
                              "steps": [{"value": v, "text": ""} for v in (1, 3, 5)]}])
        self.assertEqual(score(cl, {"A": 1}).total, Decimal("0.00"))
        self.assertEqual(score(cl, {"A": 3}).total, Decimal("50.00"))
        self.assertEqual(score(cl, {"A": 5}).total, Decimal("100.00"))


class AppealTest(unittest.TestCase):
    def test_decision_changes_total_and_keeps_original(self):
        original = dict(EXAMPLE)
        before = score(CHECKLIST, original)
        final_answers = apply_decisions(original, {"C6": 10})
        after = score(CHECKLIST, final_answers)
        self.assertEqual(original["C6"], 7)  # исходные ответы не изменились
        self.assertEqual(before.total, Decimal("85.38"))
        self.assertEqual(after.total, Decimal("92.31"))  # 59.5 / 65

    def test_decision_to_na_redistributes(self):
        after = score(CHECKLIST, apply_decisions(ALL_WORST, {"C7": "na"}))
        self.assertEqual(after.applicable_weight, Decimal(95))
        self.assertEqual(after.total, Decimal("0.00"))

    def test_confirmed_decision_keeps_total(self):
        self.assertEqual(score(CHECKLIST, apply_decisions(EXAMPLE, {"C4": 15})).total, Decimal("85.38"))

    def test_decision_unknown_criterion(self):
        with self.assertRaises(ScoringError):
            apply_decisions(EXAMPLE, {"C99": "yes"})

    def test_invalid_decision_rejected_by_score(self):
        with self.assertRaises(ScoringError):
            score(CHECKLIST, apply_decisions(EXAMPLE, {"C1": "na"}))


if __name__ == "__main__":
    unittest.main()
