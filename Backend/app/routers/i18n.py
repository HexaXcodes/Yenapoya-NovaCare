"""
I18N + TTS router.

  GET  /i18n/languages   supported languages list
  POST /tts/generate     {text, lang} -> audio (base64) or URL

TTS is mounted under the same router module for convenience but uses the
/tts prefix.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import require_any
from app.core.i18n import supported_languages
from app.core.responses import ok
from app.schemas.misc import TtsRequest
from app.services.tts_service import generate_tts

i18n_router = APIRouter(prefix="/i18n", tags=["i18n"])
tts_router = APIRouter(prefix="/tts", tags=["tts"])


@i18n_router.get("/languages")
async def languages():
    return ok({"languages": supported_languages()})


@tts_router.post("/generate")
async def tts_generate(
    body: TtsRequest,
    _principal=Depends(require_any),
):
    result = await generate_tts(body.text, body.lang)
    return ok(result.model_dump())
