"""
Security primitives: JWT issuing/verification, bcrypt password hashing,
OTP generation, and privacy-preserving phone hashing.

Privacy note: we never store raw phone numbers in records or logs. Only a
salted SHA-256 hash (`phone_hash`) is persisted for lookup.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import bcrypt
from jose import JWTError, jwt

from app.config import settings


# --------------------------------------------------------------------------- #
# Password hashing (doctors)
# --------------------------------------------------------------------------- #
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# --------------------------------------------------------------------------- #
# JWT
# --------------------------------------------------------------------------- #
def create_access_token(
    claims: Dict[str, Any],
    expires_hours: int,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        **claims,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=expires_hours)).timestamp()),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> Dict[str, Any]:
    """Raise JWTError if invalid/expired."""
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])


def try_decode_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        return decode_token(token)
    except JWTError:
        return None


# --------------------------------------------------------------------------- #
# OTP
# --------------------------------------------------------------------------- #
def generate_otp(length: int | None = None) -> str:
    length = length or settings.OTP_LENGTH
    # Cryptographically secure numeric OTP.
    return "".join(secrets.choice("0123456789") for _ in range(length))


# --------------------------------------------------------------------------- #
# Phone privacy hashing
# --------------------------------------------------------------------------- #
def hash_phone(phone: str) -> str:
    """
    Deterministic, salted hash of a phone number for lookup without storing
    the raw value. Uses the JWT secret as the HMAC key (rotate carefully).
    """
    normalised = "".join(ch for ch in phone if ch.isdigit())
    return hmac.new(
        settings.JWT_SECRET.encode("utf-8"),
        normalised.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def mask_phone(phone: str) -> str:
    """For safe display/logging: keep last 4 digits only."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) <= 4:
        return "****"
    return "*" * (len(digits) - 4) + digits[-4:]
