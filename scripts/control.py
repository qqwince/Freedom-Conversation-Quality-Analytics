"""Час контроля одной командой: загрузка набора → оценка → results/prod/results.json → (по флагу) push.

    python scripts/control.py --group-id <ID>                     # загрузить, оценить, выгрузить
    python scripts/control.py --group-id <ID> --criteria C9       # после замены критерия: переоценить только его
    python scripts/control.py --group-id <ID> --push              # то же и сразу commit + push файла
    python scripts/control.py --group-id DEV --dev                # репетиция на DEV → results/dev/results.json

Работает через запущенный сервер (uvicorn), поэтому прогресс и результаты сразу видны в интерфейсе.
Неудачные диалоги переоцениваются ещё раз; если и это не помогло, они попадают в файл со status = "error",
как требует формат. Время каждого шага печатается — по нему видно, сколько займёт контроль.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from typing import Any

import httpx

from cqa.config import PROJECT_ROOT, get_settings
from export_results import report, write_results

ADMIN = {"X-CQA-User": "admin"}
POLL_SEC = 2.0


class Step:
    """Шаг контроля с замером времени."""

    def __init__(self, title: str) -> None:
        self.title = title

    def __enter__(self) -> Step:
        self.start = time.monotonic()
        print(f"\n▶ {self.title}", flush=True)
        return self

    def __exit__(self, *exc: object) -> None:
        print(f"  {'✓' if exc[0] is None else '✗'} {time.monotonic() - self.start:.0f} с", flush=True)


class Server:
    def __init__(self, base: str) -> None:
        self.http = httpx.Client(base_url=base, headers=ADMIN, timeout=120)

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self.http.request(method, path, **kwargs)
        except httpx.ConnectError as exc:
            raise SystemExit(f"Сервер не отвечает ({self.http.base_url}). Запустите:\n"
                             "  .venv/Scripts/python -m uvicorn cqa.api:create_app --factory --port 8000") from exc
        if response.status_code >= 400:
            detail = response.json().get("detail") if "json" in response.headers.get("content-type", "") else ""
            raise SystemExit(f"Ошибка сервера {response.status_code} на {path}: {detail or response.text[:300]}")
        return response.json()

    def run_job(self, ids: list[str], checklist_id: str, criteria: list[str] | None) -> list[str]:
        """Запускает оценку и ждёт её; возвращает id диалогов, которые оценить не удалось."""
        job = self.call("POST", "/api/evaluations",
                        json={"conversation_ids": ids, "checklist_id": checklist_id, "criteria_ids": criteria})
        shown = -1
        while job["status"] != "finished":
            time.sleep(POLL_SEC)
            job = self.call("GET", f"/api/jobs/{job['id']}")
            done = job["done"] + job["failed"]
            if done != shown:
                print(f"  {done} / {job['total']}" + (f" (ошибок {job['failed']})" if job["failed"] else ""),
                      flush=True)
                shown = done
        return [i["conversation_id"] for i in job["items"] if i["status"] != "done"]


def git(*args: str) -> None:
    print("  $ git " + " ".join(args), flush=True)
    subprocess.run(["git", *args], cwd=PROJECT_ROOT, check=True)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    get_settings()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--group-id", required=True, help="group_id контрольного набора от организаторов")
    ap.add_argument("--criteria", nargs="+", metavar="ID", help="переоценить только эти критерии (после замены)")
    ap.add_argument("--checklist", default="chat-default", help="id чек-листа (берётся последняя версия)")
    ap.add_argument("--dev", action="store_true", help="репетиция: писать в results/dev, а не в prod")
    ap.add_argument("--skip-import", action="store_true", help="набор уже загружен")
    ap.add_argument("--push", action="store_true", help="сразу git add + commit + push файла результатов")
    ap.add_argument("--server", default=os.environ.get("CQA_SERVER", "http://localhost:8000"))
    ap.add_argument("--team", default=os.environ.get("CQA_TEAM_NAME"))
    ap.add_argument("--mode", default=os.environ.get("CQA_MODEL_MODE", "external_api"),
                    choices=["local", "external_api", "hybrid"])
    args = ap.parse_args()
    if not args.team:
        ap.error("укажите --team или CQA_TEAM_NAME в .env")

    started = time.monotonic()
    server = Server(args.server)
    health = server.call("GET", "/api/health")
    checklist = server.call("GET", f"/api/checklists/{args.checklist}")
    print(f"Сервер: модель {health['model']}, голосов {health['votes']}; "
          f"чек-лист {checklist['id']} v{checklist['version']}, критериев {len(checklist['criteria'])}")
    unknown = sorted(set(args.criteria or []) - {c["id"] for c in checklist["criteria"]})
    if unknown:
        raise SystemExit(f"В чек-листе v{checklist['version']} нет критериев {', '.join(unknown)} — "
                         "сначала сохраните новую версию в интерфейсе.")

    if not args.skip_import:
        with Step(f"Загрузка набора {args.group_id} из API организаторов"):
            imported = server.call("POST", "/api/datasets/import", json={"group_id": args.group_id})
            print(f"  набор {imported['dataset']}: {imported['imported']} диалогов, новых {len(imported['new_ids'])}")
            for w in imported.get("warnings", []):
                print("  предупреждение:", w)
    dataset = args.group_id
    ids = [c["conversation_id"] for c in server.call("GET", "/api/conversations", params={"dataset": dataset})]
    if not ids:
        raise SystemExit(f"В базе нет диалогов набора {dataset}")

    what = f"критериев {', '.join(args.criteria)}" if args.criteria else "всех критериев"
    with Step(f"Оценка {what}: {len(ids)} диалогов"):
        failed = server.run_job(ids, checklist["id"], args.criteria)
    if failed:
        with Step(f"Повтор для {len(failed)} неудачных: {', '.join(failed)}"):
            failed = server.run_job(failed, checklist["id"], args.criteria)
        if failed:
            print(f"  ⚠ не удалось оценить: {', '.join(failed)} — попадут в файл со status = error")

    with Step("Выгрузка и проверка results.json"):
        out, doc, problems = write_results(dataset, not args.dev, args.team, args.mode)
        ok = report(out, doc, problems)
    if not ok:
        raise SystemExit("Файл записан, но формат с ошибками — push не выполнен.")

    rel = out.relative_to(PROJECT_ROOT).as_posix()
    if args.push:
        with Step("Commit и push"):
            git("add", rel)
            git("commit", "-m", f"Add {rel} for {dataset} (checklist v{checklist['version']})")
            git("push")
    total = time.monotonic() - started
    print(f"\nГотово за {total / 60:.1f} мин.")
    if not args.push:
        print(f"Осталось отправить файл:\n  git add {rel}\n  git commit -m \"Add {rel}\"\n  git push")


if __name__ == "__main__":
    main()
