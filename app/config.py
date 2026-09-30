"""Application settings.

Tool: pydantic-settings
  What: reads configuration from environment variables / a .env file and validates types.
  Why:  one typed place for every setting (12-factor style). Switching LLM provider,
        database, or limits is a config change, never a code change.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- LLM provider: "groq" (free tier), "openai", or "azure" ---
    llm_provider: str = "groq"
    llm_model: str = "openai/gpt-oss-120b"
    groq_api_key: str | None = None
    openai_api_key: str | None = None
    azure_openai_api_key: str | None = None
    azure_openai_endpoint: str | None = None
    azure_openai_deployment: str | None = None
    azure_openai_api_version: str = "2024-10-21"

    # --- Databases ---
    # Admin connection: bootstraps tables and the knowledge base.
    database_url: str = "postgresql://analyst_admin:admin@localhost:5432/analyst"
    # Read-only connection: the ONLY connection agent-generated SQL ever runs on.
    readonly_database_url: str = "postgresql://analyst_ro:readonly@localhost:5432/analyst"

    # --- Retrieval (RAG) ---
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dim: int = 384
    embedding_cache_dir: str = ".cache/fastembed"

    # --- Safety and limits ---
    max_rows: int = 200
    statement_timeout_ms: int = 5000
    max_sql_attempts: int = 3

    # --- Observability (optional) ---
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
