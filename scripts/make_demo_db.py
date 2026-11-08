"""Снимок рабочей базы для запуска «из коробки»: demo/cqa-demo.sqlite3.

    python scripts/make_demo_db.py

Берётся из data/cqa.sqlite3 только то, что нужно, чтобы система после клонирования сразу показала
оценённые диалоги без ключей модели и без доступа к API:
- диалоги набора DEV;
- последняя оценка каждого диалога и её базовые оценки до той, где сохранён журнал вызовов модели;
- апелляции и оценки, на которые они ссылаются;
- все версии чек-листа и сводки операторов;
- автообновление выключено (без ссылки на API организаторов оно бы только писало ошибки).

При первом запуске сервер копирует снимок в data/cqa.sqlite3, если своей базы ещё нет.
"""

from __future__ import annotations

import json
import sqlite3
import sys

from cqa.config import PROJECT_ROOT, get_settings
from cqa.storage import Storage

DEMO_DB = PROJECT_ROOT / "demo" / "cqa-demo.sqlite3"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    source = get_settings().data_dir / "cqa.sqlite3"
    if not source.exists():
        raise SystemExit(f"Нет базы {source}")
    DEMO_DB.parent.mkdir(exist_ok=True)
    DEMO_DB.unlink(missing_ok=True)
    Storage(DEMO_DB)                                     # пустая база с актуальной схемой

    src = sqlite3.connect(source)
    rows = src.execute("SELECT id, conversation_id, data, created_at FROM evaluations")
    evaluations = {eid: (cid, json.loads(data), at) for eid, cid, data, at in rows}
    latest: dict[str, str] = {}
    for eid, (cid, _, at) in evaluations.items():
        if cid not in latest or at > evaluations[latest[cid]][2]:
            latest[cid] = eid
    keep: set[str] = set()
    for eid in latest.values():
        while eid in evaluations:                        # до оценки с журналом вызовов модели
            keep.add(eid)
            data = evaluations[eid][1]
            if (data.get("log") or {}).get("calls"):
                break
            eid = data.get("base_evaluation_id")
    keep |= {eid for (eid,) in src.execute("SELECT evaluation_id FROM appeals") if eid in evaluations}

    dst = sqlite3.connect(DEMO_DB)
    with dst:
        dst.executemany("INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?)",
                        src.execute("SELECT * FROM conversations WHERE dataset = 'DEV'"))
        dst.executemany("INSERT INTO checklists VALUES (?, ?, ?, ?)", src.execute("SELECT * FROM checklists"))
        dst.executemany("INSERT INTO evaluations VALUES (?, ?, ?, ?, ?, ?)",
                        [row for row in src.execute("SELECT * FROM evaluations") if row[0] in keep])
        dst.executemany("INSERT INTO appeals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [row for row in src.execute("SELECT * FROM appeals") if row[1] in keep])
        dst.executemany("INSERT INTO operator_feedback VALUES (?, ?, ?, ?)",
                        src.execute("SELECT * FROM operator_feedback"))
        sync = json.loads((src.execute("SELECT value FROM settings WHERE key = 'sync'").fetchone() or ["{}"])[0])
        dst.execute("INSERT INTO settings VALUES ('sync', ?)", (json.dumps({**sync, "enabled": False}),))
    dst.execute("VACUUM")
    counts = {t: dst.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("conversations", "checklists", "evaluations", "appeals", "operator_feedback")}
    dst.close()
    print(f"{DEMO_DB.relative_to(PROJECT_ROOT)}: {DEMO_DB.stat().st_size / 1e6:.1f} МБ, {counts}")


if __name__ == "__main__":
    main()
