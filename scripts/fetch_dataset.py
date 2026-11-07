"""Загрузка набора диалогов из API организаторов и его нормализация.

Использование:
    python scripts/fetch_dataset.py --url "<ссылка GET для DEV>"
    python scripts/fetch_dataset.py --url "<ссылка>" --group-id CONTROL123
    python scripts/fetch_dataset.py --file raw.json      # если ответ сохранён из браузера

Ссылку можно задать и через переменную окружения CQA_API_URL.
Результат: data/<group_id>.json (нормализованный) и data/<group_id>.raw.json (как пришло).
Только стандартная библиотека Python.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
ROLES = {"client", "operator", "bot", "system"}


def build_url(url: str, group_id: str | None) -> str:
    """Подставляет group_id в query-параметр, сохраняя остальные параметры ссылки."""
    if not group_id:
        return url
    parts = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    query["group_id"] = group_id
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "cqa-hackathon"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_offset(ts: str, first: datetime | None) -> tuple[float | None, datetime | None]:
    """MM:SS (или HH:MM:SS) -> секунды; ISO 8601 -> секунды от первой реплики."""
    ts = (ts or "").strip()
    if not ts:
        return None, first
    if "T" in ts or (ts.count("-") >= 2):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None, first
        if first is None:
            first = dt
        return (dt - first).total_seconds(), first
    try:
        nums = [int(p) for p in ts.split(":")]
    except ValueError:
        return None, first
    sec = 0
    for n in nums:
        sec = sec * 60 + n
    return float(sec), first


def normalize(payload: dict) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    if not payload.get("isSuccess", False):
        raise SystemExit(f"API вернул ошибку: {payload.get('error')!r}")
    data = payload.get("data") or {}
    records = data.get("records") or []
    group_id = data.get("dataset_group_id") or "UNKNOWN"
    if data.get("total_records") not in (None, len(records)):
        warnings.append(f"total_records={data.get('total_records')}, а записей {len(records)}")

    out = []
    for rec in records:
        cid = rec.get("conversation_id", "?")
        transcript = rec.get("transcript")
        if isinstance(transcript, str):  # на случай, если придёт строкой
            transcript = json.loads(transcript)
        first = None
        messages = []
        for i, m in enumerate(transcript or []):
            speaker = (m.get("speaker") or "").strip().lower()
            if speaker not in ROLES:
                warnings.append(f"{cid}[{i}]: неизвестная роль {speaker!r}")
            offset, first = parse_offset(m.get("timestamp", ""), first)
            if offset is None:
                warnings.append(f"{cid}[{i}]: не разобран timestamp {m.get('timestamp')!r}")
            messages.append({
                "idx": i,
                "timestamp": m.get("timestamp"),
                "offset_sec": offset,
                "speaker": speaker,
                "text": m.get("text", ""),
            })
        out.append({
            "conversation_id": cid,
            "dataset_group_id": group_id,
            "channel": rec.get("channel"),
            "language": rec.get("language"),
            "audio_url": rec.get("audio_url"),
            "transcript": messages,
        })
    return {"dataset_group_id": group_id, "total_records": len(out), "records": out}, warnings


def print_stats(ds: dict) -> None:
    recs = ds["records"]
    print(f"Набор {ds['dataset_group_id']}: {len(recs)} диалогов")
    print("  каналы:", dict(Counter(r["channel"] for r in recs)))
    print("  языки: ", dict(Counter(r["language"] for r in recs)))
    roles = Counter(m["speaker"] for r in recs for m in r["transcript"])
    print("  реплики по ролям:", dict(roles))
    lens = [len(r["transcript"]) for r in recs] or [0]
    print(f"  длина диалога: мин {min(lens)}, сред {sum(lens) / len(lens):.1f}, макс {max(lens)}")
    with_audio = sum(1 for r in recs if r["audio_url"])
    print(f"  с audio_url: {with_audio}")


def load_env() -> None:
    """Подхватывает CQA_API_URL из .env в корне проекта (файл не коммитится)."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def main() -> None:
    load_env()
    for stream in (sys.stdout, sys.stderr):  # кириллица в консоли Windows
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap =argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--url", help="ссылка GET из задания (или env CQA_API_URL)")
    src.add_argument("--file", help="локальный JSON с ответом API")
    ap.add_argument("--group-id", help="подставить другой group_id (контрольный набор)")
    args = ap.parse_args()

    if args.file:
        payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
    else:
        url = args.url or os.environ.get("CQA_API_URL")
        if not url:
            ap.error("укажите --url, --file или переменную CQA_API_URL")
        payload = fetch(build_url(url, args.group_id))

    ds, warnings = normalize(payload)
    DATA_DIR.mkdir(exist_ok=True)
    gid = ds["dataset_group_id"]
    (DATA_DIR / f"{gid}.raw.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA_DIR / f"{gid}.json").write_text(json.dumps(ds, ensure_ascii=False, indent=2), encoding="utf-8")

    print_stats(ds)
    print(f"Сохранено: data/{gid}.json")
    if warnings:
        print(f"Предупреждений: {len(warnings)}", file=sys.stderr)
        for w in warnings[:20]:
            print("  -", w, file=sys.stderr)


if __name__ == "__main__":
    main()
