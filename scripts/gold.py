"""Эталонная разметка (gold): создание файла, разбиение tune/test, сводка статуса.

Команды:
    python scripts/gold.py init                  # заготовка eval/gold/DEV.json для всех диалогов
    python scripts/gold.py status                # сколько размечено и проверено
    python scripts/gold.py review --split test   # лист слепой проверки людьми: транскрипты + таблица ответов
    python scripts/gold.py apply eval/gold/review/test.csv   # принять ответы людей → status = verified

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
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from cqa.config import PROJECT_ROOT, get_settings

GOLD_DIR = PROJECT_ROOT / "eval" / "gold"
REVIEW_DIR = GOLD_DIR / "review"
TEST_SHARE = 0.3
CRITERIA = ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"]
PRIORITY = ["C4", "C6", "C5"]          # здесь система ошибается чаще всего — проверять в первую очередь
SPEAKERS = {"client": "Клиент", "operator": "Оператор", "bot": "Бот", "system": "Система"}


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


def _allowed(criterion: dict) -> list[str]:
    if criterion["type"] == "scale":
        values = [str(st["value"]) for st in sorted(criterion["steps"], key=lambda st: -st["value"])]
    else:
        values = ["yes", "no"]
    return values + (["na"] if criterion.get("na_allowed") else [])


def cmd_review(dataset: Path, gold_path: Path, split: str) -> None:
    """Слепой лист проверки: транскрипты и правила без ответов модели и эталона, плюс CSV для ответов."""
    records = {r["conversation_id"]: r for r in json.loads(dataset.read_text(encoding="utf-8"))["records"]}
    items = json.loads(gold_path.read_text(encoding="utf-8"))["items"]
    checklist = json.loads(get_settings().checklist_path.read_text(encoding="utf-8"))
    criteria = {c["id"]: c for c in checklist["criteria"]}
    ids = sorted(cid for cid, it in items.items()
                 if it["split"] == split and cid in records and records[cid]["channel"] == "agent chat")

    lines = [
        f"# Слепая проверка эталона: {split}, {len(ids)} чатов",
        "",
        "Не смотрите в интерфейс и в eval/gold/DEV.json, пока не поставите свой ответ, иначе проверка",
        f"перестаёт быть независимой. Ответы пишите в таблицу `{split}.csv` (Excel или Google Sheets).",
        f"Сначала {', '.join(PRIORITY)}: в них система ошибается чаще всего. Пустая ячейка значит «не проверял».",
        "Значения: yes / no / na или ступень шкалы. Реплики нумеруются с 1.",
        "",
        "## Критерии",
        "",
    ]
    for cid in CRITERIA:
        c = criteria[cid]
        lines.append(f"**{cid}. {c['title']}** — `{' / '.join(_allowed(c))}`")
        lines.append(f"  {c['description']}")
        for st in sorted(c.get("steps") or [], key=lambda st: -st["value"]):
            lines.append(f"  - {st['value']} — {st['text']}")
        if c.get("na_rule"):
            lines.append(f"  - N/A — {c['na_rule']}")
        lines.append("")
    for cid in ids:
        r = records[cid]
        lines += ["---", "", f"## {cid} · {r['language']}", ""]
        for m in r["transcript"]:
            text = " ".join(m["text"].split())
            lines.append(f"{m['idx'] + 1}. **{SPEAKERS.get(m['speaker'], m['speaker'])}** [{m['timestamp']}]: {text}")
        lines.append("")

    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    md, sheet = REVIEW_DIR / f"{split}.md", REVIEW_DIR / f"{split}.csv"
    md.write_text("\n".join(lines), encoding="utf-8")
    if sheet.exists():
        print(f"{sheet.relative_to(PROJECT_ROOT)} уже есть: таблицу не перезаписываю, чтобы не стереть ответы")
    else:
        with sheet.open("w", encoding="utf-8-sig", newline="") as f:   # BOM: Excel правильно откроет кириллицу
            writer = csv.writer(f, delimiter=";")
            writer.writerow(["conversation_id", *CRITERIA, "reviewer", "comment"])
            writer.writerows([cid, *[""] * len(CRITERIA), "", ""] for cid in ids)
    print(f"Лист проверки: {md.relative_to(PROJECT_ROOT)} · таблица ответов: {sheet.relative_to(PROJECT_ROOT)}")


def cmd_apply(gold_path: Path, sheet: Path) -> None:
    """Ответы людей из таблицы → эталон; полностью проверенный диалог получает status = verified."""
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    checklist = json.loads(get_settings().checklist_path.read_text(encoding="utf-8"))
    allowed = {c["id"]: _allowed(c) for c in checklist["criteria"]}
    changed, verified, problems = [], 0, []
    with sheet.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            cid = row["conversation_id"].strip()
            item = gold["items"].get(cid)
            filled = {k: row[k].strip().lower() for k in CRITERIA if (row.get(k) or "").strip()}
            if item is None or not filled:
                continue
            bad = {k: v for k, v in filled.items() if v not in allowed[k]}
            if bad:
                problems.append(f"{cid}: недопустимые значения {bad}")
                continue
            comment = (row.get("comment") or "").strip()
            for k, v in filled.items():
                new = int(v) if v.isdigit() else v
                old = item["answers"].get(k)
                if old != new:
                    changed.append(f"{cid} {k}: {old} → {new}")
                    item["notes"][k] = f"проверено человеком, было {old}. {comment}".strip()
                item["answers"][k] = new
            if set(filled) == set(CRITERIA):
                item["status"] = "verified"
                verified += 1
            item["labeled_by"] = (row.get("reviewer") or "").strip() or "team"
    if problems:
        raise SystemExit("Исправьте таблицу:\n  " + "\n  ".join(problems))
    gold_path.write_text(json.dumps(gold, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Принято: полностью проверенных диалогов {verified}, изменённых ответов {len(changed)}")
    for line in changed:
        print("  " + line)
    print("Точность по проверенному: python scripts/eval.py --split all --status verified")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    settings = get_settings()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["init", "status", "review", "apply"])
    ap.add_argument("sheet", nargs="?", type=Path, help="для apply: таблица с ответами людей")
    ap.add_argument("--split", choices=["tune", "test"], default="test")
    ap.add_argument("--dataset", type=Path, default=settings.data_dir / "DEV.json")
    ap.add_argument("--gold", type=Path, default=GOLD_DIR / "DEV.json")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if args.command == "init":
        cmd_init(args.dataset, args.gold, args.force)
    elif args.command == "review":
        cmd_review(args.dataset, args.gold, args.split)
    elif args.command == "apply":
        if not args.sheet:
            ap.error("укажите таблицу: gold.py apply eval/gold/review/test.csv")
        cmd_apply(args.gold, args.sheet)
    else:
        cmd_status(args.gold)


if __name__ == "__main__":
    main()
