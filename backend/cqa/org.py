"""Организационная структура: отделы, роли, права доступа и операторы.

Вход без паролей (в ТЗ: «регистрация и вход в систему не нужны»): интерфейс передаёт
демо-пользователя в заголовке X-CQA-User, сервер применяет его права. Без заголовка —
администратор (командная строка, тесты, встраивание без ролей).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

from cqa.config import PROJECT_ROOT
from cqa.models import Conversation

ORG_PATH = PROJECT_ROOT / "config" / "org.demo.json"
ALL = "*"


class Role(StrEnum):
    OPERATOR = "operator"
    REVIEWER = "reviewer"
    MANAGER = "manager"          # одна, несколько или все линии — задаётся списком отделов
    ADMIN = "admin"


class Permission(StrEnum):
    VIEW_DIALOGS = "view_dialogs"          # видеть диалоги и оценки (в пределах своих отделов)
    EVALUATE = "evaluate"                  # запускать оценку
    APPEAL = "appeal"                      # подавать апелляции
    REVIEW_APPEALS = "review_appeals"      # решать апелляции, второе мнение ИИ
    DASHBOARD = "dashboard"                # дашборд и KPI
    EDIT_CHECKLIST = "edit_checklist"      # менять чек-лист
    IMPORT = "import"                      # загружать наборы и транскрипты
    EXPORT = "export"                      # выгружать results.json
    SYNC = "sync"                          # настраивать автообновление из API


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.OPERATOR: frozenset({Permission.VIEW_DIALOGS, Permission.APPEAL}),
    Role.REVIEWER: frozenset({Permission.VIEW_DIALOGS, Permission.REVIEW_APPEALS}),
    Role.MANAGER: frozenset({Permission.VIEW_DIALOGS, Permission.EVALUATE, Permission.APPEAL,
                             Permission.DASHBOARD}),
    Role.ADMIN: frozenset(Permission),
}

ROLE_TITLES = {
    Role.OPERATOR: "Оператор",
    Role.REVIEWER: "Проверяющий",
    Role.MANAGER: "Руководитель",
    Role.ADMIN: "Администратор",
}


@dataclass(frozen=True)
class User:
    id: str
    name: str
    role: Role
    departments: frozenset[str] | None     # None — все отделы
    operator: str | None = None            # для роли «оператор»: его имя в диалогах

    @property
    def permissions(self) -> frozenset[Permission]:
        return ROLE_PERMISSIONS[self.role]

    def can(self, permission: Permission) -> bool:
        return permission in self.permissions

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "role": self.role.value, "role_title": ROLE_TITLES[self.role],
            "departments": None if self.departments is None else sorted(self.departments),
            "operator": self.operator, "permissions": sorted(p.value for p in self.permissions),
        }


@dataclass(frozen=True)
class Department:
    id: str
    title: str
    languages: tuple[str, ...]


@dataclass
class Org:
    departments: list[Department]
    users: dict[str, User]
    kpi: dict[str, Any] = field(default_factory=dict)
    demo: bool = True
    note: str = ""

    @property
    def default_user(self) -> User:
        return next(u for u in self.users.values() if u.role == Role.ADMIN)

    def user(self, user_id: str | None) -> User:
        if not user_id:
            return self.default_user
        if user_id not in self.users:
            raise KeyError(user_id)
        return self.users[user_id]

    def department_of(self, conv: Conversation) -> str | None:
        return next((d.id for d in self.departments if conv.language in d.languages), None)

    def department_title(self, department_id: str | None) -> str:
        return next((d.title for d in self.departments if d.id == department_id), "Без отдела")

    def can_see(self, user: User, conv: Conversation) -> bool:
        if not user.can(Permission.VIEW_DIALOGS):
            return False
        if user.departments is not None and self.department_of(conv) not in user.departments:
            return False
        return not (user.role == Role.OPERATOR and operator_name(conv) != user.operator)

    def visible(self, user: User, conversations: list[Conversation]) -> list[Conversation]:
        return [c for c in conversations if self.can_see(user, c)]


def load_org(path: Path = ORG_PATH) -> Org:
    data = json.loads(path.read_text(encoding="utf-8"))
    departments = [Department(d["id"], d["title"], tuple(d["languages"])) for d in data["departments"]]
    users = {}
    for u in data["users"]:
        deps = u.get("departments", ALL)
        users[u["id"]] = User(id=u["id"], name=u["name"], role=Role(u["role"]),
                              departments=None if deps == ALL else frozenset(deps), operator=u.get("operator"))
    return Org(departments, users, data.get("kpi", {}), data.get("demo", True), "")


@lru_cache
def get_org() -> Org:
    return load_org()


# --- Имя оператора из приветствия ------------------------------------------------------------

_NAME = r"([A-ZА-ЯЁӘҒҚҢӨҰҮҺІ][a-zа-яёәғқңөұүһі]+)"
# (шаблон, отрезать казахское окончание «я — …»: Айсұлумын → Айсұлу, Мұратпын → Мұрат)
_PATTERNS: list[tuple[re.Pattern[str], bool]] = [
    (re.compile(r"[Мм]еня зовут\s+" + _NAME), False),
    (re.compile(r"(?:^|[\s,.!])[Яя]\s+" + _NAME), False),
    (re.compile(r"[Мм]енің атым\s+" + _NAME), False),
    (re.compile(r"(?:^|[\s,.!])[Мм]ен\s+" + _NAME), True),
    (re.compile(r"\bI['’]m\s+" + _NAME), False),
    (re.compile(r"\b[Mm]y name is\s+" + _NAME), False),
]
_KZ_SUFFIX = re.compile(r"(мын|мін|пын|пін|бын|бін)$")
MASKED = "Имя скрыто"


def operator_name(conv: Conversation) -> str:
    """Имя оператора из его первой реплики; «Имя скрыто», если имя замаскировано, иначе «—»."""
    first = next((m.text for m in conv.transcript if m.speaker == "operator"), "")
    if "[Имя скрыто]" in first:
        return MASKED
    for pattern, strip_kz in _PATTERNS:
        match = pattern.search(first)
        if match:
            name = match.group(1)
            stripped = _KZ_SUFFIX.sub("", name) if strip_kz else name
            return stripped if len(stripped) >= 2 else name
    return "—"
