"""Application settings.

Single place where environment variables are read. Nothing else in the
codebase should call os.getenv() directly.

Rules:
- Secrets come from the environment / .env only.
- Never log the value of a secret, only whether it is configured.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---
    app_env: str = "local"
    app_base_url: str = "http://localhost:8000"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:8501"
    # Where the browser is sent after the OAuth callback. Without this the
    # callback would strand the user on a JSON response from the API.
    frontend_url: str = "http://localhost:8501"
    database_path: str = "data/inboxpilot.db"

    # --- Nebius Token Factory ---
    nebius_api_key: str = ""
    nebius_base_url: str = "https://api.tokenfactory.nebius.com/v1/"
    nebius_model_fast: str = ""
    nebius_model_reasoning: str = ""
    # Third role, for reading a photo of a timetable. A vision-language model
    # rather than OCR: a university timetable is a grid, and a VLM reads the
    # grid, not a pile of unrelated words.
    nebius_model_vision: str = "nvidia/Nemotron-3.5-Super-VL"
    nebius_timeout_seconds: float = 60.0
    nebius_max_retries: int = 2

    # --- Google / Gmail ---
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"
    google_scopes: str = (
        "https://www.googleapis.com/auth/gmail.readonly "
        "https://www.googleapis.com/auth/gmail.compose"
    )

    # --- Demo account used during the judging period ---
    demo_refresh_token: str = ""
    demo_account_email: str = ""

    # --- Safety / limits ---
    max_emails_per_request: int = 20
    allow_email_send: bool = False

    # ---- derived values -------------------------------------------------

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def google_scope_list(self) -> list[str]:
        return [s.strip() for s in self.google_scopes.split() if s.strip()]

    @property
    def db_path(self) -> Path:
        p = Path(self.database_path)
        return p if p.is_absolute() else REPO_ROOT / p

    @property
    def is_nebius_configured(self) -> bool:
        return bool(self.nebius_api_key)

    @property
    def is_google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def is_demo_account_configured(self) -> bool:
        return bool(self.demo_refresh_token)


@lru_cache
def get_settings() -> Settings:
    return Settings()
