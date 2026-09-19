"""Shared parsing for Meeting.attendees_text — free text from calendar sync
or manual entry, comma-separated (a single token may itself be
"Name | Role", which is kept intact rather than split further).

Used everywhere a meeting's participant list is rendered, so the count and
the individual chips always agree with each other and with the aggregated
series-wide list on the Meeting History page (app/services/meeting_service.py's
meeting_history()).
"""
from __future__ import annotations


def parse_attendees(attendees_text: str | None) -> list[str]:
    if not attendees_text:
        return []
    return [name.strip() for name in attendees_text.split(",") if name.strip()]
