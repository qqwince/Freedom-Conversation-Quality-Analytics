"""Провайдеры языковых моделей за единым интерфейсом.

Провайдер выбирается по имени модели: deepseek-* — DeepSeek, остальное (gemini-*, gemma-*) — Gemini API.
"""

from cqa.llm.base import ChatTurn, LLMError, LLMProvider, LLMResult

__all__ = ["ChatTurn", "LLMError", "LLMProvider", "LLMResult", "create_provider", "provider_family"]


def provider_family(model: str) -> str:
    return "deepseek" if model.startswith("deepseek") else "gemini"


def create_provider(model: str | None = None) -> LLMProvider:
    from cqa.config import get_settings

    settings = get_settings()
    model = model or settings.llm_model
    family = provider_family(model)
    # Запасные модели — только того же провайдера: у них общий ключ и формат запроса.
    fallbacks = [m for m in settings.llm_fallback_models if provider_family(m) == family]
    common = {
        "model": model,
        "temperature": settings.llm_temperature,
        "timeout_sec": settings.llm_timeout_sec,
        "max_retries": settings.llm_max_retries,
        "fallback_models": fallbacks,
    }

    if family == "deepseek":
        from cqa.llm.openai_compat import OpenAICompatibleProvider

        if not settings.deepseek_api_key:
            raise LLMError("DEEPSEEK_API_KEY не задан (файл .env в корне проекта)")
        return OpenAICompatibleProvider(name="deepseek", base_url=settings.deepseek_base_url,
                                        api_key=settings.deepseek_api_key, **common)

    from cqa.llm.gemini import GeminiProvider

    if not settings.gemini_api_key:
        raise LLMError("GEMINI_API_KEY не задан (файл .env в корне проекта)")
    return GeminiProvider(api_key=settings.gemini_api_key, **common)
