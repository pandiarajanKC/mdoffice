"""Picks the configured email provider. Only Gmail exists today, mirroring
app/services/calendar/factory.py — callers go through this factory rather
than importing GmailService directly, so a second provider later means
writing one new adapter class, not touching sync logic, routes, or
templates.
"""
from flask import current_app

from app.services.email.base_email_service import BaseEmailService
from app.services.email.gmail_service import GmailService


def is_configured() -> bool:
    return bool(
        current_app.config.get("GOOGLE_OAUTH_CLIENT_ID") and current_app.config.get("GOOGLE_OAUTH_CLIENT_SECRET")
    )


def get_email_service(provider: str = "google") -> BaseEmailService:
    if provider == "google":
        return GmailService(
            client_id=current_app.config["GOOGLE_OAUTH_CLIENT_ID"],
            client_secret=current_app.config["GOOGLE_OAUTH_CLIENT_SECRET"],
            redirect_uri=current_app.config["GOOGLE_OAUTH_REDIRECT_URI"],
        )
    raise ValueError(f"Unsupported email provider: {provider}")
