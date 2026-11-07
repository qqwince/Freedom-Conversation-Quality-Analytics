from cqa.prompt import build_prompt
from cqa.transcript import extract_facts


def test_prompt_contains_all_criteria_without_weights(checklist, conversation):
    prompt = build_prompt(checklist, conversation, extract_facts(conversation))
    for c in checklist["criteria"]:
        assert f"### {c['id']}. {c['title']}" in prompt.user
    assert "Вес" not in prompt.user and "weight" not in prompt.user
    assert "Допустимые ответы: 20 | 15 | 10 | 5 | 0 | na" in prompt.user   # C4
    assert "Допустимые ответы: yes | no\n" in prompt.user                  # C1 без N/A
    assert "[5] 02:38 ОПЕРАТОР:" in prompt.user
    assert "Оценивается только работа оператора" in prompt.system


def test_prompt_subset_of_criteria(checklist, conversation):
    prompt = build_prompt(checklist, conversation, extract_facts(conversation), criteria_ids=["C7"])
    assert "### C7." in prompt.user
    assert "### C1." not in prompt.user
    assert prompt.user.rstrip().endswith("Оцени оператора по критериям: C7.")


def test_new_criterion_from_config_reaches_prompt(checklist, conversation):
    checklist["criteria"].append({
        "id": "C9", "section": "S3", "order": 9, "title": "Проактивность", "type": "scale", "weight": 10,
        "na_allowed": False, "description": "Предложил ли оператор полезное сверх запроса.",
        "steps": [{"value": 2, "text": "да"}, {"value": 0, "text": "нет"}],
        "checks": ["Было ли предложение сверх запроса?"],
    })
    prompt = build_prompt(checklist, conversation, extract_facts(conversation))
    assert "### C9. Проактивность" in prompt.user
    assert "1. Было ли предложение сверх запроса?" in prompt.user
