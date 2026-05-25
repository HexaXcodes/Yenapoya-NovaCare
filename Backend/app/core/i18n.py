"""
Lightweight i18n loader.

Translation files live at data/translations/{lang}.json as flat key->string
maps. We support: hi, kn, ta, te, bn, mr, en. English is the fallback.

The loader caches files in memory and exposes a `translate(key, lang, **fmt)`
helper that does {placeholder} substitution.
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Dict, List

from app.config import settings

logger = logging.getLogger("novacare.i18n")

SUPPORTED_LANGS: List[str] = ["hi", "kn", "ta", "te", "bn", "mr", "en"]
DEFAULT_LANG = "en"


def normalise_lang(lang: str | None) -> str:
    if not lang:
        return DEFAULT_LANG
    lang = lang.lower().strip()
    return lang if lang in SUPPORTED_LANGS else DEFAULT_LANG


@lru_cache(maxsize=len(SUPPORTED_LANGS))
def _load_lang(lang: str) -> Dict[str, str]:
    path = os.path.join(settings.TRANSLATIONS_DIR, f"{lang}.json")
    if not os.path.exists(path):
        logger.warning("Translation file missing for lang=%s (%s)", lang, path)
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def translate(key: str, lang: str | None = None, **fmt) -> str:
    """
    Resolve a translation key. Falls back to English, then to the raw key.
    Performs str.format substitution with the supplied kwargs.
    """
    lang = normalise_lang(lang)
    table = _load_lang(lang)
    value = table.get(key)
    if value is None and lang != DEFAULT_LANG:
        value = _load_lang(DEFAULT_LANG).get(key)
    if value is None:
        value = key
    try:
        return value.format(**fmt) if fmt else value
    except (KeyError, IndexError):
        return value


def supported_languages() -> List[Dict[str, str]]:
    """Human-readable language list for /i18n/languages."""
    names = {
        "hi": "हिन्दी (Hindi)",
        "kn": "ಕನ್ನಡ (Kannada)",
        "ta": "தமிழ் (Tamil)",
        "te": "తెలుగు (Telugu)",
        "bn": "বাংলা (Bengali)",
        "mr": "मराठी (Marathi)",
        "en": "English",
    }
    return [{"code": c, "name": names[c]} for c in SUPPORTED_LANGS]
