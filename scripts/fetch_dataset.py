"""Загрузка набора диалогов из API организаторов в data/<group_id>.json.

    python scripts/fetch_dataset.py                       # ссылка из CQA_API_URL (.env), group_id из ссылки
    python scripts/fetch_dataset.py --group-id CONTROL123 # контрольный набор
    python scripts/fetch_dataset.py --file raw.json       # ответ, сохранённый из браузера

Сохраняются нормализованный набор и исходный ответ (data/<group_id>.raw.json).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

from cqa.config import get_settings
from cqa.dataset import fetch_raw, normalize, to_file_payload
from cqa.models import Conversation


def print_stats(group_id: str, conversations: list[Conversation]) -> None:
    print(f"Набор {group_id}: {len(conversations)} диалогов")
    print("  каналы:", dict(Counter(c.channel for c in conversations)))
    print("  языки: ", dict(Counter(c.language for c in conversations)))
    print("  реплики по ролям:", dict(Counter(m.speaker for c in conversations for m in c.transcript)))
    lengths = [len(c.transcript) for c in conversations] or [0]
    print(f"  длина диалога: мин {min(lengths)}, сред {sum(lengths) / len(lengths):.1f}, макс {max(lengths)}")
    print(f"  с audio_url: {sum(1 for c in conversations if c.audio_url)}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    settings = get_settings()

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = ap.add_mutually_exclusive_group()
    source.add_argument("--url", help="ссылка GET из задания (по умолчанию CQA_API_URL)")
    source.add_argument("--file", type=Path, help="локальный JSON с ответом API")
    ap.add_argument("--group-id", help="подставить другой group_id (контрольный набор)")
    args = ap.parse_args()

    if args.file:
        payload = json.loads(args.file.read_text(encoding="utf-8"))
    else:
        url = args.url or os.environ.get("CQA_API_URL")
        if not url:
            ap.error("укажите --url, --file или CQA_API_URL в .env")
        payload = fetch_raw(url, args.group_id)

    group_id, conversations, warnings = normalize(payload)
    settings.data_dir.mkdir(exist_ok=True)
    raw_path = settings.data_dir / f"{group_id}.raw.json"
    raw_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    out_path = settings.data_dir / f"{group_id}.json"
    out_path.write_text(json.dumps(to_file_payload(group_id, conversations), ensure_ascii=False, indent=2),
                        encoding="utf-8")

    print_stats(group_id, conversations)
    print(f"Сохранено: data/{out_path.name}")
    if warnings:
        print(f"Предупреждений: {len(warnings)}", file=sys.stderr)
        for w in warnings[:20]:
            print("  -", w, file=sys.stderr)


if __name__ == "__main__":
    main()
