"""Провайдеры языковых моделей за единым интерфейсом."""

from cqa.llm.base import ChatTurn, LLMError, LLMProvider, LLMResult

__all__ = ["ChatTurn", "LLMError", "LLMProvider", "LLMResult", "create_provider"]


def create_provider(model: str | None = None) -> LLMProvider:
    """Провайдер по настройкам. Сейчас поддерживается Gemini; новые провайдеры добавляются здесь."""
    from cqa.config import get_settings
    from cqa.llm.gemini import GeminiProvider

    settings = get_settings()
    if not settings.gemini_api_key:
        raise LLMError("GEMINI_API_KEY не задан (файл .env в корне проекта)")
    return GeminiProvider(
        api_key=settings.gemini_api_key,
        model=model or settings.llm_model,
        temperature=settings.llm_temperature,
        timeout_sec=settings.llm_timeout_sec,
        max_retries=settings.llm_max_retries,
        fallback_models=settings.llm_fallback_models,
    )
