from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ipfs_api_url: str = "http://localhost:5001/api/v0"
    log_level: str = "INFO"


settings = Settings()
