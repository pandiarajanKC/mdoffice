"""Picks the configured calendar provider. Only Google exists today, but
callers (routes, sync service) go through this factory rather than
importing GoogleCalendarService directly, so a Microsoft Graph adapter can
be added later without touching them.
"""
from flask import current_app

from app.services.calendar.base_calendar_service import BaseCalendarService
from app.services.calendar.google_calendar_service import GoogleCalendarService


def is_configured() -> bool:
    return bool(
        current_app.config.get("GOOGLE_OAUTH_CLIENT_ID") and current_app.config.get("GOOGLE_OAUTH_CLIENT_SECRET")
    )


def get_calendar_service(provider: str = "google") -> BaseCalendarService:
    if provider == "google":
        return GoogleCalendarService(
            client_id=current_app.config["GOOGLE_OAUTH_CLIENT_ID"],
            client_secret=current_app.config["GOOGLE_OAUTH_CLIENT_SECRET"],
            redirect_uri=current_app.config["GOOGLE_OAUTH_REDIRECT_URI"],
        )
    raise ValueError(f"Unsupported calendar provider: {provider}")
