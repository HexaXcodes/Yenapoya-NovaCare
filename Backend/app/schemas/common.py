"""
Shared Pydantic v2 helpers, including a MongoDB ObjectId type that
serialises cleanly to/from strings.
"""
from __future__ import annotations

from typing import Annotated, Any

from bson import ObjectId
from pydantic import BeforeValidator, PlainSerializer

__all__ = ["PyObjectId"]


def _validate_object_id(v: Any) -> str:
    if isinstance(v, ObjectId):
        return str(v)
    if isinstance(v, str):
        # Accept both raw 24-char hex and already-stringified ids.
        return v
    raise ValueError("Invalid ObjectId")


# A string field that tolerates ObjectId input and always serialises to str.
PyObjectId = Annotated[
    str,
    BeforeValidator(_validate_object_id),
    PlainSerializer(lambda x: str(x), return_type=str),
]
