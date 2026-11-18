from fastapi import APIRouter, Depends, Request

from app.auth import require_service_token
from app.schemas.risk import RiskScoreRequest, RiskScoreResponse
from app.services.anti_scalping import calculate_risk_score

router = APIRouter(tags=["risk-score"])


@router.post("/api/v1/risk-score", response_model=RiskScoreResponse)
async def risk_score(body: RiskScoreRequest, request: Request, _=Depends(require_service_token)):
    return calculate_risk_score({
        "time_elapsed_seconds": body.time_elapsed_seconds,
        "account_age_hours": body.account_age_hours,
        "tickets_requested": body.tickets_requested,
    })
