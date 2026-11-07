"""Сквозной сценарий через HTTP: загрузка → оценка → апелляция → решение → замена критерия."""

from __future__ import annotations

import copy
import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cqa.api import create_app
from cqa.llm import ChatTurn, LLMResult
from cqa.service import CQAService, validate_checklist
from cqa.storage import Storage
from tests.conftest import CHAT

ANSWERS = {"C1": "yes", "C2": "yes", "C3": "yes", "C4": "15", "C5": "yes", "C6": "7", "C7": "yes", "C8": "no"}


class ScriptedProvider:
    """Отвечает на те критерии, которые перечислены в конце промпта."""

    name = "fake"
    model = "fake-model"

    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers
        self.calls = 0

    def generate_json(self, system: str, turns: list[ChatTurn], schema: type) -> LLMResult:
        self.calls += 1
        wanted = turns[0].text.rsplit("Оцени оператора по критериям:", 1)[1].strip(" .\n").split(", ")
        results = [{"criterion_id": c, "checks": [], "answer": self.answers[c],
                    "evidence": [{"msg_idx": 5, "proves": "…"}], "explanation": f"{c} — ok"} for c in wanted]
        return LLMResult(json.dumps({"results": results}), self.model, 100, 50, 0.01)


@pytest.fixture
def setup(tmp_path, checklist):
    provider = ScriptedProvider(dict(ANSWERS))
    service = CQAService(Storage(tmp_path / "test.sqlite3"), lambda model=None: provider, api_url=None, workers=2)
    service.storage.save_checklist(checklist)
    client = TestClient(create_app(service))
    record = {"conversation_id": "t-001", "channel": "agent chat", "language": "RU", "audio_url": None,
              "transcript": [{"timestamp": ts, "speaker": sp, "text": tx} for ts, sp, tx in CHAT]}
    assert client.post("/api/conversations/upload", json={"payload": record}).status_code == 200
    return client, provider, checklist


def wait_job(client: TestClient, job_id: str) -> dict[str, Any]:
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] == "finished":
            return job
        time.sleep(0.02)
    raise AssertionError("задача не завершилась")


def evaluate(client: TestClient, **extra: Any) -> dict[str, Any]:
    body = {"conversation_ids": ["t-001"], "checklist_id": "chat-default", **extra}
    job = wait_job(client, client.post("/api/evaluations", json=body).json()["id"])
    assert job["failed"] == 0, job
    return client.get(f"/api/evaluations/{job['items'][0]['evaluation_id']}").json()


def test_full_flow(setup):
    client, _, _ = setup
    listed = client.get("/api/conversations").json()
    assert [c["conversation_id"] for c in listed] == ["t-001"] and listed[0]["evaluation"] is None

    ev = evaluate(client)
    assert ev["total"] == 90.5 and "log" not in ev
    assert client.get(f"/api/evaluations/{ev['id']}?include_log=true").json()["log"]["calls"]
    assert client.get("/api/conversations").json()[0]["evaluation"]["total"] == 90.5

    appeal = client.post(f"/api/evaluations/{ev['id']}/appeals",
                         json={"criterion_id": "C6", "comment": "Оператор предупредил об ожидании"}).json()
    assert appeal["status"] == "open"
    queue = client.get("/api/appeals?status=open").json()
    assert queue[0]["original"]["answer"] == 7 and queue[0]["conversation_id"] == "t-001"

    duplicate = client.post(f"/api/evaluations/{ev['id']}/appeals", json={"criterion_id": "C6", "comment": "ещё"})
    assert duplicate.status_code == 400

    decided = client.post(f"/api/appeals/{appeal['id']}/decision",
                          json={"action": "change", "new_answer": 10, "reason": "Предупреждение в [7]"}).json()
    assert decided["status"] == "changed" and decided["decision"]["new_answer"] == 10

    final = client.get(f"/api/evaluations/{ev['id']}/final").json()
    assert final["original"]["total"] == 90.5
    assert final["final"]["total"] == 95.0 and final["final"]["changed"] == ["C6"]

    again = client.post(f"/api/appeals/{appeal['id']}/decision", json={"action": "confirm", "reason": "x"})
    assert again.status_code == 400


