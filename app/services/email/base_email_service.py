"""Email provider abstraction — mirrors app/services/calendar/base_calendar_service.py.

Business logic (app/services/email_sync_service.py) depends only on this
interface, never on a specific provider's SDK — so adding another provider
later (Outlook/Graph) means writing one new adapter class, not touching
sync logic, routes, or templates.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class EmailTokenSet:
    access_token: str
    refresh_token: str
    expiry: datetime | None
    scope: str | None
    account_email: str | None = None


@dataclass(frozen=True)
class EmailMessage:
    external_message_id: str
    thread_id: str
    subject: str
    sender_name: str | None
    sender_email: str | None
    received_at: datetime | None
    snippet: str
    body_text: str | None = None
    web_link: str | None = None


class EmailAuthError(Exception):
    """Raised when authorization/token exchange fails."""


class EmailFetchError(Exception):
    """Raised when searching/fetching messages fails."""


class BaseEmailService(ABC):
    provider_name: str

    @abstractmethod
    def get_authorization_url(self, state: str) -> tuple[str, str | None]:
        """Build the URL to send the user to the provider's consent screen.

        Returns (authorization_url, code_verifier) — see the calendar
        service's identical contract for why the verifier must be
        persisted by the caller across the redirect.
        """

    @abstractmethod
    def exchange_code_for_tokens(self, code: str, code_verifier: str | None = None) -> EmailTokenSet:
        """Exchange the OAuth callback code (and PKCE verifier, if any) for tokens."""

    @abstractmethod
    def refresh_access_token(self, refresh_token: str) -> EmailTokenSet:
        """Get a fresh access token once the stored one has expired."""

    @abstractmethod
    def search_messages(
        self, access_token: str, *, from_query: str | None, subject_query: str | None,
        date_from: date | None, date_to: date | None, limit: int = 25,
    ) -> list[EmailMessage]:
        """Search the inbox, already normalized. body_text is left unset here
        (a snippet is enough for a results list) — call get_message for the
        full body once the EA picks one to convert.
        """

    @abstractmethod
    def get_message(self, access_token: str, message_id: str) -> EmailMessage:
        """Fetch one message with its full body_text populated."""
