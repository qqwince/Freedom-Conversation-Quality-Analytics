"""Эталонная разметка (gold): создание файла, разбиение tune/test, сводка статуса.

Команды:
    python scripts/gold.py init                  # заготовка eval/gold/DEV.json для всех диалогов
    python scripts/gold.py status                # сколько размечено и проверено

Формат eval/gold/<набор>.json:
    {"dataset": "DEV", "checklist_id": ..., "items": {
        "<conversation_id>": {
            "split": "tune" | "test",
            "status": "todo" | "draft" | "verified",
            "answers": {"C1": "yes", "C4": 15, ...},
            "notes": {"C7": "почему так"},
            "labeled_by": "имя"
        }}}

test — ~30 % диалогов из каждого языка и канала, выбранные детерминированно по хэшу id.
На test промпт не подгоняется: это честная оценка точности на «новых» диалогах.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from cqa.config import PROJECT_ROOT, get_settings

GOLD_DIR = PROJECT_ROOT / "eval" / "gold"
TEST_SHARE = 0.3


def split_ids(records: list[dict]) -> dict[str, str]:
    strata: dict[tuple[str, str], list[str]] = defaultdict(list)
    for r in records:
        strata[(r["channel"], r["language"])].append(r["conversation_id"])
    split: dict[str, str] = {}
    for ids in strata.values():
        ranked = sorted(ids, key=lambda i: hashlib.sha1(i.encode()).hexdigest())
        n_test = max(1, round(len(ranked) * TEST_SHARE))
        for pos, cid in enumerate(ranked):
            split[cid] = "test" if pos < n_test else "tune"
    return split


def cmd_init(dataset: Path, out: Path, force: bool) -> None:
    if out.exists() and not force:
        raise SystemExit(f"{out} уже существует (используйте --force, чтобы перезаписать)")
    records = json.loads(dataset.read_text(encoding="utf-8"))["records"]
    checklist = json.loads(get_settings().checklist_path.read_text(encoding="utf-8"))
    split = split_ids(records)
    gold = {
        "dataset": dataset.stem,
        "checklist_id": checklist["id"],
        "checklist_version": checklist["version"],
        "items": {
            r["conversation_id"]: {
                "split": split[r["conversation_id"]],
                "status": "todo",
                "language": r["language"],
                "channel": r["channel"],
                "answers": {},
                "notes": {},
                "labeled_by": None,
            }
            for r in records
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(gold, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Создан {out}: {len(records)} диалогов, test = {sum(v == 'test' for v in split.values())}")


def cmd_status(path: Path) -> None:
    items = json.loads(path.read_text(encoding="utf-8"))["items"]
    by = Counter((v["split"], v["status"]) for v in items.values())
    for split in ("tune", "test"):
        row = ", ".join(f"{s}: {by[(split, s)]}" for s in ("todo", "draft", "verified"))
        print(f"{split:<5} {row}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    settings = get_settings()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["init", "status"])
    ap.add_argument("--dataset", type=Path, default=settings.data_dir / "DEV.json")
    ap.add_argument("--gold", type=Path, default=GOLD_DIR / "DEV.json")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if args.command == "init":
        cmd_init(args.dataset, args.gold, args.force)
    else:
        cmd_status(args.gold)


if __name__ == "__main__":
    main()