def test_decision_validation(setup):
    client, _, _ = setup
    ev = evaluate(client)
    appeal = client.post(f"/api/evaluations/{ev['id']}/appeals", json={"criterion_id": "C1", "comment": "!"}).json()
    no_reason = client.post(f"/api/appeals/{appeal['id']}/decision", json={"action": "confirm", "reason": " "})
    assert no_reason.status_code == 400
    bad = client.post(f"/api/appeals/{appeal['id']}/decision",
                      json={"action": "change", "new_answer": "na", "reason": "N/A"})
    assert bad.status_code == 400 and "не разрешён" in bad.json()["detail"]
    confirmed = client.post(f"/api/appeals/{appeal['id']}/decision", json={"action": "confirm", "reason": "верно"})
    assert confirmed.json()["status"] == "confirmed"
    assert client.get(f"/api/evaluations/{ev['id']}/final").json()["final"]["total"] == 90.5


def test_replace_criterion_and_reevaluate_only_new(setup):
    client, provider, checklist = setup
    base = evaluate(client)
    calls_before = provider.calls

    new = copy.deepcopy(checklist)
    new["criteria"] = [c for c in new["criteria"] if c["id"] != "C5"]
    new["criteria"].append({"id": "C9", "section": "S3", "order": 5, "title": "Проактивность", "type": "scale",
                            "weight": 15, "na_allowed": False, "description": "Предложил полезное сверх запроса",
                            "steps": [{"value": 2, "text": "да"}, {"value": 0, "text": "нет"}]})
    saved = client.put("/api/checklists/chat-default", json=new).json()
    assert saved["version"] == 2
    provider.answers["C9"] = "0"

    ev = evaluate(client, criteria_ids=["C9"])
    assert provider.calls == calls_before + 1
    assert ev["checklist_version"] == 2 and ev["base_evaluation_id"] == base["id"]
    assert [r["criterion_id"] for r in ev["results"]] == ["C1", "C2", "C3", "C4", "C9", "C6", "C7", "C8"]
    assert ev["reevaluated"] == ["C9"]
    assert ev["total"] == 75.5    # 90.5 − 15 (C5 «Да») + 0 (C9 = 0)


def test_weight_change_rescores_without_model(setup):
    client, provider, checklist = setup
    evaluate(client)
    calls_before = provider.calls
    new = copy.deepcopy(checklist)
    next(c for c in new["criteria"] if c["id"] == "C1")["weight"] = 20
    client.put("/api/checklists/chat-default", json=new)

    ev = evaluate(client, criteria_ids=[])
    assert provider.calls == calls_before
    assert ev["checklist_version"] == 2 and ev["reevaluated"] == []
    assert ev["total"] == 91.36    # 100.5 из 110 → к 100


def test_checklist_validation_rejects_bad_config(setup, checklist):
    client, _, _ = setup
    bad = copy.deepcopy(checklist)
    bad["criteria"][3]["steps"] = [{"value": 20, "text": ""}]
    bad["criteria"][0]["na_allowed"] = True
    bad["criteria"][0]["na_rule"] = ""
    response = client.put("/api/checklists/chat-default", json=bad)
    assert response.status_code == 400
    assert "C4: у шкалы должно быть минимум две ступени" in response.json()["detail"]
    assert "C1: N/A разрешён, но не описано условие N/A" in response.json()["detail"]
    assert validate_checklist(checklist) == []


def test_upload_rejects_garbage(setup):
    client, _, _ = setup
    assert client.post("/api/conversations/upload", json={"payload": {"foo": 1}}).status_code == 400
    assert client.get("/api/conversations/nope").status_code == 404


def test_audio_redirect(setup, monkeypatch):
    client, _, _ = setup
    from cqa import api

    record = {"conversation_id": "call-1", "channel": "voice call", "language": "RU",
              "audio_url": "https://example.test/player", "transcript": [
                  {"timestamp": "2026-11-03T13:10:00+00:00", "speaker": "operator", "text": "Здравствуйте"}]}
    client.post("/api/conversations/upload", json={"payload": record})
    monkeypatch.setattr(api, "_extract_audio_src", lambda url: "https://cdn.test/a.mp3?sig=1&amp=2")
    response = client.get("/api/conversations/call-1/audio", follow_redirects=False)
    assert response.status_code == 307 and response.headers["location"].startswith("https://cdn.test/a.mp3")
    assert client.get("/api/conversations/call-1").json()["audio_url"] is None   # подпись наружу не отдаём


def test_extract_audio_src_unescapes(monkeypatch):
    from cqa import api

    class Page:
        text = '<audio><source src="https://x.test/f?sp=r&amp;sig=a%3D" type="audio/mpeg"></audio>'

    monkeypatch.setattr(api.httpx, "get", lambda url, timeout: Page())
    assert api._extract_audio_src("https://player") == "https://x.test/f?sp=r&sig=a%3D"
