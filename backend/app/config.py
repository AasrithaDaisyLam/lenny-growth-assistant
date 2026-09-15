"""
Application configuration.

Every knob that selects a model, tunes retrieval, or points at a dependency lives
here, sourced from environment variables. This is the mechanism behind the brief's
requirement that the evaluator can switch models "without changing application
code" -- no module reads os.environ directly.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["ollama", "anthropic"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application ---------------------------------------------------------
    environment: str = "local"
    log_level: str = "INFO"
    api_port: int = 8000
    cors_origins: str = "http://localhost:8080,http://localhost:5173"
    request_timeout_seconds: int = 180
    internal_tool_token: str = "change-me-local-only"
    internal_tool_base_url: str = "http://localhost:8000"

    # --- Database ------------------------------------------------------------
    postgres_user: str = "lenny"
    postgres_password: str = "lenny_local_password"
    postgres_db: str = "lenny_growth_assistant"
    postgres_host: str = "db"
    postgres_port: int = 5432

    # --- Provider selection (the toggle) -------------------------------------
    llm_provider: Provider = "ollama"
    provider_fallback_chain: str = "ollama"

    # --- Ollama --------------------------------------------------------------
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "llama3.2:3b"
    ollama_embed_model: str = "nomic-embed-text"
    ollama_keep_alive: str = "30m"

    # --- Anthropic (optional cloud provider) ---------------------------------
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"

    # --- Embeddings ----------------------------------------------------------
    embedding_dim: int = 768
    embed_batch_size: int = 32

    # --- Ingestion -----------------------------------------------------------
    transcript_source: Literal["repo", "local"] = "repo"
    transcript_repo: str = "ChatPRD/lennys-podcast-transcripts"
    transcript_local_path: str = ""
    transcript_cache_dir: str = "/data/transcripts"
    chunk_target_tokens: int = 500
    chunk_overlap_ratio: float = 0.15

    # --- Retrieval -----------------------------------------------------------
    rag_top_k: int = 6
    rag_vector_candidates: int = 50
    rag_lexical_candidates: int = 50
    rag_rrf_k: int = 60
    rag_min_score: float = 0.65

    # --- Agent ---------------------------------------------------------------
    # Note: there is no separate "model config path" setting. Pi discovers its
    # catalog at `$PI_CODING_AGENT_DIR/models.json`, which the image sets to
    # /app/agent so the mounted ./agent directory is the single source.
    pi_bin: str = "pi"
    pi_extension_path: str = "/app/agent/growth-extension.ts"
    # The mounted config is read-only, but Pi writes into its config dir
    # (credential store, session dir), so a writable copy is made here at runtime.
    pi_config_source: str = "/app/agent"
    pi_agent_home: str = "/tmp/pi-agent"
    # Skills are Markdown principles, mounted read-only and read per request, so
    # editing one changes output without a code change or restart.
    skills_dir: str = "/app/skills"
    pi_max_sessions: int = 4
    pi_idle_timeout_seconds: int = 900
    pi_max_turns: int = 8

    @field_validator("chunk_overlap_ratio")
    @classmethod
    def _validate_overlap(cls, v: float) -> float:
        if not 0.0 <= v < 0.5:
            raise ValueError("chunk_overlap_ratio must be in [0, 0.5)")
        return v

    @field_validator("rag_min_score")
    @classmethod
    def _validate_floor(cls, v: float) -> float:
        if not -1.0 <= v <= 1.0:
            raise ValueError("rag_min_score must be a cosine similarity in [-1, 1]")
        return v

    # --- Derived -------------------------------------------------------------
    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def fallback_chain(self) -> list[Provider]:
        chain = [p.strip() for p in self.provider_fallback_chain.split(",") if p.strip()]
        valid: list[Provider] = [p for p in chain if p in ("ollama", "anthropic")]
        return valid or [self.llm_provider]

    @property
    def anthropic_configured(self) -> bool:
        return bool(self.anthropic_api_key.strip())

    def provider_unavailable_reason(self, provider: str) -> str | None:
        """Why a provider cannot be used -- surfaced in the UI rather than hidden."""
        if provider == "anthropic" and not self.anthropic_configured:
            return "ANTHROPIC_API_KEY is not set"
        if provider not in ("ollama", "anthropic"):
            return f"unknown provider '{provider}'"
        return None

    def model_for(self, provider: str) -> str:
        return self.anthropic_model if provider == "anthropic" else self.ollama_model


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
