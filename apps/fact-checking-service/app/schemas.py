from pydantic import BaseModel, Field

# Binary label domain, same as the model's inference_label.
VALID_OUTCOMES = ("0", "1")


class FactCheckSubmit(BaseModel):
    outcome: str  # "0" = non-misinformation, "1" = misinformation
    reasoning: str = Field(..., min_length=1)
    support: list[str] = Field(default_factory=list)
