from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", protected_namespaces=())

    # --- Machine Learning Acceleration ---
    # cpu | mps  -> laptop test        cuda -> 5090 server test
    torch_device: Literal['cpu','mps','cuda'] = "cpu"
    # none | fp16 -> full precision (server)   int8 | 4bit -> compressed (laptop)
    model_quantization: Literal['none','fp16','int8','4bit'] = "none"
    model_path: str = "./model"
    model_max_length: int = Field(256,ge=1,le=512)
    flag_threshold: float = Field(.5,ge=0,le=1)

    # --- Dynamic batching / performance tuning ---
    max_batch_size: int = Field(8,ge=1,le=128)
    max_queue_delay_ms: int = Field(50,ge=1,le=10000)

    # --- Kafka ---
    kafka_enabled: bool = True
    kafka_bootstrap_servers: str = "kafka:9092"
    kafka_input_topic: str = "claims.raw"
    kafka_output_topic: str = "claims.flagged"
    kafka_consumer_group: str = "flagging-engine"

    log_level: str = "INFO"
    dead_letter_path: str = "./data/failed_inference.jsonl"


settings = Settings()
