"""Пакетная оценка диалогов из командной строки.

Примеры:
    python scripts/evaluate.py --ids p1-ru-001 p1-kz-001
    python scripts/evaluate.py --lang KZ --limit 5 --votes 3
    python scripts/evaluate.py --all --workers 4

Результаты: eval/runs/<run_id>/<conversation_id>.json и summary.json.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from cqa.config import PROJECT_ROOT, get_settings
from cqa.evaluator import Evaluator
from cqa.llm import LLMError, create_provider
from cqa.models import Conversation

RUNS_DIR = PROJECT_ROOT / "eval" / "runs"


def load_conversations(path: Path) -> list[Conversation]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Conversation.model_validate(r) for r in data["records"]]


def select(convs: list[Conversation], args: argparse.Namespace) -> list[Conversation]:
    if args.ids:
        by_id = {c.conversation_id: c for c in convs}
        missing = [i for i in args.ids if i not in by_id]
        if missing:
            raise SystemExit(f"Нет в наборе: {', '.join(missing)}")
        return [by_id[i] for i in args.ids]
    selected = [c for c in convs if not args.lang or c.language == args.lang.upper()]
    if args.channel:
        selected = [c for c in selected if c.channel == args.channel]
    if not args.all:
        selected = selected[: args.limit]
    return selected


def summary_row(ev: dict[str, Any]) -> str:
    answers = " ".join(f"{r['criterion_id']}={r['answer']}" for r in ev["results"])
    total = "—" if ev["total"] is None else f"{ev['total']:6.2f}"
    flags = (" ⚠ грубая ошибка" if ev["critical"] and ev["critical"]["flag"] else "") + \
            (" ? проверить" if ev["needs_review"] else "")
    return f"{ev['conversation_id']:<12} {total}  {answers}{flags}"


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    settings = get_settings()

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", type=Path, default=settings.data_dir / "DEV.json")
    ap.add_argument("--checklist", type=Path, default=settings.checklist_path)
    ap.add_argument("--ids", nargs="*", help="конкретные conversation_id")
    ap.add_argument("--lang", choices=["RU", "KZ", "EN", "ru", "kz", "en"])
    ap.add_argument("--channel", choices=["agent chat", "voice call"])
    ap.add_argument("--limit", type=int, default=3, help="сколько диалогов взять (без --all)")
    ap.add_argument("--all", action="store_true", help="все подходящие диалоги")
    ap.add_argument("--votes", type=int, default=settings.votes)
    ap.add_argument("--workers", type=int, default=settings.workers)
    ap.add_argument("--model", default=settings.llm_model)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    checklist = json.loads(args.checklist.read_text(encoding="utf-8"))
    convs = select(load_conversations(args.dataset), args)
    evaluator = Evaluator(create_provider(args.model), votes=args.votes)

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{args.model}"
    out_dir = RUNS_DIR / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Оценка {len(convs)} диалогов · модель {args.model} · голосов {args.votes} → {out_dir}")

    started = time.monotonic()
    results: list[dict[str, Any]] = []
    failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(evaluator.evaluate, c, checklist): c.conversation_id for c in convs}
        for future in as_completed(futures):
            cid = futures[future]
            try:
                ev = future.result()
            except LLMError as exc:
                failures[cid] = str(exc)
                print(f"{cid:<12} ОШИБКА: {exc}", flush=True)
                continue
            (out_dir / f"{cid}.json").write_text(json.dumps(ev, ensure_ascii=False, indent=2), encoding="utf-8")
            results.append(ev)
            print(summary_row(ev), flush=True)

    usage = {k: sum(r["usage"][k] for r in results) for k in ("calls", "input_tokens", "output_tokens")}
    summary = {
        "run_id": run_id, "model": args.model, "votes": args.votes,
        "dataset": str(args.dataset.name), "checklist_version": checklist.get("version"),
        "evaluated": len(results), "failed": failures, "usage": usage,
        "wall_time_sec": round(time.monotonic() - started, 1),
        "totals": {r["conversation_id"]: r["total"] for r in results},
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    n = max(len(results), 1)
    print(f"\nГотово: {len(results)} ок, {len(failures)} ошибок, {summary['wall_time_sec']} с. "
          f"Токены на диалог: вход {usage['input_tokens'] // n}, выход {usage['output_tokens'] // n}.")


if __name__ == "__main__":
    main()
