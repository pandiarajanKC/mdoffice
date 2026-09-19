from datetime import datetime, timedelta

import pytest

from app.models.calendar_integration import CalendarIntegration
from app.models.meeting import Meeting, MeetingStatus
from app.services import calendar_sync_service
from app.services.calendar.base_calendar_service import CalendarEvent, CalendarSyncError, TokenSet
from app.utils.token_crypto import decrypt_token, encrypt_token


def test_token_encryption_roundtrip(app):
    with app.app_context():
        encrypted = encrypt_token("super-secret-refresh-token")
        assert encrypted != "super-secret-refresh-token"
        assert decrypt_token(encrypted) == "super-secret-refresh-token"


class FakeCalendarService:
    provider_name = "google"

    def __init__(self, events=None, raise_on_list=None):
        self._events = events or []
        self._raise_on_list = raise_on_list

    def get_authorization_url(self, state):
        return f"https://accounts.google.com/fake?state={state}", "fake-code-verifier"

    def exchange_code_for_tokens(self, code, code_verifier=None):
        return TokenSet(access_token="access-1", refresh_token="refresh-1", expiry=None, scope="calendar.readonly", account_email="md@example.com")

    def refresh_access_token(self, refresh_token):
        return TokenSet(access_token="access-2", refresh_token=refresh_token, expiry=None, scope="calendar.readonly")

    def list_events(self, access_token, *, time_min, time_max):
        if self._raise_on_list:
            raise self._raise_on_list
        return self._events


def _integration(db, **overrides):
    integration = CalendarIntegration(
        owner_username=overrides.pop("owner_username", "admin"),
        provider="google",
        access_token_encrypted=encrypt_token("access-token"),
        refresh_token_encrypted=encrypt_token("refresh-token"),
        connected_by="admin1",
        **overrides,
    )
    db.session.add(integration)
    db.session.commit()
    return integration


def _event(external_id="evt-1", title="Steering Committee", start=None):
    start = start or datetime.now()
    return CalendarEvent(
        external_event_id=external_id,
        title=title,
        description="Discuss roadmap",
        start_datetime=start,
        end_datetime=start + timedelta(hours=1),
        location="Board Room",
        organizer="md@example.com",
        attendees_text="Alice, Bob",
        attendees_emails="alice@example.com, bob@example.com",
        meeting_link=None,
    )


def test_sync_creates_new_meeting(app, db, monkeypatch):
    integration = _integration(db)
    fake = FakeCalendarService(events=[_event()])
    monkeypatch.setattr("app.services.calendar_sync_service.factory.get_calendar_service", lambda provider="google": fake)

    log = calendar_sync_service.sync_now(integration, triggered_by="admin1")

    assert log.success is True
    assert log.events_created == 1
    assert log.events_updated == 0
    meeting = Meeting.query.filter_by(external_calendar_event_id="evt-1", calendar_provider="google").first()
    assert meeting is not None
    assert meeting.title == "Steering Committee"
    assert meeting.attendees_emails == "alice@example.com, bob@example.com"
    assert meeting.status == MeetingStatus.SCHEDULED


def test_sync_cleans_html_description_from_calendar(app, db, monkeypatch):
    import dataclasses

    integration = _integration(db)
    raw_html = '<!-- Converted from text/rtf format --><P DIR=LTR><SPAN><FONT FACE="Aptos">Dear Sir</FONT></SPAN></P>'
    event = dataclasses.replace(_event(), description=raw_html)
    fake = FakeCalendarService(events=[event])
    monkeypatch.setattr("app.services.calendar_sync_service.factory.get_calendar_service", lambda provider="google": fake)

    calendar_sync_service.sync_now(integration, triggered_by="admin1")

    meeting = Meeting.query.filter_by(external_calendar_event_id="evt-1").first()
    assert meeting.description == "Dear Sir"


def test_sync_updates_existing_meeting_without_touching_status(app, db, monkeypatch):
    integration = _integration(db)
    fake = FakeCalendarService(events=[_event(title="Original Title")])
    monkeypatch.setattr("app.services.calendar_sync_service.factory.get_calendar_service", lambda provider="google": fake)
    calendar_sync_service.sync_now(integration, triggered_by="admin1")

    meeting = Meeting.query.filter_by(external_calendar_event_id="evt-1").first()
    meeting.status = MeetingStatus.COMPLETED  # EA already ran this meeting in-app
    from app.extensions import db as _db
    _db.session.commit()

    fake_updated = FakeCalendarService(events=[_event(title="Renamed by MD on Google")])
    monkeypatch.setattr("app.services.calendar_sync_service.factory.get_calendar_service", lambda provider="google": fake_updated)
    log = calendar_sync_service.sync_now(integration, triggered_by="admin1")

    assert log.events_created == 0
    assert log.events_updated == 1
    meeting = Meeting.query.filter_by(external_calendar_event_id="evt-1").first()
    assert meeting.title == "Renamed by MD on Google"
    assert meeting.status == MeetingStatus.COMPLETED  # never reset by sync


def test_sync_does_not_duplicate_on_repeated_sync(app, db, monkeypatch):
    integration = _integration(db)
    fake = FakeCalendarService(events=[_event()])
    monkeypatch.setattr("app.services.calendar_sync_service.factory.get_calendar_service", lambda provider="google": fake)

    calendar_sync_service.sync_now(integration, triggered_by="admin1")
    calendar_sync_service.sync_now(integration, triggered_by="admin1")

    assert Meeting.query.filter_by(external_calendar_event_id="evt-1").count() == 1


def test_sync_records_failure_log_on_error(app, db, monkeypatch):
    integration = _integration(db)
    fake = FakeCalendarService(raise_on_list=CalendarSyncError("API quota exceeded"))
    monkeypatch.setattr("app.services.calendar_sync_service.factory.get_calendar_service", lambda provider="google": fake)

    with pytest.raises(CalendarSyncError):
        calendar_sync_service.sync_now(integration, triggered_by="admin1")

    assert integration.sync_logs[-1].success is False
    assert "quota" in integration.sync_logs[-1].error_message


def test_disconnect_removes_integration_but_keeps_meetings(app, db, monkeypatch):
    integration = _integration(db)
    fake = FakeCalendarService(events=[_event()])
    monkeypatch.setattr("app.services.calendar_sync_service.factory.get_calendar_service", lambda provider="google": fake)
    calendar_sync_service.sync_now(integration, triggered_by="admin1")

    calendar_sync_service.disconnect(integration, performed_by="admin1")

    assert CalendarIntegration.query.count() == 0
    assert Meeting.query.filter_by(external_calendar_event_id="evt-1").count() == 1
