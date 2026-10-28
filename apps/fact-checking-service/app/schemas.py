from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

# Binary label domain, same as the model's inference_label.
VALID_OUTCOMES = ("0", "1")


def _safe_url(url: str) -> bool:
    # Evidence links end up as <a href> in the web client: absolute http(s) only, no embedded credentials.
    if len(url) > 2048 or any(c.isspace() for c in url):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname) and not parts.username


class FactCheckSubmit(BaseModel):
    outcome: Literal["0", "1"]  # "0" = non-misinformation, "1" = misinformation
    # A verdict others will rely on needs a real justification.
    reasoning: str = Field(..., min_length=20, max_length=5000)
    support: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("reasoning")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 20:
            raise ValueError("reasoning must be at least 20 characters")
        return v

    @field_validator("support")
    @classmethod
    def _links(cls, v: list[str]) -> list[str]:
        links = [u.strip() for u in v if u.strip()]
        bad = [u for u in links if not _safe_url(u)]
        if bad:
            raise ValueError(f"evidence links must be absolute http(s) URLs: {bad[0][:80]!r}")
        return links
