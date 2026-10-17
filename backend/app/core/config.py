from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_secret: str = "development-only-change-me"
    database_path: Path = Path("./data/audit-data-transformer.sqlite3")
    storage_path: Path = Path("./data/storage")
    web_origin: str = "http://localhost:5174"
    session_ttl_hours: int = 12
    max_upload_mb: int = 25

    demo_user_email: str = "user@transformer.local"
    demo_user_password: str = "ChangeMe123!"
    demo_admin_email: str = "admin@transformer.local"
    demo_admin_password: str = "ChangeAdmin123!"

    llm_provider: str = "ollama"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "max-gpu-qwen:latest"
    llm_timeout_seconds: int = 180

    @field_validator("database_path", "storage_path", mode="before")
    @classmethod
    def resolve_path(cls, value: str | Path) -> Path:
        return Path(value).expanduser().resolve()

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

