from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Blockchain gateway (apps/blockchain_gateway) ---
    # This service holds no credentials of its own. 
    blockchain_api_base: str = "http://blockchain-gateway:8000"
    # Concurrent off-chain document fetches when building a queue page.
    enrich_concurrency: int = 8

    log_level: str = "INFO"


settings = Settings()
