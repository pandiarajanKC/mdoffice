from app.services.calendar.google_calendar_service import GoogleCalendarService


def _service():
    return GoogleCalendarService(client_id="id", client_secret="secret", redirect_uri="https://example.com/callback")


def test_to_calendar_event_extracts_emails_separately_from_display_names():
    item = {
        "id": "evt-1",
        "summary": "Steering Committee",
        "start": {"dateTime": "2026-01-01T10:00:00Z"},
        "end": {"dateTime": "2026-01-01T11:00:00Z"},
        "organizer": {"displayName": "MD Office", "email": "md@example.com"},
        "attendees": [
            {"displayName": "Alice", "email": "alice@example.com"},
            {"displayName": "Bob", "email": "bob@example.com"},
        ],
    }
    event = _service()._to_calendar_event(item)
    assert event.attendees_text == "Alice, Bob"  # display names preferred for reading
    assert event.attendees_emails == "alice@example.com, bob@example.com, md@example.com"  # real addresses for mailto


def test_to_calendar_event_falls_back_to_email_when_no_display_name():
    item = {
        "id": "evt-2",
        "summary": "Vendor Call",
        "start": {"dateTime": "2026-01-01T10:00:00Z"},
        "end": {"dateTime": "2026-01-01T11:00:00Z"},
        "attendees": [{"email": "vendor@example.com"}],
    }
    event = _service()._to_calendar_event(item)
    assert event.attendees_text == "vendor@example.com"
    assert event.attendees_emails == "vendor@example.com"


def test_to_calendar_event_does_not_duplicate_organizer_already_in_attendees():
    item = {
        "id": "evt-3",
        "summary": "Internal Sync",
        "start": {"dateTime": "2026-01-01T10:00:00Z"},
        "end": {"dateTime": "2026-01-01T11:00:00Z"},
        "organizer": {"email": "alice@example.com"},
        "attendees": [{"email": "alice@example.com"}, {"email": "bob@example.com"}],
    }
    event = _service()._to_calendar_event(item)
    assert event.attendees_emails == "alice@example.com, bob@example.com"


def test_to_calendar_event_no_attendees_gives_none():
    item = {
        "id": "evt-4",
        "summary": "Solo Block",
        "start": {"dateTime": "2026-01-01T10:00:00Z"},
        "end": {"dateTime": "2026-01-01T11:00:00Z"},
    }
    event = _service()._to_calendar_event(item)
    assert event.attendees_text is None
    assert event.attendees_emails is None
