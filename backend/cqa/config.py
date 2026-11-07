"""Настройки приложения из переменных окружения и файла .env в корне проекта."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(path: Path = PROJECT_ROOT / ".env") -> None:
    """Минимальный загрузчик .env: KEY=VALUE, без перезаписи уже заданных переменных."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _float_env(name: str) -> float | None:
    value = os.environ.get(name)
    return float(value) if value else None


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str | None
    deepseek_api_key: str | None
    deepseek_base_url: str
    llm_model: str
    llm_fallback_models: tuple[str, ...]   # при перегрузке основной модели, по порядку
    llm_temperature: float
    llm_timeout_sec: float
    llm_max_retries: int
    votes: int
    workers: int
    # Цена за 1 млн токенов, USD. Не задана — стоимость не считается (бесплатный тариф).
    price_input_per_m: float | None
    price_output_per_m: float | None
    data_dir: Path
    checklist_path: Path


@lru_cache
def get_settings() -> Settings:
    load_dotenv()
    return Settings(
        gemini_api_key=os.environ.get("GEMINI_API_KEY"),
        deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY"),
        deepseek_base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        llm_model=os.environ.get("CQA_LLM_MODEL", "gemini-3.5-flash"),
        llm_fallback_models=tuple(
            m.strip() for m in os.environ.get("CQA_LLM_FALLBACK_MODELS", "gemini-3.6-flash").split(",") if m.strip()
        ),
        llm_temperature=float(os.environ.get("CQA_LLM_TEMPERATURE", "0")),
        llm_timeout_sec=float(os.environ.get("CQA_LLM_TIMEOUT_SEC", "180")),
        llm_max_retries=int(os.environ.get("CQA_LLM_MAX_RETRIES", "4")),
        votes=int(os.environ.get("CQA_VOTES", "1")),
        workers=int(os.environ.get("CQA_WORKERS", "4")),
        price_input_per_m=_float_env("CQA_PRICE_INPUT_PER_M"),
        price_output_per_m=_float_env("CQA_PRICE_OUTPUT_PER_M"),
        data_dir=Path(os.environ.get("CQA_DATA_DIR") or PROJECT_ROOT / "data"),   # отдельная база для демо и тестов
        checklist_path=PROJECT_ROOT / "config" / "checklist.default.json",
    )
