# app/config/settings.py

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


print("In Settings class definition...")
class Settings(BaseSettings):
    # -------------------------
    # APP
    # -------------------------
    ENV: str = "development"
    DEBUG: bool = True

    # -------------------------
    # LLM CONFIG
    # -------------------------
    LLM_API_KEY: str = Field(...)
    TAVILY_API_KEY: str = Field(...)
    LLM_MODEL: str = "llama-3.1-8b-instant"
    LLM_TEMPERATURE: float = 0.3
    LLM_MAX_TOKENS: int = 500
    LLM_TIMEOUT: int = 60
    LLM_MAX_RETRIES: int = 2
    AGENT_TIMEOUT: int = 300
    OSINT_MAX_RETRIES: int = 3
    OSINT_TIMEOUT: int = 20

    # -------------------------
    # APIFY & DISCOVERY
    # -------------------------
    APIFY_TOKEN: str = Field(..., env="APIFY_TOKEN")
    APIFY_MAX_RESULTS: int = 100

    # YOUR "SWEET SPOT" FILTERS
    REVIEW_MIN: int = 10
    REVIEW_MAX: int = 1000

    # -------------------------
    # SCRAPER CONFIG
    # -------------------------
    SCRAPER_TIMEOUT: int = 20
    SCRAPER_RETRIES: int = 2
    SCRAPER_CONCURRENCY: int = 2
    BROWSERLESS_WS_URL: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env", case_sensitive=True, extra="ignore"
    )


print("Instantiating settings...")
settings = Settings()
print("Settings instantiated.")
