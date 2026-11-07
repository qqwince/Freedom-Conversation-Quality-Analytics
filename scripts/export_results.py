"""Выгрузка results.json в формате организаторов из базы сервера.

    python scripts/export_results.py --dataset DEV                       # → results/dev/results.json
    python scripts/export_results.py --dataset <group_id> --prod         # → results/prod/results.json, ровно 10 записей

Берётся последняя оценка каждого диалога набора с учётом решений по апелляциям
(--no-appeals — исходные ответы модели). Перед записью файл проверяется по правилам формата 1.0.
Название команды — CQA_TEAM_NAME в .env или --team.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from cqa.config import PROJECT_ROOT, get_settings
from cqa.service import CQAService, ServiceError, export_dataset
from cqa.storage import Storage

PROD_RECORDS = 10


def write_results(dataset: str, prod: bool, team: str, mode: str, with_appeals: bool = True
                  ) -> tuple[Path, dict[str, Any], list[str]]:
    """Собирает results.json по набору, проверяет формат и записывает файл. Возвращает путь, документ и проблемы."""
    settings = get_settings()
    service = CQAService(Storage(settings.data_dir / "cqa.sqlite3"), provider_factory=lambda m=None: None,
                         api_url=None)
    doc, problems = export_dataset(service, dataset, team, mode, with_appeals)
    if prod and len(doc["results"]) != PROD_RECORDS:
        problems.append(f"в prod должно быть ровно {PROD_RECORDS} записей, а их {len(doc['results'])}")
    out = PROJECT_ROOT / "results" / ("prod" if prod else "dev") / "results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out, doc, problems


def report(out: Path, doc: dict[str, Any], problems: list[str]) -> bool:
    """Печатает сводку по файлу; True — формат в порядке."""
    ok = sum(r["status"] == "ok" for r in doc["results"])
    errors = [r for r in doc["results"] if r["status"] == "error"]
    print(f"{out.relative_to(PROJECT_ROOT)}: {len(doc['results'])} записей, ok {ok}, error {len(errors)}, "
          f"модель {doc['model']['name']} ({doc['model']['mode']}), команда {doc['team_name']}")
    for r in errors:
        print(f"  error {r['conversation_id']}: {r['error']}")
    if problems:
        print("Проблемы формата — исправьте до push:")
        for p in problems:
            print("  -", p)
    return not problems


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    get_settings()   # подхватывает .env: CQA_TEAM_NAME и CQA_MODEL_MODE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True, help="набор в базе (DEV или group_id контрольного)")
    ap.add_argument("--prod", action="store_true", help="контрольный набор → results/prod/results.json")
    ap.add_argument("--team", default=os.environ.get("CQA_TEAM_NAME"), help="название команды")
    ap.add_argument("--mode", default=os.environ.get("CQA_MODEL_MODE", "external_api"),
                    choices=["local", "external_api", "hybrid"])
    ap.add_argument("--no-appeals", action="store_true", help="не учитывать решения по апелляциям")
    args = ap.parse_args()
    if not args.team:
        ap.error("укажите --team или CQA_TEAM_NAME в .env")
    try:
        out, doc, problems = write_results(args.dataset, args.prod, args.team, args.mode, not args.no_appeals)
    except ServiceError as exc:
        raise SystemExit(f"Ошибка: {exc}") from exc
    if not report(out, doc, problems):
        raise SystemExit(1)
    print("Формат проверен. Дальше: git add, commit и push до стоп-кода.")


if __name__ == "__main__":
    main()
