# app/config/settings.py

from pydantic import BaseSettings, Field


class Settings(BaseSettings):
    # -------------------------
    # APP
    # -------------------------
    ENV: str = "development"
    DEBUG: bool = True

    # -------------------------
    # LLM CONFIG
    # -------------------------
    LLM_API_KEY: str = Field(..., env="LLM_API_KEY")
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_TEMPERATURE: float = 0.3
    LLM_MAX_TOKENS: int = 500
    LLM_TIMEOUT: int = 15
    LLM_MAX_RETRIES: int = 2

    # -------------------------
    # SCRAPER CONFIG
    # -------------------------
    SCRAPER_TIMEOUT: int = 10
    SCRAPER_RETRIES: int = 2
    SCRAPER_CONCURRENCY: int = 5

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()