"""
Central configuration loaded from .env.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    #  ── LLM provider switch ────────────────────────────────────
    llm_provider: str = "openai"  # "azure" | "bedrock" | "openai" | "gemini"

    #  ── OpenAI (api.openai.com) ────────────────────────────────
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    #  ── Bedrock (OpenAI-compatible proxy) ──────────────────────
    bedrock_api_key: str = ""
    bedrock_base_url: str = ""
    bedrock_model: str = ""

    #  ── Azure OpenAI ───────────────────────────────────────────
    #  AZURE_OPENAI_ENDPOINT is a FULL URL including deployment + api-version.
    #  Example:
    #    https://<resource>.openai.azure.com/openai/deployments/<dep>/chat/completions?api-version=2025-01-01-preview
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_model: str = ""

    #  ── Google Gemini (OpenAI-compatible endpoint) ─────────────
    gemini_api_key: str = ""
    gemini_model: str = "gemini-1.5-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"

    #  PostgreSQL
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "event_chatbot"
    db_user: str = "postgres"
    db_password: str = "postgres"

    #  ChromaDB
    chroma_persist_dir: str = "./chroma_data"
    chroma_collection: str = "chat_history"

    #  App
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    debug: bool = False

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @property
    def db_url(self) -> str:
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def async_db_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


@lru_cache()
def get_settings() -> Settings:
    return Settings()
