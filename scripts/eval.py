"""Замер точности прогона относительно эталона.

    python scripts/eval.py                          # последний прогон, эталон DEV, часть tune
    python scripts/eval.py --run eval/runs/<id> --split test
    python scripts/eval.py --split all --status draft   # включая непроверенную разметку
    python scripts/eval.py --errors C2 C7           # показать расхождения по критериям

Отчёт печатается и сохраняется в eval/reports/.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cqa.config import PROJECT_ROOT, get_settings
from cqa.metrics import compare

RUNS_DIR = PROJECT_ROOT / "eval" / "runs"
REPORTS_DIR = PROJECT_ROOT / "eval" / "reports"
STATUS_LEVEL = {"todo": 0, "draft": 1, "verified": 2}


def latest_run() -> Path:
    runs = sorted(p for p in RUNS_DIR.iterdir() if p.is_dir())
    if not runs:
        raise SystemExit("Нет прогонов в eval/runs — сначала запустите scripts/evaluate.py")
    return runs[-1]


def load_predictions(run_dir: Path) -> dict[str, dict]:
    preds = {}
    for f in run_dir.glob("*.json"):
        if f.name == "summary.json":
            continue
        ev = json.loads(f.read_text(encoding="utf-8"))
        preds[ev["conversation_id"]] = {r["criterion_id"]: r["answer"] for r in ev["results"]}
    return preds


def pct(x: float | None) -> str:
    return "  —  " if x is None else f"{x * 100:5.1f}%"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    settings = get_settings()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", type=Path, help="папка прогона (по умолчанию последняя)")
    ap.add_argument("--gold", type=Path, default=PROJECT_ROOT / "eval" / "gold" / "DEV.json")
    ap.add_argument("--split", choices=["tune", "test", "all"], default="tune")
    ap.add_argument("--status", choices=["draft", "verified"], default="verified",
                    help="минимальный статус разметки")
    ap.add_argument("--errors", nargs="*", metavar="CRITERION", help="показать расхождения (без id — все)")
    ap.add_argument("--strict", action="store_true", help="неоценённые диалоги считать ошибкой (финальный замер)")
    args = ap.parse_args()

    run_dir = args.run or latest_run()
    checklist = json.loads(settings.checklist_path.read_text(encoding="utf-8"))
    items = json.loads(args.gold.read_text(encoding="utf-8"))["items"]
    selected = {
        cid: item for cid, item in items.items()
        if (args.split == "all" or item["split"] == args.split)
        and STATUS_LEVEL[item["status"]] >= STATUS_LEVEL[args.status] and item["answers"]
    }
    if not selected:
        raise SystemExit("Нет размеченных диалогов под эти условия (см. --split и --status)")

    predictions = load_predictions(run_dir)
    not_evaluated = sorted(cid for cid in selected if cid not in predictions)
    if not args.strict:
        # Незавершённый прогон: считаем точность по оценённым диалогам, остальные — отдельной строкой.
        selected = {cid: item for cid, item in selected.items() if cid in predictions}
        if not selected:
            raise SystemExit("В прогоне нет ни одного диалога из выбранной части эталона")
    report = compare(checklist, {k: v["answers"] for k, v in selected.items()}, predictions,
                     {k: v["language"] for k, v in selected.items()})

    print(f"Прогон {run_dir.name} · эталон {args.gold.name} · {args.split} · статус ≥ {args.status}")
    print(f"Диалогов в замере: {report.conversations}, ещё не оценено системой: {len(not_evaluated)}"
          + (" (считаются ошибкой, --strict)" if args.strict and not_evaluated else ""))
    print(f"Точность ответов: {pct(report.answer_accuracy)}   "
          f"MAE итога: {f'{report.total_mae:.2f}' if report.total_mae is not None else '—'}   "
          f"итог в пределах 5 баллов: {pct(report.total_within_tolerance)}")
    print("\nКритерий   точность   MAE   ошибки (эталон→система)")
    for cid, s in report.criteria.items():
        if not s.n:
            continue
        errors = ", ".join(f"{g}→{p}×{n}" for (g, p), n in s.confusion.most_common() if g != p)
        mae = f"{s.mae:5.2f}" if s.mae is not None else "   — "
        print(f"{cid:<9} {pct(s.accuracy)}   {mae}   {errors}")
    print("\nПо языкам:")
    for lang, row in report.by_language.items():
        print(f"  {lang}: диалогов {row['conversations']}, точность {pct(row['answer_accuracy'])}, "
              f"MAE итога {row['total_mae']}")
    print(f"Грубые ошибки: {report.critical}")

    if args.errors is not None:
        wanted = set(args.errors)
        print("\nРасхождения:")
        for m in report.mismatches:
            if not wanted or m.criterion_id in wanted:
                note = items[m.conversation_id].get("notes", {}).get(m.criterion_id, "")
                print(f"  {m.conversation_id} {m.criterion_id}: эталон {m.gold}, система {m.predicted}"
                      + (f" — {note}" if note else ""))

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / f"{run_dir.name}--{args.split}.json"
    out.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nОтчёт: {out.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
