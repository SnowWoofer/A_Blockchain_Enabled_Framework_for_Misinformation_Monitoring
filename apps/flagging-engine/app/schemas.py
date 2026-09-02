from pydantic import BaseModel, Field


class ClaimMessage(BaseModel):
    """Matches flagging-engine/source-format.json — the Kafka input contract
    produced by the Claim-Ingest-workers."""

    msg_id: str
    source_platform: str
    post_text: str
    ingest_timestamp: str
    masked_user_hash: str


class PredictRequest(BaseModel):
    post_text: str = Field(..., min_length=1)


class PredictResponse(BaseModel):
    label: str
    confidence: float
    misinformation_probability: float
    flagged: bool
    model_version: str
