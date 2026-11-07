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

from cqa.config import PROJECT_ROOT, get_settings
from cqa.service import CQAService, ServiceError, export_dataset
from cqa.storage import Storage

PROD_RECORDS = 10


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    settings = get_settings()
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

    service = CQAService(Storage(settings.data_dir / "cqa.sqlite3"), provider_factory=lambda m=None: None,
                         api_url=None)
    try:
        doc, problems = export_dataset(service, args.dataset, args.team, args.mode, not args.no_appeals)
    except ServiceError as exc:
        raise SystemExit(f"Ошибка: {exc}") from exc
    if args.prod and len(doc["results"]) != PROD_RECORDS:
        problems.append(f"в prod должно быть ровно {PROD_RECORDS} записей, а их {len(doc['results'])}")

    out = PROJECT_ROOT / "results" / ("prod" if args.prod else "dev") / "results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    ok = sum(r["status"] == "ok" for r in doc["results"])
    errors = [r for r in doc["results"] if r["status"] == "error"]
    print(f"{out.relative_to(PROJECT_ROOT)}: {len(doc['results'])} записей, ok {ok}, error {len(errors)}, "
          f"модель {doc['model']['name']} ({doc['model']['mode']})")
    for r in errors:
        print(f"  error {r['conversation_id']}: {r['error']}")
    if problems:
        print("Проблемы формата — исправьте до push:")
        for p in problems:
            print("  -", p)
        raise SystemExit(1)
    print("Формат проверен. Дальше: git add, commit и push до стоп-кода.")


if __name__ == "__main__":
    main()
