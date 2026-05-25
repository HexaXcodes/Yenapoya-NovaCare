"""
TTS service.

Wraps a text-to-speech backend (Coqui or Google) behind a single async
interface. In dev the provider is `mock`, which returns a tiny silent WAV as
base64 so the frontend flow can be exercised end-to-end without credentials.

Generated audio is cached on disk keyed by a hash of (text, lang) to avoid
regenerating the same prompt repeatedly (useful for fixed report templates).
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import struct
from typing import Optional

from app.config import settings
from app.core.i18n import normalise_lang
from app.schemas.misc import TtsResult

logger = logging.getLogger("novacare.tts")


def _cache_path(text: str, lang: str) -> str:
    os.makedirs(settings.TTS_OUTPUT_DIR, exist_ok=True)
    key = hashlib.sha256(f"{lang}:{text}".encode("utf-8")).hexdigest()[:24]
    return os.path.join(settings.TTS_OUTPUT_DIR, f"{lang}_{key}.wav")


def _silent_wav_bytes(duration_s: float = 0.4, sample_rate: int = 8000) -> bytes:
    """Generate a minimal valid silent WAV (PCM16 mono) — used by the mock."""
    n_samples = int(duration_s * sample_rate)
    data = b"\x00\x00" * n_samples
    byte_rate = sample_rate * 2
    header = b"RIFF"
    header += struct.pack("<I", 36 + len(data))
    header += b"WAVE"
    header += b"fmt "
    header += struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, byte_rate, 2, 16)
    header += b"data"
    header += struct.pack("<I", len(data))
    return header + data


async def generate_tts(text: str, lang: str = "en") -> TtsResult:
    lang = normalise_lang(lang)
    path = _cache_path(text, lang)

    if os.path.exists(path):
        with open(path, "rb") as fh:
            audio_b64 = base64.b64encode(fh.read()).decode("ascii")
        return TtsResult(
            provider=settings.TTS_PROVIDER,
            lang=lang,
            audio_base64=audio_b64,
            cached=True,
        )

    provider = settings.TTS_PROVIDER
    if provider == "mock":
        audio = _silent_wav_bytes()
    elif provider == "coqui":
        audio = _synthesize_coqui(text, lang)
    elif provider == "google":
        audio = _synthesize_google(text, lang)
    else:
        raise ValueError(f"Unknown TTS provider: {provider}")

    with open(path, "wb") as fh:
        fh.write(audio)

    return TtsResult(
        provider=provider,
        lang=lang,
        audio_base64=base64.b64encode(audio).decode("ascii"),
        cached=False,
    )


def _synthesize_coqui(text: str, lang: str) -> bytes:
    """
    Placeholder for a Coqui TTS integration. Import lazily so the dependency
    is only required when actually configured.
    """
    try:
        from TTS.api import TTS  # type: ignore

        model = TTS(model_name="tts_models/multilingual/multi-dataset/xtts_v2")
        tmp = _cache_path(text, lang) + ".tmp.wav"
        model.tts_to_file(text=text, language=lang, file_path=tmp)
        with open(tmp, "rb") as fh:
            data = fh.read()
        os.remove(tmp)
        return data
    except Exception:  # noqa: BLE001
        logger.exception("Coqui TTS failed; returning silent fallback")
        return _silent_wav_bytes()


def _synthesize_google(text: str, lang: str) -> bytes:
    """Placeholder for Google Cloud TTS; lazy import."""
    try:
        from google.cloud import texttospeech  # type: ignore

        client = texttospeech.TextToSpeechClient()
        synthesis_input = texttospeech.SynthesisInput(text=text)
        voice = texttospeech.VoiceSelectionParams(
            language_code={"hi": "hi-IN", "kn": "kn-IN", "ta": "ta-IN",
                           "te": "te-IN", "bn": "bn-IN", "mr": "mr-IN",
                           "en": "en-IN"}.get(lang, "en-IN")
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16
        )
        resp = client.synthesize_speech(
            input=synthesis_input, voice=voice, audio_config=audio_config
        )
        return resp.audio_content
    except Exception:  # noqa: BLE001
        logger.exception("Google TTS failed; returning silent fallback")
        return _silent_wav_bytes()
