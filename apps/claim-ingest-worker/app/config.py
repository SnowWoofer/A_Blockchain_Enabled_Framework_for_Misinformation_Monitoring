from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    kafka_bootstrap_servers: str = "kafka:9092"
    kafka_output_topic: str = "claims.raw"
    # Where these claims came from. Also namespaces ingest_id, so every id
    # carries its own provenance ("test_data_9f3c...", "twitter_1a2b...")
    # instead of a hardcoded prefix that outlives the source it named.
    source_platform: str = "test_data"

    log_level: str = "INFO"


settings = Settings()
