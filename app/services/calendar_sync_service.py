"""Calendar connect/sync/disconnect business logic (master spec sections
6-7). Routes never talk to the Google SDK or the encryption helper
directly — everything goes through here.
"""
from __future__ import annotations

import secrets
from datetime import timedelta

from app.extensions import db
from app.models.activity_log import ActivityLog
from app.models.calendar_integration import CalendarIntegration, CalendarSyncLog
from app.models.meeting import Meeting, MeetingStatus
from app.models.mixins import utcnow
from app.services.calendar import factory
from app.services.calendar.base_calendar_service import CalendarAuthError, CalendarSyncError
from app.utils.text_clean import clean_description
from app.utils.token_crypto import decrypt_token, encrypt_token

SYNC_DAYS_BACK = 7
SYNC_DAYS_FORWARD = 60


def new_oauth_state() -> str:
    return secrets.token_urlsafe(24)


def start_authorization(state: str) -> tuple[str, str | None]:
    service = factory.get_calendar_service()
    return service.get_authorization_url(state)


def complete_authorization(
    *, owner_username: str, code: str, connected_by: str, code_verifier: str | None = None
) -> CalendarIntegration:
    service = factory.get_calendar_service()
    try:
        tokens = service.exchange_code_for_tokens(code, code_verifier=code_verifier)
    except CalendarAuthError:
        raise

    integration = CalendarIntegration.query.filter_by(owner_username=owner_username).first()
    is_new = integration is None
    if integration is None:
        integration = CalendarIntegration(owner_username=owner_username, provider=service.provider_name, connected_by=connected_by)
        db.session.add(integration)

    integration.access_token_encrypted = encrypt_token(tokens.access_token)
    integration.refresh_token_encrypted = encrypt_token(tokens.refresh_token)
    integration.token_expiry = tokens.expiry.replace(tzinfo=None) if tokens.expiry else None
    integration.scope = tokens.scope
    integration.google_account_email = tokens.account_email
    integration.connected_by = connected_by

    ActivityLog.record(
        entity_type="CALENDAR",
        entity_id=owner_username,
        action_type="CALENDAR_CONNECTED" if is_new else "CALENDAR_RECONNECTED",
        performed_by=connected_by,
        new_value=tokens.account_email,
    )
    db.session.commit()
    return integration


def disconnect(integration: CalendarIntegration, *, performed_by: str) -> None:
    owner = integration.owner_username
    db.session.delete(integration)
    ActivityLog.record(entity_type="CALENDAR", entity_id=owner, action_type="CALENDAR_DISCONNECTED", performed_by=performed_by)
    db.session.commit()


def _valid_access_token(integration: CalendarIntegration) -> str:
    access_token = decrypt_token(integration.access_token_encrypted)
    if integration.token_expiry and integration.token_expiry <= utcnow() + timedelta(minutes=2):
        service = factory.get_calendar_service(integration.provider)
        refresh_token = decrypt_token(integration.refresh_token_encrypted)
        tokens = service.refresh_access_token(refresh_token)
        integration.access_token_encrypted = encrypt_token(tokens.access_token)
        if tokens.refresh_token:
            integration.refresh_token_encrypted = encrypt_token(tokens.refresh_token)
        integration.token_expiry = tokens.expiry.replace(tzinfo=None) if tokens.expiry else None
        db.session.commit()
        access_token = tokens.access_token
    return access_token


def sync_now(integration: CalendarIntegration, *, triggered_by: str) -> CalendarSyncLog:
    service = factory.get_calendar_service(integration.provider)

    try:
        access_token = _valid_access_token(integration)
        time_min = utcnow() - timedelta(days=SYNC_DAYS_BACK)
        time_max = utcnow() + timedelta(days=SYNC_DAYS_FORWARD)
        events = service.list_events(access_token, time_min=time_min, time_max=time_max)

        created = 0
        updated = 0
        for event in events:
            meeting = Meeting.query.filter_by(
                external_calendar_event_id=event.external_event_id, calendar_provider=integration.provider
            ).first()

            if meeting is None:
                meeting = Meeting(
                    external_calendar_event_id=event.external_event_id,
                    calendar_provider=integration.provider,
                    title=event.title,
                    description=clean_description(event.description),
                    start_datetime=event.start_datetime,
                    end_datetime=event.end_datetime,
                    location=event.location,
                    organizer=event.organizer,
                    attendees_text=event.attendees_text,
                    attendees_emails=event.attendees_emails,
                    meeting_link=event.meeting_link,
                    status=MeetingStatus.SCHEDULED,
                    created_by=triggered_by,
                )
                db.session.add(meeting)
                created += 1
            else:
                # Never touch status, confidentiality, or anything EA has
                # already set in-app — only refresh what the calendar owns.
                meeting.title = event.title
                meeting.description = clean_description(event.description)
                meeting.start_datetime = event.start_datetime
                meeting.end_datetime = event.end_datetime
                meeting.location = event.location
                meeting.organizer = event.organizer
                meeting.attendees_text = event.attendees_text
                meeting.attendees_emails = event.attendees_emails
                meeting.meeting_link = event.meeting_link
                meeting.updated_by = triggered_by
                updated += 1

        integration.last_synced_at = utcnow()
        log = CalendarSyncLog(
            integration_id=integration.id,
            triggered_by=triggered_by,
            synced_at=utcnow(),
            events_fetched=len(events),
            events_created=created,
            events_updated=updated,
            success=True,
        )
        db.session.add(log)
        ActivityLog.record(
            entity_type="CALENDAR",
            entity_id=integration.owner_username,
            action_type="CALENDAR_SYNCED",
            performed_by=triggered_by,
            new_value=f"{created} new, {updated} updated",
        )
        db.session.commit()
        return log

    except (CalendarAuthError, CalendarSyncError) as exc:
        db.session.rollback()
        log = CalendarSyncLog(
            integration_id=integration.id,
            triggered_by=triggered_by,
            synced_at=utcnow(),
            success=False,
            error_message=str(exc),
        )
        db.session.add(log)
        db.session.commit()
        raise
