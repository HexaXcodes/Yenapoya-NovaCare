"""
Risk router — stateless composite risk computation.

  POST /risk/compute  {idrs_score, voice_features|voice_score, rppg_features|rppg_hrv_flag}
                      -> {tier, component_scores, explanation, ...}

Accepts both the nested feature objects (matching the API spec's
voice_features / rppg_features) and the flat fields, for convenience.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends

from app.core.deps import require_any
from app.core.responses import AppError, ok
from app.services.risk_engine import evaluate_risk

router = APIRouter(prefix="/risk", tags=["risk"])


def _extract_voice(payload: Dict[str, Any]) -> float:
    if "voice_score" in payload and payload["voice_score"] is not None:
        return float(payload["voice_score"])
    vf = payload.get("voice_features") or {}
    if isinstance(vf, dict) and vf.get("voice_score") is not None:
        return float(vf["voice_score"])
    return 0.0


def _extract_rppg(payload: Dict[str, Any]) -> bool:
    if "rppg_hrv_flag" in payload and payload["rppg_hrv_flag"] is not None:
        return bool(payload["rppg_hrv_flag"])
    rf = payload.get("rppg_features") or {}
    if isinstance(rf, dict) and rf.get("hrv_flag") is not None:
        return bool(rf["hrv_flag"])
    return False


@router.post("/compute")
async def compute_risk(
    payload: Dict[str, Any] = Body(...),
    _principal=Depends(require_any),
):
    if "idrs_score" not in payload or payload["idrs_score"] is None:
        raise AppError("idrs_score is required", status_code=400)

    idrs = float(payload["idrs_score"])
    if not (0 <= idrs <= 100):
        raise AppError("idrs_score must be between 0 and 100", status_code=400)

    voice = _extract_voice(payload)
    rppg = _extract_rppg(payload)

    result = evaluate_risk(
        idrs_score=idrs,
        voice_score=voice,
        rppg_hrv_flag=rppg,
        waist_cm=payload.get("waist_cm"),
        dietary_score=payload.get("dietary_score"),
        occupation_transition_flag=bool(payload.get("occupation_transition_flag", False)),
        family_history_flag=bool(payload.get("family_history_flag", False)),
        lang=payload.get("lang", "en"),
    )
    # Expose under both keys the spec mentions.
    data = result.model_dump()
    data["component_scores"] = data["component_breakdown"]
    return ok(data)
