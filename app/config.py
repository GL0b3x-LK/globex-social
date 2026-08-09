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

    # --- kie.ai (AI image generation: nano-banana) ---
    # Optional: if unset, image-generation requests fall back to a designed
    # typographic post. The generated image is only ever the photographic layer
    # UNDER the brand template overlay — brand identity stays in the template.
    kie_api_key: str | None = None
    kie_base_url: str = "https://api.kie.ai"
    kie_image_model: str = "nano-banana-2"  # text->image; verified live 2026-06-04
    kie_edit_model: str = "nano-banana-2"  # image->image (img2img) via image_input

    # --- Twilio ---
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_whatsapp_number: str
    authorized_numbers: str  # comma-separated; parsed via authorized_numbers_list
    twilio_validate_signature: bool = True  # verify X-Twilio-Signature; disable only in dev

    # --- Blotato (publishing to IG/FB/LinkedIn) ---
    blotato_api_key: str
    blotato_base_url: str = "https://backend.blotato.com/v2"

    # --- Supabase ---
    supabase_url: str
    supabase_key: str
    supabase_storage_bucket: str = "post-images"
    supabase_db_url: str | None = None  # DDL only (apply_schema.py); not needed at runtime

    # --- App ---
    environment: str = "development"
    timezone: str = "America/New_York"
    log_level: str = "INFO"

    # --- Calendar scheduler (Phase 6) ---
    # Drafts upcoming calendar posts and sends them to the approver's WhatsApp;
    # publishes approved posts on their scheduled date. Off by default so dev
    # servers and tests never fire real drafts.
    scheduler_enabled: bool = False
    draft_lead_days: int = 3  # draft/preview posts this many days before post_date
    draft_hour: int = 7  # local hour (timezone above) the daily draft job runs
    publish_hour: int = 9  # local hour an approved post goes live on its date

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
