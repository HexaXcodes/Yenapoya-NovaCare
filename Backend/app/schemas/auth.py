"""Auth request/response schemas (Pydantic v2)."""
from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class AshaLoginRequest(BaseModel):
    asha_id: str = Field(..., min_length=2, max_length=40)
    pin: str = Field(..., min_length=4, max_length=12)


class PatientOtpRequest(BaseModel):
    phone: str = Field(..., min_length=8, max_length=15)


class PatientVerifyRequest(BaseModel):
    phone: str = Field(..., min_length=8, max_length=15)
    otp: str = Field(..., min_length=4, max_length=8)


class DoctorLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    expires_in_hours: int


class OtpSentResponse(BaseModel):
    phone_masked: str
    ttl_seconds: int
    # In dev only, the mock OTP is echoed for convenience.
    dev_otp: str | None = None
