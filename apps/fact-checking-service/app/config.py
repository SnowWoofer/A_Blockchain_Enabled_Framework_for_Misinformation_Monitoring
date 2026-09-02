from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Blockchain gateway (apps/blockchain_gateway) ---
    blockchain_api_base: str = "http://localhost:8000"
    # One org-level API key per org (from blockchain/scripts/bootstrap-keys.sh)
    # — the same keys blockchain_gateway itself accepts. A fact-checking org
    # authenticates to this service by presenting its own key directly (as
    # X-API-Key); there is no separate account/login layer here. Which
    # individual person at that org is using it is that org's own concern,
    # handled by their internal systems, not this one.
    org_api_keys: dict[str, str] = {
        "org1": "key-org1",
        "org2": "key-org2",
        "org3": "key-org3",
        # Client-only member: no peer, no ledger copy — identity only.
        "org4": "key-org4",
    }

    log_level: str = "INFO"


settings = Settings()
