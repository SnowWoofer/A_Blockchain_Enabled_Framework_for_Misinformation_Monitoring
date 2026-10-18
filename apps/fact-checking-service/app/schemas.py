from pydantic import BaseModel, Field

# Binary label domain, same as the model's inference_label.
VALID_OUTCOMES = ("0", "1")


class FactCheckSubmit(BaseModel):
    outcome: str = Field(pattern='^[01]$')
    reasoning: str = Field(..., min_length=1,max_length=100000)
    support: list[str] = Field(default_factory=list,max_length=100)
