"""Configuration from environment variables. Fails fast on unsafe prod config."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_env: str
    secret_key: str
    allowed_emails: frozenset[str]
    google_client_id: str
    google_client_secret: str
    dev_user_email: str

    @property
    def is_dev(self) -> bool:
        return self.app_env == "dev"


def load_settings() -> Settings:
    s = Settings(
        app_env=os.environ.get("APP_ENV", "prod"),
        secret_key=os.environ.get("SECRET_KEY", ""),
        allowed_emails=frozenset(
            e.strip().lower() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()
        ),
        google_client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
        google_client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        dev_user_email=os.environ.get("DEV_USER_EMAIL", "").strip().lower(),
    )
    if s.app_env not in ("dev", "prod"):
        raise RuntimeError("APP_ENV must be 'dev' or 'prod'")
    if not s.allowed_emails:
        raise RuntimeError("ALLOWED_EMAILS must list at least one email")
    if not s.is_dev:
        if len(s.secret_key) < 32:
            raise RuntimeError("SECRET_KEY must be at least 32 characters in prod")
        if not (s.google_client_id and s.google_client_secret):
            raise RuntimeError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are required in prod")
    return s
