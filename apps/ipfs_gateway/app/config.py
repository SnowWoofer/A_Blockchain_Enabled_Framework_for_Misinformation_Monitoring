from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ipfs_api_url: str = "http://localhost:5001/api/v0"
    # Shared secret the blockchain gateway presents as X-Internal-Token on /add. Without it anyone who can reach this port could pin arbitrary content into the consortium's private swarm.
    internal_api_token: str = ""
    max_add_bytes: int = 1024 * 1024
    log_level: str = "INFO"


settings = Settings()
