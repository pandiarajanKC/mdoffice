"""Calendar provider abstraction (master spec section 6).

Business logic (app/services/calendar_sync_service.py) depends only on
this interface, never on a specific provider's SDK — so adding Microsoft
Graph later means writing one new adapter class, not touching sync logic,
routes, or templates.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str
    expiry: datetime | None
    scope: str | None
    account_email: str | None = None


@dataclass(frozen=True)
class CalendarEvent:
    external_event_id: str
    title: str
    description: str | None
    start_datetime: datetime
    end_datetime: datetime
    location: str | None
    organizer: str | None
    attendees_text: str | None
    attendees_emails: str | None
    meeting_link: str | None


class CalendarAuthError(Exception):
    """Raised when authorization/token exchange fails."""


class CalendarSyncError(Exception):
    """Raised when fetching events fails."""


class BaseCalendarService(ABC):
    provider_name: str

    @abstractmethod
    def get_authorization_url(self, state: str) -> tuple[str, str | None]:
        """Build the URL to send the user to the provider's consent screen.

        Returns (authorization_url, code_verifier). Providers using PKCE
        return the verifier they bound to the URL's code_challenge; the
        caller must persist it (e.g. in the session) and pass it back into
        exchange_code_for_tokens, since it can't be regenerated later — a
        fresh Flow object won't know the verifier used for this request.
        """

    @abstractmethod
    def exchange_code_for_tokens(self, code: str, code_verifier: str | None = None) -> TokenSet:
        """Exchange the OAuth callback code (and PKCE verifier, if any) for tokens."""

    @abstractmethod
    def refresh_access_token(self, refresh_token: str) -> TokenSet:
        """Get a fresh access token once the stored one has expired."""

    @abstractmethod
    def list_events(self, access_token: str, *, time_min: datetime, time_max: datetime) -> list[CalendarEvent]:
        """Fetch events in the given window, already normalized."""
