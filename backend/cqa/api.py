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
import shutil
import threading
import time
from datetime import date
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from cqa.analytics import (
    AppealFact,
    Row,
    appeal_calibration,
    build_dashboard,
    operator_conversations,
    operator_profile,
)
from cqa.coach import CoachLimitError
from cqa.coach import ask as ask_coach
from cqa.coach import build_context as build_coach_context
from cqa.config import PROJECT_ROOT, get_settings
from cqa.criterion_draft import draft_criterion
from cqa.dataset import DatasetError
from cqa.feedback import build_feedback
from cqa.llm import LLMError, create_provider
from cqa.models import Conversation
from cqa.org import Permission, Role, User, get_org, operator_name
from cqa.service import CQAService, ServiceError, conversation_summary, export_dataset
from cqa.storage import NotFound, Storage
from cqa.sync import SyncWatcher

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


class CoachMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    text: str


class CoachRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[CoachMessage] = Field(default_factory=list)
    operator: str | None = Field(default=None, description="Для руководителя: о каком операторе спрашивать")


class CriterionDraftRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    weight: float = Field(default=10, ge=0, le=100)
    checklist: dict[str, Any] | None = Field(default=None, description="Несохранённый черновик чек-листа из редактора")


class SyncRequest(BaseModel):
    enabled: bool
    interval_min: int = Field(default=5, ge=1, le=120)
    groups: list[str] = Field(default_factory=list)
    auto_evaluate: bool = True


class DecisionRequest(BaseModel):
    action: str = Field(pattern="^(confirm|change)$")
    reason: str
    new_answer: Any = None


# --- Пользователь --------------------------------------------------------------------------

def current_user(x_cqa_user: str | None = Header(default=None), as_user: str | None = None) -> User:
    """Демо-пользователь из заголовка X-CQA-User (вход без паролей); без заголовка — администратор.
    Параметр ?as_user= — для ссылок, к которым нельзя добавить заголовок (аудиоплеер, скачивание файла)."""
    user_id = x_cqa_user or as_user
    try:
        return get_org().user(user_id)
    except KeyError:
        raise HTTPException(status_code=401, detail=f"неизвестный пользователь {user_id!r}") from None


CurrentUser = Annotated[User, Depends(current_user)]


# --- Приложение ----------------------------------------------------------------------------

