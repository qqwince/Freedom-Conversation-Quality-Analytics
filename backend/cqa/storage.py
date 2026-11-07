"""Хранилище на SQLite: диалоги, версии чек-листов, оценки, апелляции.

Сущности хранятся как JSON-документы с несколькими индексируемыми колонками: схема данных
меняется вместе с чек-листом, а миграции на хакатоне не нужны. Каждая операция открывает
своё соединение — безопасно для фоновых потоков оценки.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cqa.models import Conversation

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    dataset         TEXT,
    channel         TEXT NOT NULL,
    language        TEXT NOT NULL,
    data            TEXT NOT NULL,
    imported_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS conversations_dataset ON conversations(dataset);

CREATE TABLE IF NOT EXISTS checklists (
    checklist_id TEXT NOT NULL,
    version      INTEGER NOT NULL,
    data         TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (checklist_id, version)
);

CREATE TABLE IF NOT EXISTS evaluations (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    checklist_id    TEXT,
    checklist_version INTEGER,
    data            TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS evaluations_conversation ON evaluations(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS appeals (
    id            TEXT PRIMARY KEY,
    evaluation_id TEXT NOT NULL,
    criterion_id  TEXT NOT NULL,
    comment       TEXT NOT NULL,
    status        TEXT NOT NULL,       -- open | confirmed | changed
    created_at    TEXT NOT NULL,
    decision      TEXT,                -- JSON: {new_answer, reason, decided_at}
    decided_at    TEXT,
    ai_opinion    TEXT                 -- JSON: второе мнение модели для проверяющего
);
CREATE INDEX IF NOT EXISTS appeals_evaluation ON appeals(evaluation_id);
CREATE INDEX IF NOT EXISTS appeals_status ON appeals(status);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class NotFound(LookupError):
    pass


class Storage:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(_SCHEMA)
            _migrate(db)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    # --- Диалоги -------------------------------------------------------------------------

    def upsert_conversations(self, conversations: list[Conversation]) -> int:
        rows = [(c.conversation_id, c.dataset_group_id, c.channel, c.language, c.model_dump_json(), now())
                for c in conversations]
        with self._connect() as db:
            db.executemany(
                "INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(conversation_id) DO UPDATE SET dataset=excluded.dataset, channel=excluded.channel, "
                "language=excluded.language, data=excluded.data, imported_at=excluded.imported_at",
                rows,
            )
        return len(rows)

    def get_conversation(self, conversation_id: str) -> Conversation:
        with self._connect() as db:
            row = db.execute("SELECT data FROM conversations WHERE conversation_id = ?",
                             (conversation_id,)).fetchone()
        if row is None:
            raise NotFound(f"диалог {conversation_id} не найден")
        return Conversation.model_validate_json(row["data"])

    def list_conversations(self, dataset: str | None = None) -> list[Conversation]:
        query, params = "SELECT data FROM conversations", ()
        if dataset:
            query, params = query + " WHERE dataset = ?", (dataset,)
        with self._connect() as db:
            rows = db.execute(query + " ORDER BY dataset, conversation_id", params).fetchall()
        return [Conversation.model_validate_json(r["data"]) for r in rows]

    def list_datasets(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT dataset, COUNT(*) AS n, MAX(imported_at) AS imported_at "
                              "FROM conversations GROUP BY dataset ORDER BY dataset").fetchall()
        return [dict(r) for r in rows]

    # --- Чек-листы -----------------------------------------------------------------------

    def save_checklist(self, checklist: dict[str, Any]) -> dict[str, Any]:
        """Сохраняет новую версию: version = последняя + 1."""
        with self._connect() as db:
            row = db.execute("SELECT MAX(version) AS v FROM checklists WHERE checklist_id = ?",
                             (checklist["id"],)).fetchone()
            version = (row["v"] or 0) + 1
            saved = {**checklist, "version": version, "saved_at": now()}
            db.execute("INSERT INTO checklists VALUES (?, ?, ?, ?)",
                       (saved["id"], version, json.dumps(saved, ensure_ascii=False), saved["saved_at"]))
        return saved

    def get_checklist(self, checklist_id: str, version: int | None = None) -> dict[str, Any]:
        with self._connect() as db:
            if version is None:
                row = db.execute("SELECT data FROM checklists WHERE checklist_id = ? ORDER BY version DESC LIMIT 1",
                                 (checklist_id,)).fetchone()
            else:
                row = db.execute("SELECT data FROM checklists WHERE checklist_id = ? AND version = ?",
                                 (checklist_id, version)).fetchone()
        if row is None:
            raise NotFound(f"чек-лист {checklist_id} v{version or 'latest'} не найден")
        return json.loads(row["data"])

    def list_checklists(self) -> list[dict[str, Any]]:
        """Последние версии всех чек-листов и список их версий."""
        with self._connect() as db:
            rows = db.execute("SELECT checklist_id, version, created_at, data FROM checklists "
                              "ORDER BY checklist_id, version").fetchall()
        result: dict[str, dict[str, Any]] = {}
        for r in rows:
            data = json.loads(r["data"])
            entry = result.setdefault(r["checklist_id"], {"id": r["checklist_id"], "versions": []})
            entry.update(name=data.get("name"), latest_version=r["version"])
            entry["versions"].append({"version": r["version"], "created_at": r["created_at"]})
        return list(result.values())

    # --- Оценки --------------------------------------------------------------------------

    def save_evaluation(self, evaluation: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as db:
            db.execute("INSERT INTO evaluations VALUES (?, ?, ?, ?, ?, ?)", (
                evaluation["id"], evaluation["conversation_id"], evaluation.get("checklist_id"),
                evaluation.get("checklist_version"), json.dumps(evaluation, ensure_ascii=False),
                evaluation["created_at"],
            ))
        return evaluation

    def get_evaluation(self, evaluation_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT data FROM evaluations WHERE id = ?", (evaluation_id,)).fetchone()
        if row is None:
            raise NotFound(f"оценка {evaluation_id} не найдена")
        return json.loads(row["data"])

    def list_evaluations(self, conversation_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT data FROM evaluations WHERE conversation_id = ? ORDER BY created_at DESC",
                              (conversation_id,)).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def latest_evaluations(self) -> dict[str, dict[str, Any]]:
        """Последняя оценка каждого диалога (без журнала вызовов — для списков)."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT e.data FROM evaluations e JOIN ("
                "  SELECT conversation_id, MAX(created_at) AS ts FROM evaluations GROUP BY conversation_id"
                ") last ON e.conversation_id = last.conversation_id AND e.created_at = last.ts"
            ).fetchall()
        latest = {}
        for r in rows:
            ev = json.loads(r["data"])
            ev.pop("log", None)
            latest[ev["conversation_id"]] = ev
        return latest

    # --- Апелляции -----------------------------------------------------------------------

    def create_appeal(self, evaluation_id: str, criterion_id: str, comment: str) -> dict[str, Any]:
        appeal = {"id": uuid.uuid4().hex, "evaluation_id": evaluation_id, "criterion_id": criterion_id,
                  "comment": comment, "status": "open", "created_at": now(), "decision": None, "decided_at": None,
                  "ai_opinion": None}
        with self._connect() as db:
            db.execute("INSERT INTO appeals (id, evaluation_id, criterion_id, comment, status, created_at) "
                       "VALUES (?, ?, ?, ?, ?, ?)",
                       (appeal["id"], evaluation_id, criterion_id, comment, "open", appeal["created_at"]))
        return appeal

    def set_ai_opinion(self, appeal_id: str, opinion: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as db:
            updated = db.execute("UPDATE appeals SET ai_opinion = ? WHERE id = ?",
                                 (json.dumps(opinion, ensure_ascii=False), appeal_id)).rowcount
        if not updated:
            raise NotFound(f"апелляция {appeal_id} не найдена")
        return self.get_appeal(appeal_id)

    def get_appeal(self, appeal_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM appeals WHERE id = ?", (appeal_id,)).fetchone()
        if row is None:
            raise NotFound(f"апелляция {appeal_id} не найдена")
        return _appeal_row(row)

    def list_appeals(self, status: str | None = None, evaluation_id: str | None = None) -> list[dict[str, Any]]:
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if evaluation_id:
            clauses.append("evaluation_id = ?")
            params.append(evaluation_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connect() as db:
            rows = db.execute(f"SELECT * FROM appeals{where} ORDER BY created_at", params).fetchall()
        return [_appeal_row(r) for r in rows]

    def decide_appeal(self, appeal_id: str, status: str, decision: dict[str, Any]) -> dict[str, Any]:
        decided_at = now()
        payload = {**decision, "decided_at": decided_at}
        with self._connect() as db:
            updated = db.execute(
                "UPDATE appeals SET status = ?, decision = ?, decided_at = ? WHERE id = ? AND status = 'open'",
                (status, json.dumps(payload, ensure_ascii=False), decided_at, appeal_id),
            ).rowcount
        if not updated:
            raise NotFound(f"открытая апелляция {appeal_id} не найдена")
        return self.get_appeal(appeal_id)


def _appeal_row(row: sqlite3.Row) -> dict[str, Any]:
    appeal = dict(row)
    for key in ("decision", "ai_opinion"):
        appeal[key] = json.loads(appeal[key]) if appeal.get(key) else None
    return appeal


def _migrate(db: sqlite3.Connection) -> None:
    """Добавляет колонки, появившиеся после создания базы (базы с хакатона не пересоздаём)."""
    columns = {r["name"] for r in db.execute("PRAGMA table_info(appeals)")}
    if "ai_opinion" not in columns:
        db.execute("ALTER TABLE appeals ADD COLUMN ai_opinion TEXT")
