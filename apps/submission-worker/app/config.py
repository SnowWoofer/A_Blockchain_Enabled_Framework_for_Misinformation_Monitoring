from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Kafka ---
    kafka_bootstrap_servers: str = "kafka:9092"
    kafka_input_topic: str = "claims.flagged"
    kafka_consumer_group: str = "submission-worker"

    # --- Blockchain gateway (apps/blockchain_gateway) ---
    blockchain_api_base: str = "http://localhost:8000"
    blockchain_api_key: str = ""

    # --- Dead-letter output for claims that fail to reach the blockchain ---
    output_path: str = "./data/failed_claims.jsonl"

    # --- Integrity of claims.flagged (see app/signing.py) ---
    pipeline_flagged_key: str = ""
    pipeline_allow_unsigned: bool = False
    # msg_ids already anchored, so a replayed or redelivered message does not become a second on-chain report (each submission gets a fresh CID).
    processed_db: str = "./data/processed.db"

    log_level: str = "INFO"


settings = Settings()
