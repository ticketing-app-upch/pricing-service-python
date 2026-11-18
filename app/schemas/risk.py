from typing import Annotated

from pydantic import BaseModel, Field


class RiskScoreRequest(BaseModel):
    time_elapsed_seconds: Annotated[float, Field(ge=0, strict=True)]
    account_age_hours: Annotated[float, Field(ge=0, strict=True)]
    tickets_requested: Annotated[int, Field(ge=1, le=6, strict=True)]


class RiskScoreResponse(BaseModel):
    risk_score: float
    action: str
