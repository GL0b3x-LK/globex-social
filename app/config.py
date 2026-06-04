"""Application settings, loaded from environment / .env.

Required fields have no default, so a missing credential fails fast at startup
with a clear pydantic ValidationError rather than a confusing runtime crash.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Anthropic ---
    anthropic_api_key: str
    claude_model: str = "claude-opus-4-7"

    # --- OpenAI (Whisper speech-to-text only; Anthropic has no STT) ---
    # Optional: if unset, voice notes degrade gracefully ("voice isn't set up")
    # rather than blocking the whole app from booting.
    openai_api_key: str | None = None
    whisper_model: str = "whisper-1"

    # --- Twilio ---
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_whatsapp_number: str
    authorized_numbers: str  # comma-separated; parsed via authorized_numbers_list
    twilio_validate_signature: bool = True  # verify X-Twilio-Signature; disable only in dev

    # --- Blotato ---
    blotato_api_key: str

    # --- Supabase ---
    supabase_url: str
    supabase_key: str
    supabase_storage_bucket: str = "post-images"
    supabase_db_url: str | None = None  # DDL only (apply_schema.py); not needed at runtime

    # --- App ---
    environment: str = "development"
    timezone: str = "America/New_York"
    log_level: str = "INFO"

    @property
    def authorized_numbers_list(self) -> list[str]:
        """Normalised allowlist of WhatsApp senders (lowercased, no spaces)."""
        return [
            n.strip().lower().replace(" ", "")
            for n in self.authorized_numbers.split(",")
            if n.strip()
        ]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    """Cached singleton. First call validates all required env vars (fail-fast)."""
    return Settings()  # type: ignore[call-arg]  # values come from env / .env
