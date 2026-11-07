"""Получение и нормализация наборов диалогов из API организаторов.

Нормализация: к каждой реплике добавляются idx (номер для ссылок в обоснованиях) и offset_sec
(секунды от начала диалога). Исходные timestamp сохраняются без изменений.
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import datetime
from typing import Any

import httpx

from cqa.models import Conversation

ROLES = {"client", "operator", "bot", "system"}


class DatasetError(RuntimeError):
    pass


def build_url(url: str, group_id: str | None) -> str:
    """Подставляет group_id в query-параметр, сохраняя остальные параметры ссылки (подпись sig и т. п.)."""
    if not group_id:
        return url
    parts = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    query["group_id"] = group_id
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def fetch_raw(url: str, group_id: str | None = None, timeout: float = 60) -> dict[str, Any]:
    try:
        response = httpx.get(build_url(url, group_id), timeout=timeout, headers={"Accept": "application/json"})
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise DatasetError(f"не удалось получить набор: {exc}") from exc


def parse_offset(timestamp: str, first: datetime | None) -> tuple[float | None, datetime | None]:
    """MM:SS (или HH:MM:SS) → секунды; ISO 8601 → секунды от первой ISO-метки диалога."""
    ts = (timestamp or "").strip()
    if not ts:
        return None, first
    if "T" in ts or ts.count("-") >= 2:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None, first
        first = first or dt
        return (dt - first).total_seconds(), first
    try:
        parts = [int(p) for p in ts.split(":")]
    except ValueError:
        return None, first
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    return float(seconds), first


def normalize_record(record: dict[str, Any], group_id: str) -> tuple[Conversation, list[str]]:
    cid = record.get("conversation_id", "?")
    warnings: list[str] = []
    transcript = record.get("transcript") or []
    if isinstance(transcript, str):
        transcript = json.loads(transcript)

    first: datetime | None = None
    messages = []
    for i, m in enumerate(transcript):
        speaker = (m.get("speaker") or "").strip().lower()
        if speaker not in ROLES:
            warnings.append(f"{cid}[{i}]: неизвестная роль {speaker!r}, считаю системной")
            speaker = "system"
        offset, first = parse_offset(m.get("timestamp", ""), first)
        if offset is None:
            warnings.append(f"{cid}[{i}]: не разобран timestamp {m.get('timestamp')!r}")
        messages.append({"idx": i, "timestamp": m.get("timestamp") or "", "offset_sec": offset,
                         "speaker": speaker, "text": m.get("text") or ""})

    conv = Conversation(
        conversation_id=cid,
        dataset_group_id=group_id,
        channel=record.get("channel") or "agent chat",
        language=(record.get("language") or "RU").upper(),
        audio_url=record.get("audio_url"),
        transcript=messages,
    )
    return conv, warnings


def normalize(payload: dict[str, Any]) -> tuple[str, list[Conversation], list[str]]:
    """Ответ API → (group_id, диалоги, предупреждения)."""
    if not payload.get("isSuccess", False):
        raise DatasetError(f"API вернул ошибку: {payload.get('error')!r}")
    data = payload.get("data") or {}
    records = data.get("records") or []
    group_id = data.get("dataset_group_id") or "UNKNOWN"
    warnings: list[str] = []
    if data.get("total_records") not in (None, len(records)):
        warnings.append(f"total_records={data.get('total_records')}, а записей {len(records)}")
    conversations = []
    for record in records:
        conv, conv_warnings = normalize_record(record, group_id)
        conversations.append(conv)
        warnings += conv_warnings
    return group_id, conversations, warnings


def to_file_payload(group_id: str, conversations: list[Conversation]) -> dict[str, Any]:
    return {
        "dataset_group_id": group_id,
        "total_records": len(conversations),
        "records": [c.model_dump() for c in conversations],
    }
