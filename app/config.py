from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./support.db"
    docs_dir: str = "data/docs"
    seed: int = 42

    kb_min_score: float = 0.35
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    max_failed_lookups: int = 5

    staff_username: str = "staff"
    staff_password: str = "change-me"  # noqa: S105 - demo default, override via env

    anthropic_api_key: str = ""
    anthropic_model: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
