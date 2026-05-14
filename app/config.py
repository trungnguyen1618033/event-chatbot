"""
Central configuration loaded from .env.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
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

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

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
