"""
app/core/llm_factory.py
─────────────────────────────────────────────────────────────────────────────
Dynamic LLM provider factory with automatic priority-based fallback.

Priority is read from the ``LLM_PRIORITY_ORDER`` environment variable (or
``settings.llm_priority_order``).  Providers are tried in order; on a quota /
rate-limit error the factory silently moves to the next one.

Supported provider keys
-----------------------
    "gemini"      → Google Gemini (langchain-google-genai)
    "grok"        → xAI Grok     (langchain-openai + custom base URL)
    "openai"      → OpenAI GPT   (langchain-openai)
    "anthropic"   → Claude        (langchain-anthropic)
    "ollama"      → Ollama local  (langchain-ollama)

Environment variables (per provider)
-------------------------------------
    GEMINI_API_KEY, GEMINI_MODEL          (default: gemini-2.0-flash)
    GROK_API_KEY, GROK_MODEL             (default: grok-3-mini)
    OPENAI_API_KEY, OPENAI_MODEL         (default: gpt-4o-mini)
    ANTHROPIC_API_KEY, ANTHROPIC_MODEL   (default: claude-3-haiku-20240307)
    OLLAMA_BASE_URL, OLLAMA_MODEL        (default: qwen2.5:3b)

    LLM_PRIORITY_ORDER   Comma-separated list, e.g.:
                         "gemini,grok,openai,anthropic,ollama"
    LLM_TEMPERATURE      Float 0.0-1.0 (default: 0.1)
    LLM_MAX_TOKENS       Integer        (default: 4096)
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from langchain_core.language_models.chat_models import BaseChatModel

from app.core.settings import get_settings

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Exceptions that signal we should try the next provider
_QUOTA_ERRORS: tuple[type[Exception], ...] = ()

try:
    from openai import RateLimitError as OpenAIRateLimitError

    _QUOTA_ERRORS += (OpenAIRateLimitError,)
except ImportError:
    pass

try:
    from anthropic import RateLimitError as AnthropicRateLimitError

    _QUOTA_ERRORS += (AnthropicRateLimitError,)
except ImportError:
    pass

try:
    from google.api_core.exceptions import ResourceExhausted

    _QUOTA_ERRORS += (ResourceExhausted,)
except ImportError:
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Provider builder functions
# ─────────────────────────────────────────────────────────────────────────────


def _build_gemini(settings: Any, temperature: float, max_tokens: int) -> BaseChatModel:
    from langchain_google_genai import ChatGoogleGenerativeAI  # type: ignore[import]

    model = settings.gemini_model or "gemini-2.0-flash"
    logger.info("Initializing Gemini provider: model=%s", model)
    return ChatGoogleGenerativeAI(
        model=model,
        google_api_key=settings.gemini_api_key,
        temperature=temperature,
        max_output_tokens=max_tokens,
    )


def _build_grok(settings: Any, temperature: float, max_tokens: int) -> BaseChatModel:
    from langchain_openai import ChatOpenAI  # type: ignore[import]

    model = settings.grok_model or "grok-3-mini"
    base_url = settings.grok_base_url or "https://api.x.ai/v1"
    logger.info("Initializing Grok provider: model=%s, base_url=%s", model, base_url)
    return ChatOpenAI(
        model=model,
        api_key=settings.grok_api_key,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def _build_openai(settings: Any, temperature: float, max_tokens: int) -> BaseChatModel:
    from langchain_openai import ChatOpenAI  # type: ignore[import]

    model = settings.openai_model or "gpt-4o-mini"
    logger.info("Initializing OpenAI provider: model=%s", model)
    return ChatOpenAI(
        model=model,
        api_key=settings.openai_api_key,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def _build_anthropic(
    settings: Any, temperature: float, max_tokens: int
) -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic  # type: ignore[import]

    model = settings.anthropic_model or "claude-3-haiku-20240307"
    logger.info("Initializing Anthropic provider: model=%s", model)
    return ChatAnthropic(
        model=model,
        api_key=settings.anthropic_api_key,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def _build_ollama(settings: Any, temperature: float, max_tokens: int) -> BaseChatModel:
    from langchain_ollama import ChatOllama  # type: ignore[import]

    model = settings.ollama_model or "qwen2.5:3b"
    base_url = settings.ollama_base_url or "http://localhost:11434"
    logger.info(
        "Initializing Ollama local provider: model=%s, base_url=%s", model, base_url
    )
    return ChatOllama(
        model=model,
        base_url=base_url,
        temperature=temperature,
        num_predict=max_tokens,
    )


_PROVIDER_BUILDERS: dict[
    str, Any  # Callable[[Any, float, int], BaseChatModel]
] = {
    "gemini": _build_gemini,
    "grok": _build_grok,
    "openai": _build_openai,
    "anthropic": _build_anthropic,
    "ollama": _build_ollama,
}


# ─────────────────────────────────────────────────────────────────────────────
# LLMFactory class
# ─────────────────────────────────────────────────────────────────────────────


class LLMFactory:
    """
    Stateful factory that manages the active LLM and handles automatic fallback.

    Usage
    -----
    >>> factory = LLMFactory()
    >>> llm = factory.get_llm()          # → active provider
    >>> llm = factory.next_provider()    # → rotate after a quota error
    >>> factory.reset()                  # → back to the first provider
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._priority_order: list[str] = self._settings.llm_priority_order
        self._current_index: int = 0
        self._failed: list[str] = []
        self._temperature: float = self._settings.llm_temperature
        self._max_tokens: int = self._settings.llm_max_tokens
        self._active_llm: BaseChatModel | None = None
        self._active_provider: str = ""

    # ── Public API ────────────────────────────────────────────────────────────

    def get_llm(self) -> BaseChatModel:
        """Return the currently active LLM, initialising it if necessary."""
        if self._active_llm is None:
            self._active_llm = self._build_current()
        return self._active_llm

    @property
    def active_provider(self) -> str:
        return self._active_provider

    @property
    def failed_providers(self) -> list[str]:
        return list(self._failed)

    def next_provider(self) -> BaseChatModel:
        """
        Mark the current provider as failed and rotate to the next available one.

        Raises
        ------
        RuntimeError
            When all configured providers have been exhausted.
        """
        if self._active_provider:
            logger.warning(
                "Provider '%s' exhausted – rotating to next.", self._active_provider
            )
            self._failed.append(self._active_provider)

        self._current_index += 1
        self._active_llm = None

        # Skip already-failed providers (shouldn't happen with a linear list,
        # but guard against repeated calls)
        while self._current_index < len(self._priority_order):
            candidate = self._priority_order[self._current_index]
            if candidate not in self._failed:
                self._active_llm = self._build_current()
                return self._active_llm
            self._current_index += 1

        raise RuntimeError(
            "All configured LLM providers have been exhausted. "
            f"Priority order: {self._priority_order}. "
            f"Failed: {self._failed}."
        )

    def reset(self) -> None:
        """Reset the factory back to the first provider (e.g. for a new session)."""
        self._current_index = 0
        self._failed = []
        self._active_llm = None
        self._active_provider = ""

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_current(self) -> BaseChatModel:
        provider_key = self._priority_order[self._current_index]
        builder = _PROVIDER_BUILDERS.get(provider_key)
        if builder is None:
            raise ValueError(
                f"Unknown LLM provider '{provider_key}'. "
                f"Valid providers: {list(_PROVIDER_BUILDERS)}"
            )
        llm = builder(self._settings, self._temperature, self._max_tokens)
        self._active_provider = provider_key
        logger.info("Active LLM provider set to '%s'.", provider_key)
        return llm


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton + convenience helpers
# ─────────────────────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _get_factory_singleton() -> LLMFactory:
    """Module-level singleton LLMFactory (cached for import-time reuse)."""
    return LLMFactory()


def get_llm() -> BaseChatModel:
    """Shortcut – returns the active LLM from the singleton factory."""
    return _get_factory_singleton().get_llm()


def rotate_llm_on_quota_error(exc: Exception) -> BaseChatModel:
    """
    Check whether *exc* is a quota/rate-limit error; if so rotate providers.

    Args:
        exc: The exception caught inside a graph node.

    Returns:
        A new (rotated) LLM instance.

    Raises:
        RuntimeError: If all providers are exhausted.
        exc: Re-raises the original exception if it is not a quota error.
    """
    if _QUOTA_ERRORS and isinstance(exc, _QUOTA_ERRORS):
        logger.warning(
            "Quota/rate-limit error detected (%s). Rotating LLM provider.", type(exc).__name__
        )
        return _get_factory_singleton().next_provider()

    # Not a recoverable quota error – propagate
    raise exc


def get_factory() -> LLMFactory:
    """Return the module-level factory singleton (for state inspection)."""
    return _get_factory_singleton()
