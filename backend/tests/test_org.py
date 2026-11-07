"""Роли, отделы, права доступа и имя оператора из приветствия."""

from __future__ import annotations

import pytest

from cqa.org import MASKED, Permission, Role, get_org, operator_name
from tests.conftest import make_conversation


@pytest.mark.parametrize(("greeting", "expected"), [
    ("Здравствуйте! Меня зовут Алина. В веб-версии…", "Алина"),
    ("Добрый день! Я Лера из поддержки.", "Лера"),
    ("Здравствуйте, я Соня. Уточните…", "Соня"),
    ("Сәлеметсіз бе, менің атым Аружан.", "Аружан"),
    ("Сәлеметсіз бе! Мен Айсұлумын.", "Айсұлу"),
    ("Сәлеметсіз бе, мен Саматпын.", "Самат"),
    ("Сәлеметсіз бе, мен Айбекпін.", "Айбек"),
    ("Сәлеметсіз бе, мен Дана.", "Дана"),
    ("Hi, I'm Maya from support.", "Maya"),
    ("Здравствуйте, Фридом брокер меня зовут [Имя скрыто], чем могу помочь?", MASKED),
    ("Привет, где именно ищете?", "—"),
])
def test_operator_name(greeting, expected):
    conv = make_conversation([("00:00", "client", "Вопрос"), ("00:10", "operator", greeting)])
    assert operator_name(conv) == expected


def test_department_scoping():
    org = get_org()
    ru = make_conversation([("00:00", "operator", "Здравствуйте, меня зовут Алина.")], language="RU")
    kz = make_conversation([("00:00", "operator", "Сәлеметсіз бе, менің атым Аружан.")], language="KZ")
    assert org.department_of(ru) == "ru" and org.department_of(kz) == "kz"

    assert org.can_see(org.user("mgr-ru"), ru) and not org.can_see(org.user("mgr-ru"), kz)
    assert org.can_see(org.user("mgr-kzen"), kz) and not org.can_see(org.user("mgr-kzen"), ru)
    assert org.can_see(org.user("admin"), ru) and org.can_see(org.user("admin"), kz)
    # оператор видит только свои диалоги
    assert org.can_see(org.user("op-alina"), ru) and not org.can_see(org.user("op-alina"), kz)


def test_roles_permissions():
    org = get_org()
    assert org.default_user.role == Role.ADMIN
    assert not org.user("op-alina").can(Permission.REVIEW_APPEALS)
    assert org.user("reviewer").can(Permission.REVIEW_APPEALS)
    assert not org.user("mgr-ru").can(Permission.EDIT_CHECKLIST)
    assert org.user("admin").can(Permission.EDIT_CHECKLIST)
