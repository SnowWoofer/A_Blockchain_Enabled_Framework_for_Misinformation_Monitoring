from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    kafka_bootstrap_servers: str = "kafka:9092"
    kafka_output_topic: str = "claims.raw"
    source_platform: str = "test"

    log_level: str = "INFO"


settings = Settings()
