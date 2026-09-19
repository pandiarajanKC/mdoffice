"""Email connect/search/convert business logic — the EA's own Gmail inbox
(master spec-consistent extension of the Calendar sync feature). Routes
never talk to the Gmail SDK or the encryption helper directly; everything
goes through here. Mirrors app/services/calendar_sync_service.py.

Nothing here stores email content in this app's database — messages are
searched and fetched live from Gmail every time, and only the OAuth
connection itself (encrypted tokens + the connected account's own email
address, for display) is persisted.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.extensions import db
from app.models.activity_log import ActivityLog
from app.models.email_integration import EmailIntegration
from app.models.mixins import utcnow
from app.services.email import factory
from app.services.email.base_email_service import EmailAuthError, EmailFetchError, EmailMessage
from app.utils.token_crypto import decrypt_token, encrypt_token

SEARCH_LIMIT = 25


def start_authorization(state: str) -> tuple[str, str | None]:
    service = factory.get_email_service()
    return service.get_authorization_url(state)


def complete_authorization(
    *, owner_username: str, code: str, connected_by: str, code_verifier: str | None = None
) -> EmailIntegration:
    service = factory.get_email_service()
    tokens = service.exchange_code_for_tokens(code, code_verifier=code_verifier)  # raises EmailAuthError on failure

    integration = EmailIntegration.query.filter_by(owner_username=owner_username).first()
    is_new = integration is None
    if integration is None:
        integration = EmailIntegration(owner_username=owner_username, provider=service.provider_name, connected_by=connected_by)
        db.session.add(integration)

    integration.access_token_encrypted = encrypt_token(tokens.access_token)
    integration.refresh_token_encrypted = encrypt_token(tokens.refresh_token)
    integration.token_expiry = tokens.expiry.replace(tzinfo=None) if tokens.expiry else None
    integration.scope = tokens.scope
    integration.google_account_email = tokens.account_email
    integration.connected_by = connected_by

    ActivityLog.record(
        entity_type="EMAIL",
        entity_id=owner_username,
        action_type="EMAIL_CONNECTED" if is_new else "EMAIL_RECONNECTED",
        performed_by=connected_by,
        new_value=tokens.account_email,
    )
    db.session.commit()
    return integration


def disconnect(integration: EmailIntegration, *, performed_by: str) -> None:
    owner = integration.owner_username
    db.session.delete(integration)
    ActivityLog.record(entity_type="EMAIL", entity_id=owner, action_type="EMAIL_DISCONNECTED", performed_by=performed_by)
    db.session.commit()


def _valid_access_token(integration: EmailIntegration) -> str:
    access_token = decrypt_token(integration.access_token_encrypted)
    if integration.token_expiry and integration.token_expiry <= utcnow() + timedelta(minutes=2):
        service = factory.get_email_service(integration.provider)
        refresh_token = decrypt_token(integration.refresh_token_encrypted)
        tokens = service.refresh_access_token(refresh_token)
        integration.access_token_encrypted = encrypt_token(tokens.access_token)
        if tokens.refresh_token:
            integration.refresh_token_encrypted = encrypt_token(tokens.refresh_token)
        integration.token_expiry = tokens.expiry.replace(tzinfo=None) if tokens.expiry else None
        db.session.commit()
        access_token = tokens.access_token
    return access_token


def search(
    integration: EmailIntegration, *, from_query: str | None, subject_query: str | None,
    date_from: date | None, date_to: date | None,
) -> list[EmailMessage]:
    service = factory.get_email_service(integration.provider)
    access_token = _valid_access_token(integration)
    messages = service.search_messages(
        access_token, from_query=from_query or None, subject_query=subject_query or None,
        date_from=date_from, date_to=date_to, limit=SEARCH_LIMIT,
    )  # raises EmailFetchError on failure
    integration.last_used_at = utcnow()
    db.session.commit()
    return messages


def get_message(integration: EmailIntegration, message_id: str) -> EmailMessage:
    service = factory.get_email_service(integration.provider)
    access_token = _valid_access_token(integration)
    return service.get_message(access_token, message_id)  # raises EmailFetchError on failure


__all__ = [
    "start_authorization", "complete_authorization", "disconnect", "search", "get_message",
    "EmailAuthError", "EmailFetchError", "SEARCH_LIMIT",
]
