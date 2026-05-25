"""
Standard API response envelope and shared exception handling.

Every endpoint returns:  {success: bool, data: ... | null, error: str | null}
"""
from __future__ import annotations

from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    success: bool
    data: Optional[T] = None
    error: Optional[str] = None


def ok(data: Any = None) -> dict:
    return {"success": True, "data": data, "error": None}


def fail(error: str, data: Any = None) -> dict:
    return {"success": False, "data": data, "error": error}


class AppError(Exception):
    """Domain error that maps to a clean envelope + HTTP status."""

    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)
