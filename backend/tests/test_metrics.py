from cqa.metrics import compare

GOLD = {"C1": "yes", "C2": "na", "C3": "na", "C4": 15, "C5": "yes", "C6": 7, "C7": "yes", "C8": "no"}


def test_perfect_match(checklist):
    report = compare(checklist, {"a": GOLD}, {"a": dict(GOLD)})
    assert report.answer_accuracy == 1
    assert report.total_mae == 0
    assert report.critical == {"tp": 0, "fp": 0, "fn": 0, "tn": 1}


def test_mismatches_scale_error_and_total(checklist):
    pred = dict(GOLD, C2="yes", C4=10)
    report = compare(checklist, {"a": GOLD}, {"a": pred}, {"a": "RU"})
    assert report.criteria["C2"].confusion[("na", "yes")] == 1
    assert report.criteria["C4"].mae == 5
    assert report.answer_accuracy == 6 / 8
    # эталон 85.38; система: C2 становится применимым → 10+15+10+15+10.5+5 = 65.5 из 80 = 81.875 → 81.88
    assert round(report.total_mae, 2) == 3.5
    assert report.total_within_tolerance == 1
    assert {(m.criterion_id, m.gold, m.predicted) for m in report.mismatches} == {("C2", "na", "yes"),
                                                                                 ("C4", 15, 10)}
    assert report.by_language["RU"]["conversations"] == 1


def test_missing_and_invalid_predictions(checklist):
    report = compare(checklist, {"a": GOLD, "b": GOLD}, {"a": dict(GOLD, C1="na")})
    assert report.missing_predictions == ["b"]
    # «a»: недопустимый ответ, «b»: нет оценки — оба считаются ошибкой
    assert report.criteria["C1"].confusion[("yes", "—")] == 2
    assert sorted(report.total_undefined_mismatch) == ["a", "b"]


def test_critical_flag_outcomes(checklist):
    gold = {"a": dict(GOLD, C8="yes"), "b": GOLD, "c": GOLD}
    pred = {"a": GOLD, "b": dict(GOLD, C8="yes"), "c": GOLD}
    assert compare(checklist, gold, pred).critical == {"tp": 0, "fp": 1, "fn": 1, "tn": 1}