def create_app(service: CQAService | None = None, start_sync: bool | None = None) -> FastAPI:
    settings = get_settings()
    start_sync = service is None if start_sync is None else start_sync
    if service is None:
        database = settings.data_dir / "cqa.sqlite3"
        _restore_demo(database)
        service = CQAService(
            storage=Storage(database),
            provider_factory=create_provider,
            api_url=os.environ.get("CQA_API_URL"),
            workers=settings.workers,
            default_votes=settings.votes,
        )
        _seed(service)

    app = FastAPI(title="CQA — оценка качества диалогов", version="0.1.0")
    app.state.service = service
    sync = SyncWatcher(service)
    app.state.sync = sync
    if start_sync:
        sync.start()
    audio_cache: dict[str, tuple[float, str]] = {}

    @app.middleware("http")
    async def _no_stale_frontend(request: Request, call_next: Any) -> Any:
        """Файлы интерфейса браузер перепроверяет при каждой загрузке — после обновления нет старых JS-модулей."""
        response = await call_next(request)
        if not request.url.path.startswith(("/api/", "/_v/")):   # /_v/ — адреса с меткой запуска, кэш можно
            response.headers["Cache-Control"] = "no-cache"
        elif request.url.path.startswith("/api/") and FRONTEND_DIR.exists():
            # открытая вкладка сравнивает с версией, с которой загрузилась, и предлагает обновиться
            response.headers["X-CQA-Build"] = frontend_version()
        return response

    @app.exception_handler(ServiceError)
    async def _bad_request(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(DatasetError)
    async def _dataset_error(_: Request, exc: DatasetError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(LLMError)
    async def _llm_error(_: Request, exc: LLMError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": f"модель недоступна: {exc}"})

    org = get_org()

    def require(user: User, permission: Permission) -> None:
        if not user.can(permission):
            raise HTTPException(status_code=403, detail=f"у роли «{user.to_dict()['role_title']}» нет прав на это")

    def visible_conversation(user: User, conversation_id: str) -> Conversation:
        conv = service.storage.get_conversation(conversation_id)
        if not org.can_see(user, conv):
            raise NotFound(f"диалог {conversation_id} не найден")   # не раскрываем, что он существует
        return conv

    def visible_evaluation(user: User, evaluation_id: str) -> dict[str, Any]:
        ev = service.storage.get_evaluation(evaluation_id)
        visible_conversation(user, ev["conversation_id"])
        return ev

    def visible_appeal(user: User, appeal_id: str) -> dict[str, Any]:
        appeal = service.storage.get_appeal(appeal_id)
        visible_evaluation(user, appeal["evaluation_id"])
        return appeal

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "model": settings.llm_model, "votes": service.default_votes}

    # Пользователь и структура

    @app.get("/api/me")
    def me(user: CurrentUser) -> dict[str, Any]:
        """Текущий пользователь, его права и демо-пользователи для переключателя ролей."""
        return {
            "user": user.to_dict(),
            "users": [u.to_dict() for u in org.users.values()],
            "departments": [{"id": d.id, "title": d.title, "languages": list(d.languages)} for d in org.departments],
            "demo": org.demo,
            "note": org.note,
        }

    # Наборы и диалоги

    @app.get("/api/datasets")
    def datasets(user: CurrentUser) -> list[dict[str, Any]]:
        return service.storage.list_datasets()

    @app.post("/api/datasets/import")
    def import_group(body: ImportGroupRequest, user: CurrentUser) -> dict[str, Any]:
        require(user, Permission.IMPORT)
        return service.import_group(body.group_id.strip())

    @app.post("/api/conversations/upload")
    def upload(body: UploadRequest, user: CurrentUser) -> dict[str, Any]:
        require(user, Permission.IMPORT)
        return service.import_payload(body.payload, body.dataset)

    @app.get("/api/conversations")
    def conversations(user: CurrentUser, dataset: str | None = None, language: str | None = None,
                      channel: str | None = None) -> list[dict[str, Any]]:
        latest = service.storage.latest_evaluations()
        # Итог после решений проверяющего — для оценок, где оценку меняли по апелляции.
        changed = {a["evaluation_id"] for a in service.storage.list_appeals(status="changed")}
        rows = []
        for conv in org.visible(user, service.storage.list_conversations(dataset)):
            if (language and conv.language != language) or (channel and conv.channel != channel):
                continue
            summary = conversation_summary(conv, latest.get(conv.conversation_id))
            department = org.department_of(conv)
            summary.update(operator=operator_name(conv), department=department,
                           department_title=org.department_title(department))
            ev = summary["evaluation"]
            if ev and ev["id"] in changed:
                ev["final_total"] = service.final_result(ev["id"])["final"]["total"]
            rows.append(summary)
        return rows

    @app.get("/api/conversations/{conversation_id}")
    def conversation(conversation_id: str, user: CurrentUser) -> dict[str, Any]:
        conv = visible_conversation(user, conversation_id)
        department = org.department_of(conv)
        return {**conv.model_dump(), "audio_url": None,   # ссылка с подписью — только через /audio
                "has_audio": bool(conv.audio_url), "operator": operator_name(conv),
                "department": department, "department_title": org.department_title(department),
                "evaluations": [_brief(e) for e in service.storage.list_evaluations(conversation_id)]}

    @app.get("/api/conversations/{conversation_id}/audio")
    def audio(conversation_id: str, user: CurrentUser) -> RedirectResponse:
        """Свежая ссылка на mp3: audio_url ведёт на страницу плеера, подпись ссылки живёт ~1 час."""
        conv = visible_conversation(user, conversation_id)
        cached = audio_cache.get(conversation_id)
        if cached and cached[0] > time.monotonic():
            return RedirectResponse(cached[1])
        if not conv.audio_url:
            raise NotFound("у диалога нет записи")
        src = _extract_audio_src(conv.audio_url)
        audio_cache[conversation_id] = (time.monotonic() + 40 * 60, src)
        return RedirectResponse(src)

    # Чек-листы

    @app.get("/api/checklists")
    def checklists(user: CurrentUser) -> list[dict[str, Any]]:
        return service.storage.list_checklists()

    @app.get("/api/checklists/{checklist_id}")
    def checklist(checklist_id: str, user: CurrentUser, version: int | None = None) -> dict[str, Any]:
        return service.storage.get_checklist(checklist_id, version)

    @app.post("/api/checklists/{checklist_id}/draft-criterion")
    def draft_new_criterion(checklist_id: str, body: CriterionDraftRequest, user: CurrentUser) -> dict[str, Any]:
        """Черновик критерия по названию и описанию — для проверки в редакторе, не сохраняется."""
        require(user, Permission.EDIT_CHECKLIST)
        checklist = body.checklist or service.storage.get_checklist(checklist_id)
        try:
            return draft_criterion(checklist, body.title, body.description, body.weight,
                                   lambda: service._provider_factory(None))
        except ValueError as exc:
            raise ServiceError(str(exc)) from exc

    @app.put("/api/checklists/{checklist_id}")
    def save_checklist(checklist_id: str, body: dict[str, Any], user: CurrentUser) -> dict[str, Any]:
        require(user, Permission.EDIT_CHECKLIST)
        if body.get("id") != checklist_id:
            raise ServiceError("id в теле не совпадает с адресом")
        return service.save_checklist(body)

    # Оценка

    @app.post("/api/evaluations")
    def start_evaluation(body: EvaluateRequest, user: CurrentUser) -> dict[str, Any]:
        require(user, Permission.EVALUATE)
        for conversation_id in body.conversation_ids:
            visible_conversation(user, conversation_id)
        return service.start_batch(body.conversation_ids, body.checklist_id, body.checklist_version,
                                   body.criteria_ids, body.votes, body.model)

    @app.get("/api/jobs")
    def jobs(user: CurrentUser) -> list[dict[str, Any]]:
        return service.jobs.list()

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str, user: CurrentUser) -> dict[str, Any]:
        return service.jobs.get(job_id)

    @app.get("/api/evaluations/{evaluation_id}")
    def evaluation(evaluation_id: str, user: CurrentUser, include_log: bool = False) -> dict[str, Any]:
        ev = visible_evaluation(user, evaluation_id)
        return ev if include_log else {k: v for k, v in ev.items() if k != "log"}

    @app.get("/api/evaluations/{evaluation_id}/final")
    def final(evaluation_id: str, user: CurrentUser) -> dict[str, Any]:
        visible_evaluation(user, evaluation_id)
        return service.final_result(evaluation_id)

    # Апелляции

    @app.post("/api/evaluations/{evaluation_id}/appeals")
    def create_appeal(evaluation_id: str, body: AppealRequest, user: CurrentUser) -> dict[str, Any]:
        require(user, Permission.APPEAL)
        visible_evaluation(user, evaluation_id)
        return service.create_appeal(evaluation_id, body.criterion_id, body.comment)

    @app.get("/api/appeals")
    def appeals(user: CurrentUser, status: str | None = None) -> list[dict[str, Any]]:
        """Очередь проверяющего: апелляция, спорный результат критерия и контекст оценки."""
        rows = []
        evaluations: dict[str, dict[str, Any]] = {}
        for a in service.storage.list_appeals(status=status):
            if a["evaluation_id"] not in evaluations:
                evaluations[a["evaluation_id"]] = service.storage.get_evaluation(a["evaluation_id"])
            ev = evaluations[a["evaluation_id"]]
            if not org.can_see(user, service.storage.get_conversation(ev["conversation_id"])):
                continue
            a.update(
                conversation_id=ev["conversation_id"],
                checklist_id=ev["checklist_id"],
                checklist_version=ev["checklist_version"],
                evaluation_total=ev["total"],
                original=next((r for r in ev["results"] if r["criterion_id"] == a["criterion_id"]), None),
            )
            rows.append(a)
        return rows

    @app.post("/api/appeals/{appeal_id}/ai-opinion")
    def ai_opinion(appeal_id: str, user: CurrentUser) -> dict[str, Any]:
        """Второе мнение модели по спорному критерию — подсказка проверяющему (≈ 20–40 с)."""
        require(user, Permission.REVIEW_APPEALS)
        visible_appeal(user, appeal_id)
        return service.ai_second_opinion(appeal_id)

    @app.post("/api/appeals/{appeal_id}/decision")
    def decide(appeal_id: str, body: DecisionRequest, user: CurrentUser) -> dict[str, Any]:
        require(user, Permission.REVIEW_APPEALS)
        visible_appeal(user, appeal_id)
        return service.decide_appeal(appeal_id, body.action, body.reason, body.new_answer)

    # Аналитика

    def analytics_rows(user: User) -> tuple[list[Row], int, dict[str, Any]]:
        """Видимые пользователю диалоги с последней оценкой (с учётом решений по апелляциям)."""
        conversations = org.visible(user, service.storage.list_conversations())
        latest = service.storage.latest_evaluations()
        changed = {a["evaluation_id"] for a in service.storage.list_appeals(status="changed")}
        rows = []
        for conv in conversations:
            ev = latest.get(conv.conversation_id)
            if not ev:
                continue
            answers = {r["criterion_id"]: r["answer"] for r in ev["results"]}
            total = ev["total"]
            if ev["id"] in changed:
                final_view = service.final_result(ev["id"])["final"]
                answers, total = final_view["answers"], final_view["total"]
            critical = ev.get("critical") or {}
            flag_result = next((r for r in ev["results"] if r.get("flag_subtypes") or r["criterion_id"] == "C8"), {})
            department = org.department_of(conv)
            rows.append(Row(
                conversation_id=conv.conversation_id, operator=operator_name(conv), department=department,
                department_title=org.department_title(department), answers=answers, total=total,
                critical=bool(critical.get("flag")), critical_subtypes=critical.get("subtypes") or [],
                critical_reason=flag_result.get("explanation", "") if critical.get("flag") else "",
                needs_review=bool(ev.get("needs_review")),
            ))
        checklist_meta = service.storage.list_checklists()[0]
        checklist = service.storage.get_checklist(checklist_meta["id"])
        return rows, len(conversations), checklist

    @app.get("/api/dashboard")
    def dashboard(user: CurrentUser) -> dict[str, Any]:
        """KPI качества, соблюдение критериев, грубые ошибки и рейтинг операторов по видимым отделам."""
        require(user, Permission.DASHBOARD)
        rows, total_dialogs, checklist = analytics_rows(user)
        visible_ids = {r.conversation_id for r in rows}
        evaluations: dict[str, dict[str, Any]] = {}
        facts = []
        for a in service.storage.list_appeals():
            if a["evaluation_id"] not in evaluations:
                evaluations[a["evaluation_id"]] = service.storage.get_evaluation(a["evaluation_id"])
            ev = evaluations[a["evaluation_id"]]
            if ev["conversation_id"] not in visible_ids:
                continue
            decision, opinion = a.get("decision") or {}, a.get("ai_opinion") or {}
            facts.append(AppealFact(
                criterion_id=a["criterion_id"], conversation_id=ev["conversation_id"], status=a["status"],
                model_answer=next((r["answer"] for r in ev["results"] if r["criterion_id"] == a["criterion_id"]), None),
                new_answer=decision.get("new_answer"), reason=decision.get("reason") or "",
                decided_at=a.get("decided_at") or "", ai_supports=opinion.get("supports_appeal"),
            ))
        open_appeals = sum(1 for f in facts if f.status == "open")
        return {**build_dashboard(rows, checklist, org.kpi, open_appeals, total_dialogs),
                "calibration": appeal_calibration(facts, checklist),
                "checklist_version": checklist["version"]}

    @app.get("/api/operators/{name}/feedback")
    def operator_feedback(name: str, user: CurrentUser, ai: bool = True, refresh: bool = False) -> dict[str, Any]:
        """Сводка для оператора на начало смены: что улучшить и сколько баллов это даст.

        Сводка формируется один раз в день и хранится в базе: повторные открытия модель не вызывают.
        refresh=true — пересчитать заново (только руководитель)."""
        if not (user.can(Permission.DASHBOARD) or (user.role.value == "operator" and user.operator == name)):
            raise HTTPException(status_code=403, detail="сводку видят сам оператор и его руководители")
        # Рейтинг — среди коллег по линии: оператор видит только своё место, не чужие диалоги.
        scope = user if user.role.value != "operator" else User(
            id=user.id, name=user.name, role=Role.MANAGER, departments=user.departments)
        rows, _, checklist = analytics_rows(scope)
        today = date.today().isoformat()
        conversations = operator_conversations(rows, name)   # всегда свежий список — модель не нужна
        cached = service.storage.get_feedback(name, today)
        if cached and not (refresh and user.can(Permission.DASHBOARD)):
            return {**cached, "cached": True, "conversations": conversations}
        profile = operator_profile(rows, checklist, name)
        if not profile["dialogs"]:
            raise NotFound(f"нет оценённых диалогов оператора {name}")
        feedback = build_feedback(profile, (lambda: service._provider_factory(None)) if ai else None)
        service.storage.save_feedback(name, today, feedback)
        return {**feedback, "cached": False, "conversations": conversations}

    @app.post("/api/coach")
    def coach(body: CoachRequest, user: CurrentUser) -> dict[str, Any]:
        """Чат-наставник: оператор спрашивает про свои оценки, ответ — по чек-листу и обоснованиям."""
        name = user.operator if user.role == Role.OPERATOR else body.operator
        if not name:
            raise ServiceError("укажите оператора")
        if user.role != Role.OPERATOR and not user.can(Permission.DASHBOARD):
            raise HTTPException(status_code=403, detail="наставник доступен оператору и его руководителю")
        scope = user if user.role != Role.OPERATOR else User(
            id=user.id, name=user.name, role=Role.MANAGER, departments=user.departments)
        rows, _, checklist = analytics_rows(scope)
        profile = operator_profile(rows, checklist, name)
        latest = service.storage.latest_evaluations()
        evaluations = [latest[r.conversation_id] for r in rows if r.operator == name and r.conversation_id in latest]
        context = build_coach_context(name, profile, checklist, evaluations)
        try:
            history = [m.model_dump() for m in body.history]
            return ask_coach(name, body.question, history, context, lambda: service._provider_factory(None))
        except CoachLimitError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except ValueError as exc:
            raise ServiceError(str(exc)) from exc

    # Автообновление из API организаторов

    @app.get("/api/sync")
    def sync_status(user: CurrentUser, since: str | None = None) -> dict[str, Any]:
        """Настройки, журнал проверок и новые диалоги после since (только видимые пользователю)."""
        status = sync.status(since)
        visible = set()
        for cid in status["new_since"]:
            try:
                visible_conversation(user, cid)
                visible.add(cid)
            except NotFound:
                pass
        status["new_since"] = [cid for cid in status["new_since"] if cid in visible]
        if not user.can(Permission.SYNC):
            status = {k: status[k] for k in ("enabled", "last_check", "next_check", "running", "new_since")}
        return status

    @app.put("/api/sync")
    def sync_update(body: SyncRequest, user: CurrentUser) -> dict[str, Any]:
        require(user, Permission.SYNC)
        try:
            return sync.update(body.enabled, body.interval_min, body.groups, body.auto_evaluate)
        except ValueError as exc:
            raise ServiceError(str(exc)) from exc

    @app.post("/api/sync/run")
    def sync_run(user: CurrentUser) -> dict[str, Any]:
        """Проверить API сейчас (в фоне); результат появится в журнале и в пометке «+N новых»."""
        require(user, Permission.SYNC)
        threading.Thread(target=sync.run_once, name="cqa-sync-now", daemon=True).start()
        return sync.status()

    # Выгрузка в формате организаторов

    @app.get("/api/export")
    def export(dataset: str, user: CurrentUser, with_appeals: bool = True) -> JSONResponse:
        """results.json (формат 1.0) по набору; проблемы формата — в заголовке X-CQA-Problems."""
        require(user, Permission.EXPORT)
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
    @app.get("/", include_in_schema=False)
    def frontend_index() -> HTMLResponse:
        index = (root / "index.html").read_text(encoding="utf-8")
        build = frontend_version()
        for asset in ('href="css/', 'src="js/'):
            index = index.replace(asset, asset.replace('="', f'="_v/{build}/'))
        index = index.replace("<head>", f'<head>\n  <meta name="cqa-build" content="{build}">', 1)
        return HTMLResponse(index, headers={"Cache-Control": "no-cache"})

    @app.get("/_v/{_build}/{path:path}", include_in_schema=False)
    def frontend_asset(_build: str, path: str) -> FileResponse:
        file = (root / path).resolve()
        if not file.is_file() or root not in file.parents:
            raise HTTPException(status_code=404)
        return FileResponse(file, headers={"Cache-Control": "public, max-age=31536000, immutable"})

    app.mount("/", StaticFiles(directory=root, html=True), name="frontend")


_version_cache: tuple[float, str] = (0.0, "")


def frontend_version() -> str:
    """Версия интерфейса — время последнего изменения его файлов (с кэшем на 2 секунды)."""
    global _version_cache
    checked, value = _version_cache
    if time.monotonic() - checked > 2 or not value:
        value = str(max(int(p.stat().st_mtime) for p in FRONTEND_DIR.resolve().rglob("*") if p.is_file()))
        _version_cache = (time.monotonic(), value)
    return value


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


DEMO_DB = PROJECT_ROOT / "demo" / "cqa-demo.sqlite3"


def _restore_demo(database: Path) -> None:
    """Первый запуск после клонирования: своей базы ещё нет — берём снимок с оценённым DEV-набором,
    чтобы интерфейс сразу показывал диалоги, оценки и апелляции без ключей модели и доступа к API."""
    if database.exists() or not DEMO_DB.exists():
        return
    database.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DEMO_DB, database)
    log.info("первый запуск: база создана из демо-снимка %s", DEMO_DB.name)


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

