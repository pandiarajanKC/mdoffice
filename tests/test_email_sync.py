from datetime import date, datetime, timedelta

import pytest

from app.models.email_integration import EmailIntegration
from app.models.mixins import utcnow
from app.services import email_sync_service
from app.services.email.base_email_service import EmailAuthError, EmailFetchError, EmailMessage, EmailTokenSet
from app.utils.token_crypto import decrypt_token, encrypt_token


class FakeEmailService:
    provider_name = "google"

    def __init__(self, messages=None, raise_on_search=None, account_email="ea@example.com"):
        self._messages = messages or []
        self._raise_on_search = raise_on_search
        self._account_email = account_email
        self.search_calls = []
        self.refreshed_with = None

    def get_authorization_url(self, state):
        return f"https://accounts.google.com/fake?state={state}", "fake-code-verifier"

    def exchange_code_for_tokens(self, code, code_verifier=None):
        return EmailTokenSet(access_token="access-1", refresh_token="refresh-1", expiry=None, scope="gmail.readonly", account_email=self._account_email)

    def refresh_access_token(self, refresh_token):
        self.refreshed_with = refresh_token
        return EmailTokenSet(access_token="access-2", refresh_token=refresh_token, expiry=None, scope="gmail.readonly")

    def search_messages(self, access_token, *, from_query, subject_query, date_from, date_to, limit=25):
        self.search_calls.append({
            "access_token": access_token, "from_query": from_query, "subject_query": subject_query,
            "date_from": date_from, "date_to": date_to,
        })
        if self._raise_on_search:
            raise self._raise_on_search
        return self._messages

    def get_message(self, access_token, message_id):
        return next(m for m in self._messages if m.external_message_id == message_id)


def _integration(db, **overrides):
    integration = EmailIntegration(
        owner_username=overrides.pop("owner_username", "ea_user"),
        provider="google",
        access_token_encrypted=encrypt_token("access-token"),
        refresh_token_encrypted=encrypt_token("refresh-token"),
        connected_by="ea_user",
        **overrides,
    )
    db.session.add(integration)
    db.session.commit()
    return integration


def _message(external_id="msg-1", subject="Q3 Budget Review"):
    return EmailMessage(
        external_message_id=external_id,
        thread_id=f"thread-{external_id}",
        subject=subject,
        sender_name="Alice",
        sender_email="alice@example.com",
        received_at=datetime.now(),
        snippet="Please review the attached numbers before Friday.",
        body_text="Full body: please review the attached numbers before Friday.",
    )


def test_complete_authorization_creates_new_integration(app, db, monkeypatch):
    fake = FakeEmailService()
    monkeypatch.setattr("app.services.email_sync_service.factory.get_email_service", lambda: fake)

    integration = email_sync_service.complete_authorization(
        owner_username="ea_user", code="auth-code", connected_by="ea_user", code_verifier="verifier"
    )

    assert integration.owner_username == "ea_user"
    assert integration.google_account_email == "ea@example.com"
    assert decrypt_token(integration.access_token_encrypted) == "access-1"
    assert decrypt_token(integration.refresh_token_encrypted) == "refresh-1"
    assert EmailIntegration.query.count() == 1


def test_complete_authorization_reconnects_existing_integration(app, db, monkeypatch):
    _integration(db, google_account_email="old@example.com")
    fake = FakeEmailService(account_email="new@example.com")
    monkeypatch.setattr("app.services.email_sync_service.factory.get_email_service", lambda: fake)

    email_sync_service.complete_authorization(owner_username="ea_user", code="auth-code", connected_by="ea_user")

    assert EmailIntegration.query.count() == 1  # updated in place, not duplicated
    assert EmailIntegration.query.first().google_account_email == "new@example.com"


def test_complete_authorization_wraps_provider_error(app, db, monkeypatch):
    class FailingService(FakeEmailService):
        def exchange_code_for_tokens(self, code, code_verifier=None):
            raise EmailAuthError("Google rejected the code")

    monkeypatch.setattr("app.services.email_sync_service.factory.get_email_service", lambda: FailingService())

    with pytest.raises(EmailAuthError):
        email_sync_service.complete_authorization(owner_username="ea_user", code="bad-code", connected_by="ea_user")
    assert EmailIntegration.query.count() == 0


def test_search_passes_filters_through_and_updates_last_used(app, db, monkeypatch):
    integration = _integration(db)
    fake = FakeEmailService(messages=[_message()])
    monkeypatch.setattr("app.services.email_sync_service.factory.get_email_service", lambda provider="google": fake)

    results = email_sync_service.search(
        integration, from_query="alice@example.com", subject_query="Budget",
        date_from=date(2026, 1, 1), date_to=date(2026, 1, 31),
    )

    assert len(results) == 1
    assert results[0].subject == "Q3 Budget Review"
    assert fake.search_calls[0]["from_query"] == "alice@example.com"
    assert fake.search_calls[0]["subject_query"] == "Budget"
    assert integration.last_used_at is not None


def test_search_raises_fetch_error(app, db, monkeypatch):
    integration = _integration(db)
    fake = FakeEmailService(raise_on_search=EmailFetchError("Gmail API quota exceeded"))
    monkeypatch.setattr("app.services.email_sync_service.factory.get_email_service", lambda provider="google": fake)

    with pytest.raises(EmailFetchError):
        email_sync_service.search(integration, from_query=None, subject_query=None, date_from=None, date_to=None)


def test_search_refreshes_expired_token_before_calling_provider(app, db, monkeypatch):
    integration = _integration(db, token_expiry=utcnow() - timedelta(minutes=5))
    fake = FakeEmailService(messages=[_message()])
    monkeypatch.setattr("app.services.email_sync_service.factory.get_email_service", lambda provider="google": fake)

    email_sync_service.search(integration, from_query=None, subject_query=None, date_from=None, date_to=None)

    assert fake.refreshed_with == "refresh-token"
    assert decrypt_token(integration.access_token_encrypted) == "access-2"


def test_get_message_fetches_full_body(app, db, monkeypatch):
    integration = _integration(db)
    fake = FakeEmailService(messages=[_message(external_id="msg-42")])
    monkeypatch.setattr("app.services.email_sync_service.factory.get_email_service", lambda provider="google": fake)

    message = email_sync_service.get_message(integration, "msg-42")

    assert message.external_message_id == "msg-42"
    assert "Full body" in message.body_text


def test_disconnect_removes_integration(app, db, monkeypatch):
    integration = _integration(db)
    email_sync_service.disconnect(integration, performed_by="ea_user")
    assert EmailIntegration.query.count() == 0
