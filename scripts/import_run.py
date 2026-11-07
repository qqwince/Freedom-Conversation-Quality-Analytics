"""Загрузка результатов прогона scripts/evaluate.py в базу сервера, чтобы они были видны в интерфейсе.

    python scripts/import_run.py eval/runs/<run_id> [eval/runs/<run_id2> ...]

Оценки сохраняются как есть, с журналом вызовов модели. Повторный импорт пропускает уже
загруженные оценки. Версия чек-листа должна существовать в базе (исходная — v1).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cqa.config import get_settings
from cqa.storage import NotFound, Storage


def _content(checklist: dict) -> dict:
    return {k: v for k, v in checklist.items() if k not in ("version", "saved_at")}


def sync_checklist(storage: Storage, path: Path) -> int:
    """Версия в базе, совпадающая по содержанию с файлом чек-листа (при необходимости — новая)."""
    config = json.loads(path.read_text(encoding="utf-8"))
    try:
        latest = storage.get_checklist(config["id"])
        if _content(latest) == _content(config):
            return latest["version"]
    except NotFound:
        pass
    saved = storage.save_checklist(config)
    print(f"Чек-лист из {path.name} сохранён в базу как версия {saved['version']}")
    return saved["version"]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--sync-checklist", action="store_true",
                    help="прогон сделан по config/checklist.default.json: сохранить его в базу новой версией "
                         "(если отличается от последней) и привязать оценки к этой версии")
    args = ap.parse_args()

    settings = get_settings()
    storage = Storage(settings.data_dir / "cqa.sqlite3")
    version_override = sync_checklist(storage, settings.checklist_path) if args.sync_checklist else None
    imported = skipped = 0
    for run in args.runs:
        for path in sorted(run.glob("*.json")):
            if path.name == "summary.json":
                continue
            evaluation = json.loads(path.read_text(encoding="utf-8"))
            if version_override is not None:
                evaluation["checklist_version"] = version_override
            try:
                storage.get_evaluation(evaluation["id"])
                skipped += 1
                continue
            except NotFound:
                pass
            try:
                storage.get_conversation(evaluation["conversation_id"])
                storage.get_checklist(evaluation["checklist_id"], evaluation["checklist_version"])
            except NotFound as exc:
                print(f"пропуск {path.name}: {exc}")
                skipped += 1
                continue
            storage.save_evaluation(evaluation)
            imported += 1
    print(f"Загружено оценок: {imported}, пропущено: {skipped}")


if __name__ == "__main__":
    main()
