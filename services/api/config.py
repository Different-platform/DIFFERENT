"""Environment-driven configuration (Requirement 13.3, 16.3).

Single source of settings for both the API and the ingestion job. The
ingestion job imports this module so that the embedding model name can
never drift between index time and query time — if those disagree,
retrieval silently returns nonsense rather than failing loudly.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root, so .env resolves regardless of the working directory a
# process was launched from.
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Database ---
    database_url: str = "postgresql://differ:differ@localhost:5432/differ"

    # --- LLM ---
    # Empty means the app falls back to FakeLLMGateway, which returns
    # scripted responses. Everything downstream of the gateway still runs.
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"

    # --- Embeddings ---
    # Must be identical between ingestion and query time (Req 13.3).
    embedding_model: str = "sentence-transformers/all-mpnet-base-v2"
    embedding_dimensions: int = 768

    # --- Ingestion ---
    nhis_data_dir: Path = Path("/Users/pukhraj/Desktop/Chatbot/Dataset")

    # Abort the load for a survey year if fewer than this many variables
    # parse out of its codebook (Req 9.2). Real years yield ~600+.
    ingest_min_variables: int = 100

    # --- Rate limiting ---
    rate_limit_max: int = 50
    rate_limit_window_seconds: int = 3600

    # --- HTTP ---
    frontend_origin: str = "http://localhost:5173"

    # Reserved for the Vercel server-side proxy (Task 16.2, deferred).
    # Unused while the browser talks to the API directly in local dev.
    api_shared_secret: str = ""

    @property
    def has_real_llm(self) -> bool:
        return bool(self.groq_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
