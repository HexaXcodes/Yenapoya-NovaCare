"""
Application configuration.

All settings are loaded from environment variables with sensible
development defaults. In production, override via a .env file or the
deployment environment. Nothing secret is hard-coded.
"""
from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- App ----
    APP_NAME: str = "NovaCare"
    APP_ENV: str = Field(default="dev")  # dev | staging | prod
    DEBUG: bool = Field(default=True)
    API_PREFIX: str = ""  # routers already namespaced (e.g. /auth, /patients)

    # ---- Security / JWT ----
    JWT_SECRET: str = Field(default="dev-insecure-change-me")
    JWT_ALGORITHM: str = Field(default="HS256")
    ASHA_TOKEN_EXPIRE_HOURS: int = Field(default=12)
    PATIENT_TOKEN_EXPIRE_HOURS: int = Field(default=24)
    DOCTOR_TOKEN_EXPIRE_HOURS: int = Field(default=12)

    # ---- OTP (patient login) ----
    OTP_LENGTH: int = Field(default=6)
    OTP_TTL_SECONDS: int = Field(default=300)  # 5 minutes

    # ---- MongoDB ----
    MONGO_URI: str = Field(default="mongodb://localhost:27017")
    MONGO_DB: str = Field(default="novacare")

    # ---- SQLite (on-device mirror / fallback) ----
    SQLITE_URL: str = Field(default="sqlite+aiosqlite:///./novacare_local.db")

    # ---- CORS ----
    # Comma-separated list of allowed origins in env, e.g.
    # CORS_ORIGINS="https://asha.novacare.in,https://doctor.novacare.in"
    CORS_ORIGINS: str = Field(default="http://localhost:3000,http://localhost:5173")

    # ---- SMS gateway (MSG91 / Twilio) ----
    SMS_PROVIDER: str = Field(default="mock")  # mock | msg91 | twilio
    SMS_API_KEY: str = Field(default="")
    SMS_SENDER_ID: str = Field(default="NOVCRE")
    TWILIO_FROM: str = Field(default="")

    # ---- FCM push ----
    FCM_SERVER_KEY: str = Field(default="")
    FCM_PROVIDER: str = Field(default="mock")  # mock | fcm

    # ---- TTS ----
    TTS_PROVIDER: str = Field(default="mock")  # mock | coqui | google
    TTS_OUTPUT_DIR: str = Field(default="./data/tts_cache")

    # ---- Follow-up rules ----
    MISSED_FOLLOWUP_DAYS: int = Field(default=21)

    # ---- Data paths ----
    DIET_KB_PATH: str = Field(default="data/diet_advice.json")
    TRANSLATIONS_DIR: str = Field(default="data/translations")

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor used as a FastAPI dependency."""
    return Settings()


settings = get_settings()
