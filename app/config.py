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

    # --- ElevenLabs (video engine: one locked voice per character) ---
    # Optional: unset only disables the video engine's speech, not the app.
    elevenlabs_api_key: str | None = None

    # --- Video generation ---
    # Higgsfield is the client's own account and the tool the approved reference
    # video came from, so it wins when configured; kie.ai is the fallback.
    # Auth is "Key {key}:{secret}", not a bearer token.
    video_provider: str | None = None  # "higgsfield" | "kie" | None (auto)
    higgsfield_api_key: str | None = None
    higgsfield_api_secret: str | None = None
    higgsfield_broll_model: str | None = None
    # Lip-sync is not on Higgsfield's documented REST surface; set these once the
    # account's model gallery confirms what it exposes, rather than guessing and
    # silently shipping a mute clip that ignores the voice track.
    higgsfield_speaking_model: str | None = None
    higgsfield_audio_param: str | None = None

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
    # Day one of the 52-week calendar, "YYYY-MM-DD". UNSET = the calendar is
    # dormant: no entry is ever due, so nothing drafts and nothing is silently
    # skipped while the client decides when to go live. Setting it re-flows the
    # whole year from that date (see app/db/calendar_source.py).
    calendar_launch_date: str | None = None
    # The client's cadence: the draft lands at 7am on the previous WORKING day
    # (so a Monday post previews on Friday), giving them a full business day to
    # edit and approve, and an approved post then waits — however early the yes
    # came — until 1am on the date itself. The lead is a rule, not a number:
    # see clock.next_working_day.
    draft_hour: int = 7  # local hour (timezone above) the daily draft job runs
    publish_hour: int = 1  # local hour an approved post goes live on its date

    # --- Calendar sheet bridge (Apps Script web app on the client's Sheet) ---
    # Both set = the "Exact Caption" column becomes live: client-authored
    # captions post verbatim, and published captions are written back.
    # Unset = the calendar behaves exactly as before.
    sheet_webapp_url: str | None = None
    sheet_webapp_secret: str | None = None

    # --- Internal test run ---
    # Walks the approved calendar in order, dropping one post every
    # `test_interval_hours` instead of on its real date, so the team can see the
    # whole flow — draft, approve, edit, publish — in an afternoon rather than a
    # year. The approval gate is untouched: nothing publishes without a yes.
    # Turn OFF before the client cadence starts.
    test_mode: bool = False
    test_interval_hours: float = 2.0
    # Local-time anchor for the test grid ("2026-08-12T12:00"), instead of the
    # default local midnight. Set it when the run must begin at a particular
    # moment — the Twilio trial cap frees capacity on a rolling window, and a
    # grid anchored to midnight would fire into a closed window and draft posts
    # nobody can receive. Slots before the anchor are simply never scheduled.
    test_start_at: str | None = None
    # Who receives approval previews. Empty = the first authorised number, which
    # is the production behaviour. During the test run both testers are listed so
    # either can approve.
    approval_recipients: str = ""

    @property
    def approval_recipients_list(self) -> list[str]:
        """Everyone who gets a preview. Falls back to the first authorised number."""
        explicit = [
            n.strip().lower().replace(" ", "")
            for n in self.approval_recipients.split(",")
            if n.strip()
        ]
        if explicit:
            return explicit
        allowed = self.authorized_numbers_list
        return allowed[:1]

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
