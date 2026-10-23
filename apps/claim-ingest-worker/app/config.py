from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    kafka_bootstrap_servers: str = "kafka:9092"
    kafka_output_topic: str = "claims.raw"
    source_platform: str = "test"

    # Signs every claims.raw message (see app/signing.py). Required unless
    # pipeline_allow_unsigned is set for a throwaway dev run.
    pipeline_raw_key: str = ""
    pipeline_allow_unsigned: bool = False

    # Bulk POST /ingest is for internal tools and needs this key as X-API-Key.
    ingest_api_key: str = ""
    max_claims_per_request: int = 100
    max_claim_chars: int = 10_000

    # Public tip line (POST /public/tip), reached through the web edge proxy.
    tip_min_chars: int = 10
    tip_max_chars: int = 2_000
    tip_rate_limit: int = 5
    tip_rate_window_s: int = 600
    trust_proxy_headers: bool = False

    log_level: str = "INFO"


settings = Settings()
