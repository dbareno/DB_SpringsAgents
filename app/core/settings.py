"""
app/core/settings.py
─────────────────────────────────────────────────────────────────────────────
Application settings loaded from environment variables / .env file.
Uses pydantic-settings for clean, typed configuration.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    All runtime configuration for the Spring Design Agent system.

    Values are read (in priority order) from:
      1. Real environment variables
      2. A ``.env`` file in the project root
      3. The default values defined here
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ────────────────────────────────────────────────────────
    app_name: str = "Spring Design Agent API"
    app_version: str = "0.1.0"
    debug: bool = False
    log_level: str = "INFO"

    # ── LLM Orchestration ──────────────────────────────────────────────────
    llm_priority_order_raw: str = Field(
        "ollama,gemini,grok,openai,anthropic",
        alias="LLM_PRIORITY_ORDER",
        description="Comma-separated ordered list of LLM provider keys",
    )
    llm_temperature: float = Field(0.1, alias="LLM_TEMPERATURE")
    llm_max_tokens: int = Field(4096, alias="LLM_MAX_TOKENS")

    @field_validator("llm_priority_order_raw", mode="before")
    @classmethod
    def strip_brackets(cls, v: str) -> str:
        """Accept both 'a,b,c' and '["a","b","c"]' formats."""
        return v.strip().strip("[]").replace('"', "").replace("'", "")

    @property
    def llm_priority_order(self) -> list[str]:
        return [p.strip() for p in self.llm_priority_order_raw.split(",") if p.strip()]

    # ── Gemini ─────────────────────────────────────────────────────────────
    gemini_api_key: str | None = Field(None, alias="GEMINI_API_KEY")
    gemini_model: str = Field("gemini-2.0-flash", alias="GEMINI_MODEL")

    # ── Grok / xAI ────────────────────────────────────────────────────────
    grok_api_key: str | None = Field(None, alias="GROK_API_KEY")
    grok_model: str = Field("grok-3-mini", alias="GROK_MODEL")
    grok_base_url: str = Field("https://api.x.ai/v1", alias="GROK_BASE_URL")

    # ── OpenAI ────────────────────────────────────────────────────────────
    openai_api_key: str | None = Field(None, alias="OPENAI_API_KEY")
    openai_model: str = Field("gpt-4o-mini", alias="OPENAI_MODEL")

    # ── Anthropic ─────────────────────────────────────────────────────────
    anthropic_api_key: str | None = Field(None, alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field("claude-3-haiku-20240307", alias="ANTHROPIC_MODEL")

    # ── Ollama (local fallback) ────────────────────────────────────────────
    ollama_base_url: str = Field("http://localhost:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field("qwen2.5:7b", alias="OLLAMA_MODEL")

    # ── PostgreSQL ─────────────────────────────────────────────────────────
    postgres_url: str = Field(
        "postgresql+asyncpg://postgres:password@localhost:5432/springs_db",
        alias="POSTGRES_URL",
    )

    # ── ChromaDB ──────────────────────────────────────────────────────────
    chroma_host: str = Field("localhost", alias="CHROMA_HOST")
    chroma_port: int = Field(8000, alias="CHROMA_PORT")
    chroma_collection_standards: str = Field(
        "spring_standards", alias="CHROMA_COLLECTION_STANDARDS"
    )

    # ── Redis (optional alternative to ChromaDB) ───────────────────────────
    redis_url: str = Field("redis://localhost:6379/0", alias="REDIS_URL")
    use_redis: bool = Field(False, alias="USE_REDIS")

    # ── Workflow limits ────────────────────────────────────────────────────
    max_design_iterations: int = Field(
        5,
        alias="MAX_DESIGN_ITERATIONS",
        description="Hard cap on redesign loop cycles",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached singleton Settings instance."""
    return Settings()
