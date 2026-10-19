import ssl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Kafka ---
    kafka_ssl_cafile: str = ""
    kafka_ssl_certfile: str = ""
    kafka_ssl_keyfile: str = ""
    kafka_bootstrap_servers: str = "kafka:9092"
    kafka_input_topic: str = "claims.flagged"
    kafka_consumer_group: str = "submission-worker"

    # --- Blockchain gateway (apps/blockchain_gateway) ---
    blockchain_api_base: str = "https://localhost:18000"
    blockchain_api_key: str = ""
    # Optional private, run-scoped identity routing for the synthetic benchmark.
    benchmark_routes_file: str = ""

    # --- Dead-letter output for claims that fail to reach the blockchain ---
    output_path: str = "./data/failed_claims.jsonl"

    log_level: str = "INFO"


settings = Settings()


def kafka_tls_options():
    if not all((settings.kafka_ssl_cafile, settings.kafka_ssl_certfile, settings.kafka_ssl_keyfile)):
        raise ValueError("Kafka TLS CA, client certificate and key are required")
    context = ssl.create_default_context(cafile=settings.kafka_ssl_cafile)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(settings.kafka_ssl_certfile, settings.kafka_ssl_keyfile)
    return {"security_protocol": "SSL", "ssl_context": context}
