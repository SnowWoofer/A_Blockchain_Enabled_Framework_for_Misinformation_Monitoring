from pydantic import BaseModel, Field


class ClaimMessage(BaseModel):
    """The claims.raw Kafka input contract, produced by the Claim-Ingest-workers.
    This model is the contract — there is no separate schema file."""

    ingest_id: str
    source_platform: str
    content: str
    ingest_timestamp: str


class PredictRequest(BaseModel):
    post_text: str = Field(..., min_length=1)


class PredictResponse(BaseModel):
    label: str
    # The predicted class index as the ledger stores it ("0" | "1").
    label_binary: str
    confidence: float
    misinformation_probability: float
    model_version: str
