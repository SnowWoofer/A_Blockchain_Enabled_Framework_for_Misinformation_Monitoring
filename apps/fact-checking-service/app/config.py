from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Blockchain gateway (apps/blockchain_gateway) ---
    blockchain_api_base: str = "http://localhost:8000"
    # Explicit provisioned caller credentials; no predictable bootstrap keys.
    # — the same keys blockchain_gateway itself accepts. A fact-checking org
    # authenticates to this service by presenting its own key directly (as
    # X-API-Key); there is no separate account/login layer here. Which
    # individual person at that org is using it is that org's own concern,
    # handled by their internal systems, not this one.
    org_api_keys: dict[str, str] = {}
    enrichment_concurrency: int = 8

    log_level: str = "INFO"


settings = Settings()
