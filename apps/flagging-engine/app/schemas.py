from pydantic import BaseModel, Field, ConfigDict


class ClaimMessage(BaseModel):
    """The claims.raw Kafka input contract, produced by the Claim-Ingest-workers.
    This model is the contract — there is no separate schema file."""

    model_config=ConfigDict(extra='forbid')
    msg_id: str = Field(min_length=1,max_length=256)
    source_platform: str = Field(min_length=1,max_length=256)
    content: str = Field(min_length=1,max_length=200000)
    ingest_timestamp: str = Field(max_length=256)


class PredictRequest(BaseModel):
    post_text: str = Field(..., min_length=1,max_length=200000)


class PredictResponse(BaseModel):
    label: str
    confidence: float
    misinformation_probability: float
    flagged: bool
    model_version: str
