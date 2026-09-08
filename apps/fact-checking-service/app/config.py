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
    # Consortium membership — the same source of truth blockchain_gateway
    # uses for its ORGS. The key map below is derived from this rather than
    # listed separately: the two used to be maintained by hand and drifted, so
    # a five-org consortium silently locked org5 out of this service (401,
    # "unknown API key") while the gateway accepted it perfectly well.
    orgs: str = "org1,org2,org3"
    # Only needed if the keys are not named key-<org>; otherwise derived.
    org_api_keys: dict[str, str] = {}

    log_level: str = "INFO"


settings = Settings()

if not settings.org_api_keys:
    settings.org_api_keys = {
        o.strip(): f"key-{o.strip()}" for o in settings.orgs.split(",") if o.strip()
    }
