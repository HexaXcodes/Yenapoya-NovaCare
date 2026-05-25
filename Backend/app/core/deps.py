"""
Auth dependencies for role-based access control.

Usage in routers:
    @router.get("/secret")
    async def f(user = Depends(require_role("doctor"))): ...
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable

from fastapi import Depends, Header, HTTPException, status

from app.core.security import try_decode_token


async def get_current_principal(
    authorization: str = Header(default=""),
) -> Dict[str, Any]:
    """Extract and validate the JWT from the Authorization header."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
        )
    token = authorization.split(" ", 1)[1].strip()
    payload = try_decode_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    return payload


def require_role(*allowed: str) -> Callable:
    """Dependency factory that enforces one of the allowed roles."""

    allowed_set: Iterable[str] = set(allowed)

    async def _checker(
        principal: Dict[str, Any] = Depends(get_current_principal),
    ) -> Dict[str, Any]:
        role = principal.get("role")
        if role not in allowed_set:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{role}' not permitted for this resource",
            )
        return principal

    return _checker


# Convenience pre-built dependencies
require_asha = require_role("asha")
require_patient = require_role("patient")
require_doctor = require_role("doctor")
require_asha_or_doctor = require_role("asha", "doctor")
require_any = require_role("asha", "patient", "doctor")
