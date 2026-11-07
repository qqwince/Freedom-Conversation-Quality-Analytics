"""Автообновление: сервер сам периодически запрашивает API организаторов по выбранным group_id,
загружает новые диалоги и (по настройке) сразу ставит их на оценку.

API организаторов не сообщает об изменениях, поэтому новые диалоги находятся сравнением
conversation_id ответа с уже загруженными. Настройки и журнал проверок хранятся в базе.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_CONFIG: dict[str, Any] = {"enabled": False, "interval_min": 5, "auto_evaluate": True, "groups": []}
TICK_SEC = 15


class SyncWatcher:
    def __init__(self, service: Any, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.service = service
        self._now = now
        self._lock = threading.Lock()
        self._running = False
        self._last_check: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- Настройки ------------------------------------------------------------------------

    def config(self) -> dict[str, Any]:
        return {**DEFAULT_CONFIG, **(self.service.storage.get_setting("sync") or {})}

    def update(self, enabled: bool, interval_min: int, groups: list[str], auto_evaluate: bool) -> dict[str, Any]:
        groups = sorted({g.strip() for g in groups if g.strip()})
        if enabled and not groups:
            raise ValueError("укажите хотя бы один group_id")
        config = {"enabled": enabled, "interval_min": max(1, min(int(interval_min), 120)),
                  "auto_evaluate": auto_evaluate, "groups": groups}
        self.service.storage.set_setting("sync", config)
        return self.status()

    # --- Проверка -------------------------------------------------------------------------

    def run_once(self) -> list[dict[str, Any]]:
        """Проверить все group_id сейчас. Повторный запуск во время проверки ничего не делает."""
        with self._lock:
            if self._running:
                return []
            self._running = True
        events = []
        try:
            config = self.config()
            for group in config["groups"]:
                events.append(self._check_group(group, config["auto_evaluate"]))
        finally:
            self._last_check = self._now().isoformat(timespec="seconds")
            self._running = False
        return events

    def _check_group(self, group: str, auto_evaluate: bool) -> dict[str, Any]:
        event: dict[str, Any] = {"group_id": group, "new_ids": [], "job_id": None, "error": None}
        try:
            result = self.service.import_group(group)
            event["new_ids"] = result["new_ids"]
            if result["new_ids"] and auto_evaluate:
                checklist = self.service.storage.list_checklists()[0]
                job = self.service.start_batch(result["new_ids"], checklist["id"])
                event["job_id"] = job["id"]
        except Exception as exc:  # сеть, формат ответа — проверка не должна останавливать сервер
            log.warning("автообновление %s: %s", group, exc)
            event["error"] = str(exc)
        if event["new_ids"] or event["error"]:
            self.service.storage.add_sync_event(event)
        return event

    def due(self) -> bool:
        config = self.config()
        if not config["enabled"] or not config["groups"] or self._running:
            return False
        if self._last_check is None:
            return True
        last = datetime.fromisoformat(self._last_check)
        return self._now() >= last + timedelta(minutes=config["interval_min"])

    # --- Фоновый поток --------------------------------------------------------------------

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="cqa-sync", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(TICK_SEC):
            if self.due():
                self.run_once()

    def status(self, since: str | None = None) -> dict[str, Any]:
        config = self.config()
        next_check = None
        if config["enabled"] and self._last_check:
            next_check = (datetime.fromisoformat(self._last_check)
                          + timedelta(minutes=config["interval_min"])).isoformat(timespec="seconds")
        events = self.service.storage.list_sync_events(limit=20)
        new_since = [cid for e in events if since is None or e["at"] > since for cid in e["new_ids"]]
        return {**config, "running": self._running, "last_check": self._last_check, "next_check": next_check,
                "events": events, "new_since": new_since}
