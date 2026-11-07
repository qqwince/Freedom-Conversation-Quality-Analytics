"""HTTP API (FastAPI) и раздача интерфейса.

Запуск:  .venv/Scripts/python -m uvicorn cqa.api:create_app --factory --port 8000
Интерфейс: http://localhost:8000/   Документация API: http://localhost:8000/docs
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import time
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from cqa.config import PROJECT_ROOT, get_settings
from cqa.llm import LLMError, create_provider
from cqa.service import CQAService, ServiceError, conversation_summary, export_dataset
from cqa.storage import NotFound, Storage

log = logging.getLogger(__name__)
FRONTEND_DIR = PROJECT_ROOT / "frontend"


# --- Запросы -------------------------------------------------------------------------------

class ImportGroupRequest(BaseModel):
    group_id: str = Field(min_length=1)


class UploadRequest(BaseModel):
    payload: Any = Field(description="Ответ API, одна запись диалога или список записей")
    dataset: str = "MANUAL"


class EvaluateRequest(BaseModel):
    conversation_ids: list[str] = Field(min_length=1)
    checklist_id: str
    checklist_version: int | None = None
    criteria_ids: list[str] | None = Field(default=None, description="Переоценить только эти критерии")
    votes: int | None = Field(default=None, ge=1, le=5)
    model: str | None = None


class AppealRequest(BaseModel):
    criterion_id: str
    comment: str


class DecisionRequest(BaseModel):
    action: str = Field(pattern="^(confirm|change)$")
    reason: str
    new_answer: Any = None


# --- Приложение ----------------------------------------------------------------------------

def create_app(service: CQAService | None = None) -> FastAPI:
    settings = get_settings()
    if service is None:
        service = CQAService(
            storage=Storage(settings.data_dir / "cqa.sqlite3"),
            provider_factory=create_provider,
            api_url=os.environ.get("CQA_API_URL"),
            workers=settings.workers,
            default_votes=settings.votes,
        )
        _seed(service)

    app = FastAPI(title="CQA — оценка качества диалогов", version="0.1.0")
    app.state.service = service
    audio_cache: dict[str, tuple[float, str]] = {}

    @app.middleware("http")
    async def _no_stale_frontend(request: Request, call_next: Any) -> Any:
        """Файлы интерфейса браузер перепроверяет при каждой загрузке — после обновления нет старых JS-модулей."""
        response = await call_next(request)
        if not request.url.path.startswith(("/api/", "/_v/")):   # /_v/ — адреса с меткой запуска, кэш можно
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.exception_handler(ServiceError)
    async def _bad_request(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(LLMError)
    async def _llm_error(_: Request, exc: LLMError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": f"модель недоступна: {exc}"})

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "model": settings.llm_model, "votes": service.default_votes}

    # Наборы и диалоги

    @app.get("/api/datasets")
    def datasets() -> list[dict[str, Any]]:
        return service.storage.list_datasets()

    @app.post("/api/datasets/import")
    def import_group(body: ImportGroupRequest) -> dict[str, Any]:
        return service.import_group(body.group_id.strip())

    @app.post("/api/conversations/upload")
    def upload(body: UploadRequest) -> dict[str, Any]:
        return service.import_payload(body.payload, body.dataset)

    @app.get("/api/conversations")
    def conversations(dataset: str | None = None, language: str | None = None,
                      channel: str | None = None) -> list[dict[str, Any]]:
        latest = service.storage.latest_evaluations()
        # Итог после решений проверяющего — для оценок, где оценку меняли по апелляции.
        changed = {a["evaluation_id"] for a in service.storage.list_appeals(status="changed")}
        rows = []
        for conv in service.storage.list_conversations(dataset):
            if (language and conv.language != language) or (channel and conv.channel != channel):
                continue
            summary = conversation_summary(conv, latest.get(conv.conversation_id))
            ev = summary["evaluation"]
            if ev and ev["id"] in changed:
                ev["final_total"] = service.final_result(ev["id"])["final"]["total"]
            rows.append(summary)
        return rows

    @app.get("/api/conversations/{conversation_id}")
    def conversation(conversation_id: str) -> dict[str, Any]:
        conv = service.storage.get_conversation(conversation_id)
        return {**conv.model_dump(), "audio_url": None,   # ссылка с подписью — только через /audio
                "has_audio": bool(conv.audio_url),
                "evaluations": [_brief(e) for e in service.storage.list_evaluations(conversation_id)]}

    @app.get("/api/conversations/{conversation_id}/audio")
    def audio(conversation_id: str) -> RedirectResponse:
        """Свежая ссылка на mp3: audio_url ведёт на страницу плеера, подпись ссылки живёт ~1 час."""
        cached = audio_cache.get(conversation_id)
        if cached and cached[0] > time.monotonic():
            return RedirectResponse(cached[1])
        conv = service.storage.get_conversation(conversation_id)
        if not conv.audio_url:
            raise NotFound("у диалога нет записи")
        src = _extract_audio_src(conv.audio_url)
        audio_cache[conversation_id] = (time.monotonic() + 40 * 60, src)
        return RedirectResponse(src)

    # Чек-листы

    @app.get("/api/checklists")
    def checklists() -> list[dict[str, Any]]:
        return service.storage.list_checklists()

    @app.get("/api/checklists/{checklist_id}")
    def checklist(checklist_id: str, version: int | None = None) -> dict[str, Any]:
        return service.storage.get_checklist(checklist_id, version)

    @app.put("/api/checklists/{checklist_id}")
    def save_checklist(checklist_id: str, body: dict[str, Any]) -> dict[str, Any]:
        if body.get("id") != checklist_id:
            raise ServiceError("id в теле не совпадает с адресом")
        return service.save_checklist(body)

    # Оценка

    @app.post("/api/evaluations")
    def start_evaluation(body: EvaluateRequest) -> dict[str, Any]:
        return service.start_batch(body.conversation_ids, body.checklist_id, body.checklist_version,
                                   body.criteria_ids, body.votes, body.model)

    @app.get("/api/jobs")
    def jobs() -> list[dict[str, Any]]:
        return service.jobs.list()

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str) -> dict[str, Any]:
        return service.jobs.get(job_id)

    @app.get("/api/evaluations/{evaluation_id}")
    def evaluation(evaluation_id: str, include_log: bool = False) -> dict[str, Any]:
        ev = service.storage.get_evaluation(evaluation_id)
        return ev if include_log else {k: v for k, v in ev.items() if k != "log"}

    @app.get("/api/evaluations/{evaluation_id}/final")
    def final(evaluation_id: str) -> dict[str, Any]:
        return service.final_result(evaluation_id)

    # Апелляции

    @app.post("/api/evaluations/{evaluation_id}/appeals")
    def create_appeal(evaluation_id: str, body: AppealRequest) -> dict[str, Any]:
        return service.create_appeal(evaluation_id, body.criterion_id, body.comment)

    @app.get("/api/appeals")
    def appeals(status: str | None = None) -> list[dict[str, Any]]:
        """Очередь проверяющего: апелляция, спорный результат критерия и контекст оценки."""
        rows = service.storage.list_appeals(status=status)
        evaluations: dict[str, dict[str, Any]] = {}
        for a in rows:
            if a["evaluation_id"] not in evaluations:
                evaluations[a["evaluation_id"]] = service.storage.get_evaluation(a["evaluation_id"])
            ev = evaluations[a["evaluation_id"]]
            a.update(
                conversation_id=ev["conversation_id"],
                checklist_id=ev["checklist_id"],
                checklist_version=ev["checklist_version"],
                evaluation_total=ev["total"],
                original=next((r for r in ev["results"] if r["criterion_id"] == a["criterion_id"]), None),
            )
        return rows

    @app.post("/api/appeals/{appeal_id}/ai-opinion")
    def ai_opinion(appeal_id: str) -> dict[str, Any]:
        """Второе мнение модели по спорному критерию — подсказка проверяющему (≈ 20–40 с)."""
        return service.ai_second_opinion(appeal_id)

    @app.post("/api/appeals/{appeal_id}/decision")
    def decide(appeal_id: str, body: DecisionRequest) -> dict[str, Any]:
        return service.decide_appeal(appeal_id, body.action, body.reason, body.new_answer)

    # Выгрузка в формате организаторов

    @app.get("/api/export")
    def export(dataset: str, with_appeals: bool = True) -> JSONResponse:
        """results.json (формат 1.0) по набору; проблемы формата — в заголовке X-CQA-Problems."""
        team = os.environ.get("CQA_TEAM_NAME") or "CQA"
        mode = os.environ.get("CQA_MODEL_MODE", "external_api")
        doc, problems = export_dataset(service, dataset, team, mode, with_appeals)
        filename = f"results-{dataset}.json"
        return JSONResponse(doc, headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-CQA-Problems": str(len(problems)),
        })

    if FRONTEND_DIR.exists():
        _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """Интерфейс с адресами вида /_v/<версия>/js/…, где версия — время последнего изменения файлов
    интерфейса. После любой правки браузер гарантированно берёт новые JS-модули и стили, а не старые из кэша."""
    root = FRONTEND_DIR.resolve()

    def version() -> str:
        return str(max(int(p.stat().st_mtime) for p in root.rglob("*") if p.is_file()))

    @app.get("/", include_in_schema=False)
    def frontend_index() -> HTMLResponse:
        index = (root / "index.html").read_text(encoding="utf-8")
        build = version()
        for asset in ('href="css/', 'src="js/'):
            index = index.replace(asset, asset.replace('="', f'="_v/{build}/'))
        return HTMLResponse(index, headers={"Cache-Control": "no-cache"})

    @app.get("/_v/{_build}/{path:path}", include_in_schema=False)
    def frontend_asset(_build: str, path: str) -> FileResponse:
        file = (root / path).resolve()
        if not file.is_file() or root not in file.parents:
            raise HTTPException(status_code=404)
        return FileResponse(file, headers={"Cache-Control": "public, max-age=31536000, immutable"})

    app.mount("/", StaticFiles(directory=root, html=True), name="frontend")


def _brief(evaluation: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "created_at", "total", "checklist_id", "checklist_version", "model", "needs_review",
            "critical", "base_evaluation_id", "reevaluated")
    return {k: evaluation.get(k) for k in keys}


_AUDIO_SRC = re.compile(r"<source[^>]+src=\"([^\"]+)\"", re.I)


def _extract_audio_src(player_url: str) -> str:
    try:
        page = httpx.get(player_url, timeout=30).text
    except httpx.HTTPError as exc:
        raise ServiceError(f"не удалось открыть страницу записи: {exc}") from exc
    match = _AUDIO_SRC.search(page)
    if not match:
        raise ServiceError("на странице записи не найден аудиофайл")
    return html.unescape(match.group(1))


def _seed(service: CQAService) -> None:
    """Первый запуск: исходный чек-лист и DEV-набор, если он уже скачан."""
    settings = get_settings()
    if not service.storage.list_checklists():
        default = json.loads(settings.checklist_path.read_text(encoding="utf-8"))
        service.storage.save_checklist(default)
        log.info("загружен исходный чек-лист %s", default["id"])
    dev = settings.data_dir / "DEV.json"
    if not service.storage.list_datasets() and dev.exists():
        records = json.loads(dev.read_text(encoding="utf-8"))["records"]
        service.import_payload(records, dataset="DEV")

