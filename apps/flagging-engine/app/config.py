from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", protected_namespaces=())

    # --- Machine Learning Acceleration ---
    # cpu | mps  -> laptop test        cuda -> 5090 server test
    torch_device: str = "cpu"
    # none | fp16 -> full precision (server)   int8 | 4bit -> compressed (laptop)
    model_quantization: str = "none"
    model_path: str = "./model"
    model_max_length: int = 256
    flag_threshold: float = 0.5

    # --- Dynamic batching / performance tuning ---
    max_batch_size: int = 8
    max_queue_delay_ms: int = 50

    # --- Kafka ---
    kafka_enabled: bool = True
    kafka_bootstrap_servers: str = "kafka:9092"
    kafka_input_topic: str = "claims.raw"
    kafka_output_topic: str = "claims.flagged"
    kafka_consumer_group: str = "flagging-engine"

    log_level: str = "INFO"


settings = Settings()
