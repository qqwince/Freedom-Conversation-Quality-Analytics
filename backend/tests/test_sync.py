"""Автообновление: новые диалоги из API находятся, загружаются и ставятся на оценку."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from cqa.service import CQAService
from cqa.storage import Storage
from cqa.sync import SyncWatcher


def record(cid: str) -> dict[str, Any]:
    return {"conversation_id": cid, "channel": "agent chat", "language": "RU", "audio_url": None,
            "transcript": [{"timestamp": "00:00", "speaker": "client", "text": "Вопрос"},
                           {"timestamp": "00:20", "speaker": "operator", "text": "Здравствуйте, я Тимур."}]}


class FakeApi:
    """Ответ API организаторов, в который «приходят» новые диалоги."""

    def __init__(self) -> None:
        self.records = [record("c-1"), record("c-2")]

    def __call__(self, url: str, group_id: str | None = None) -> dict[str, Any]:
        return {"isSuccess": True, "error": "",
                "data": {"dataset_group_id": group_id, "total_records": len(self.records), "records": self.records}}


@pytest.fixture
def setup(tmp_path, checklist, monkeypatch):
    api = FakeApi()
    monkeypatch.setattr("cqa.service.fetch_raw", api)
    service = CQAService(Storage(tmp_path / "s.sqlite3"), lambda m=None: None, api_url="https://api.test")
    service.storage.save_checklist(checklist)
    started: list[list[str]] = []
    monkeypatch.setattr(service, "start_batch", lambda ids, cl_id: started.append(list(ids)) or {"id": "job-1"})
    clock = {"now": datetime(2026, 11, 8, 10, 0, tzinfo=UTC)}
    watcher = SyncWatcher(service, now=lambda: clock["now"])
    return watcher, api, started, clock


def test_finds_only_new_dialogs_and_evaluates_them(setup):
    watcher, api, started, _ = setup
    watcher.update(enabled=True, interval_min=5, groups=["PROD"], auto_evaluate=True)

    first = watcher.run_once()
    assert first[0]["new_ids"] == ["c-1", "c-2"] and started == [["c-1", "c-2"]]

    assert watcher.run_once()[0]["new_ids"] == []            # ничего нового — оценка не запускается
    assert len(started) == 1

    api.records.append(record("c-3"))                         # организаторы добавили диалог
    assert watcher.run_once()[0]["new_ids"] == ["c-3"] and started[-1] == ["c-3"]

    status = watcher.status()
    assert [e["new_ids"] for e in status["events"]] == [["c-3"], ["c-1", "c-2"]]   # пустые проверки не пишутся
    assert status["new_since"] == ["c-3", "c-1", "c-2"]


def test_without_auto_evaluate_only_imports(setup):
    watcher, _, started, _ = setup
    watcher.update(enabled=True, interval_min=5, groups=["PROD"], auto_evaluate=False)
    assert watcher.run_once()[0]["new_ids"] == ["c-1", "c-2"] and started == []


def test_due_respects_interval_and_enabled(setup):
    watcher, _, _, clock = setup
    assert watcher.due() is False                             # выключено
    watcher.update(enabled=True, interval_min=5, groups=["PROD"], auto_evaluate=False)
    assert watcher.due() is True                              # ещё не проверяли
    watcher.run_once()
    assert watcher.due() is False
    clock["now"] += timedelta(minutes=5)
    assert watcher.due() is True


def test_enabling_requires_group(setup):
    watcher, _, _, _ = setup
    with pytest.raises(ValueError):
        watcher.update(enabled=True, interval_min=5, groups=["  "], auto_evaluate=True)


def test_api_errors_are_logged_not_raised(setup, monkeypatch):
    watcher, _, _, _ = setup
    watcher.update(enabled=True, interval_min=5, groups=["PROD"], auto_evaluate=True)

    def broken(url, group_id=None):
        raise RuntimeError("сеть недоступна")

    monkeypatch.setattr("cqa.service.fetch_raw", broken)
    assert watcher.run_once()[0]["error"] == "сеть недоступна"
    assert watcher.status()["events"][0]["error"] == "сеть недоступна"
