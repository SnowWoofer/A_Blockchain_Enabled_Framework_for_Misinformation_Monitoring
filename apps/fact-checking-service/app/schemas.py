from pydantic import BaseModel, Field

VALID_VERDICTS = ("factual", "opinion", "misinformation")


class ReportSubmit(BaseModel):
    verdict: str  # factual / opinion / misinformation
    reasoning: str = Field(..., min_length=1)
    evidence: list[str] = Field(default_factory=list)
