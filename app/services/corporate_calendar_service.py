"""Corporate Calendar (master flow: an employee — usually one nominated per
division — posts events on their own calendar, filling in the same fields
as the company's paper "Corporate Calendar Event Submission Form"; EA and
MD get a read-only, color-by-division aggregate of every division's
calendar; every other employee only ever sees and manages their own
events).

See app/corporate_calendar/routes.py for the three surfaces this powers:
``index``/``events_json`` (EA/MD aggregate, read-only) and
``mine``/``my_events_json`` (an employee's own calendar, full CRUD). See
app/models/corporate_event.py for why division/color are derived live from
EmployeeRepository rather than stored on the row, and for the field-by-
field mapping to the source form's Sections 2-5.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.extensions import db
from app.models.corporate_event import (
    ATTACHMENT_TYPE_LABELS,
    PRIORITY_BADGE_COLOR,
    PRIORITY_LABELS,
    ConflictStatus,
    CorporateEvent,
    EventType,
    MDInvolvement,
    PriorityLevel,
    division_color_hex,
    division_color_name,
)


class CorporateEventValidationError(Exception):
    pass


class CorporateEventPermissionError(Exception):
    pass


def _apply_fields(
    event: CorporateEvent, *,
    title: str, event_type: EventType, event_type_other: str | None,
    start_date: date, end_date: date, location: str | None, organizer: str | None, description: str | None,
    priority_level: PriorityLevel, md_presence_required: MDInvolvement,
    involves_external_stakeholder: bool, has_critical_impact: bool,
    key_participants: str | None, estimated_attendees: int | None,
    pre_event_actions: str | None, documents_to_prepare: str | None,
    budget_involved: bool, budget_amount: Decimal | None, preparation_deadline: date | None,
    conflicts_with_events: ConflictStatus | None, conflict_notes: str | None,
    attachment_types: list[str] | None, attachment_notes: str | None,
) -> None:
    title = (title or "").strip()
    if not title:
        raise CorporateEventValidationError("Event Title is required.")
    if len(title) > 300:
        raise CorporateEventValidationError("Event Title is too long (max 300 characters).")
    if end_date < start_date:
        raise CorporateEventValidationError("Event End Date can't be before Event Start Date.")
    if event_type == EventType.OTHER and not (event_type_other or "").strip():
        raise CorporateEventValidationError("Please specify the event type.")

    event.title = title
    event.event_type = event_type
    event.event_type_other = (event_type_other or "").strip() or None
    event.start_date = start_date
    event.end_date = end_date
    event.location = (location or "").strip() or None
    event.organizer = (organizer or "").strip() or None
    event.description = (description or "").strip() or None

    event.priority_level = priority_level
    event.md_presence_required = md_presence_required
    event.involves_external_stakeholder = involves_external_stakeholder
    event.has_critical_impact = has_critical_impact
    event.key_participants = (key_participants or "").strip() or None
    event.estimated_attendees = estimated_attendees

    event.pre_event_actions = (pre_event_actions or "").strip() or None
    event.documents_to_prepare = (documents_to_prepare or "").strip() or None
    event.budget_involved = budget_involved
    event.budget_amount = budget_amount if budget_involved else None
    event.preparation_deadline = preparation_deadline
    event.conflicts_with_events = conflicts_with_events
    event.conflict_notes = (conflict_notes or "").strip() or None

    event.attachment_types = ",".join(attachment_types) if attachment_types else None
    event.attachment_notes = (attachment_notes or "").strip() or None


def create_event(*, created_by: str, **fields) -> CorporateEvent:
    event = CorporateEvent(created_by=created_by)
    _apply_fields(event, **fields)
    db.session.add(event)
    db.session.commit()
    return event


def _get_owned_or_error(event_id: int, *, requested_by: str) -> CorporateEvent:
    event = db.session.get(CorporateEvent, event_id)
    if event is None:
        raise CorporateEventValidationError("That event no longer exists.")
    if event.created_by != requested_by:
        raise CorporateEventPermissionError("You can only manage events you created.")
    return event


def get_owned_event(event_id: int, *, requested_by: str) -> CorporateEvent:
    """Public wrapper for the route layer's GET /edit — same ownership
    check as every mutation below, since viewing the edit form leaks the
    same data a PUT would.
    """
    return _get_owned_or_error(event_id, requested_by=requested_by)


def update_event(event_id: int, *, requested_by: str, **fields) -> CorporateEvent:
    event = _get_owned_or_error(event_id, requested_by=requested_by)
    _apply_fields(event, **fields)
    db.session.commit()
    return event


def reschedule_event(event_id: int, *, requested_by: str, start_date: date, end_date: date) -> CorporateEvent:
    """Drag/resize on the calendar grid — touches only the date span, not
    the other fields, so it can't be blocked by unrelated validation.
    """
    event = _get_owned_or_error(event_id, requested_by=requested_by)
    if end_date < start_date:
        raise CorporateEventValidationError("End date can't be before start date.")
    event.start_date = start_date
    event.end_date = end_date
    db.session.commit()
    return event


def delete_event(event_id: int, *, requested_by: str) -> None:
    event = _get_owned_or_error(event_id, requested_by=requested_by)
    db.session.delete(event)
    db.session.commit()


def _parse_range_bound(value: str | None) -> date | None:
    """FullCalendar appends ?start=&end= (the visible grid range, as an ISO
    date or datetime) to every events fetch — only that window needs to
    come back rather than every row ever created. The first 10 characters
    are always the YYYY-MM-DD date, whether or not a time/offset follows.
    """
    if not value:
        return None
    return date.fromisoformat(value[:10])


def _in_range(query, *, start: str | None, end: str | None):
    start_bound = _parse_range_bound(start)
    end_bound = _parse_range_bound(end)
    if start_bound:
        query = query.filter(CorporateEvent.end_date >= start_bound)
    if end_bound:
        query = query.filter(CorporateEvent.start_date <= end_bound)
    return query


def list_mine(username: str, *, start: str | None = None, end: str | None = None) -> list[CorporateEvent]:
    query = CorporateEvent.query.filter_by(created_by=username)
    return _in_range(query, start=start, end=end).order_by(CorporateEvent.start_date).all()


def list_all(*, start: str | None = None, end: str | None = None) -> list[CorporateEvent]:
    return _in_range(CorporateEvent.query, start=start, end=end).order_by(CorporateEvent.start_date).all()


def divisions_legend(employee_repository) -> list[dict]:
    """One row per division that has ever posted an event, for the color
    legend on the EA/MD aggregate view — resolved live via
    EmployeeRepository, never guessed from the event rows themselves.
    """
    creator_codes = [c for (c,) in db.session.query(CorporateEvent.created_by).distinct().all()]
    if not creator_codes:
        return []
    records = employee_repository.get_by_codes(creator_codes)

    divisions: set[str] = set()
    for code in creator_codes:
        record = records.get(code)
        divisions.add((record.department if record else None) or "Unassigned")

    return [{"division": d, "color": division_color_hex(d)} for d in sorted(divisions)]


def find_conflicts(events: list[CorporateEvent]) -> dict[int, list[CorporateEvent]]:
    """event.id -> every other event in `events` whose date span overlaps
    it (inclusive on both ends, since these are date-only events — see
    app/models/corporate_event.py's module docstring), for the EA/MD
    aggregate table's automatic conflict alert.

    Deliberately independent of each event's own Section 4 "Conflicts with
    known company events?" answer (event.conflicts_with_events) — that's
    self-reported at submission time by whoever filled the form, filed
    before the other event necessarily even existed yet, so it can't be
    relied on to catch every real clash the way comparing actual date
    spans can.
    """
    conflicts: dict[int, list[CorporateEvent]] = {e.id: [] for e in events}
    for i, a in enumerate(events):
        for b in events[i + 1:]:
            if a.start_date <= b.end_date and b.start_date <= a.end_date:
                conflicts[a.id].append(b)
                conflicts[b.id].append(a)
    return conflicts


def creator_info(events: list[CorporateEvent], employee_repository) -> dict[str, dict]:
    """created_by -> {"name", "division", "color"} for every distinct
    creator among `events`, resolved live via EmployeeRepository in one
    batched call — for the EA/MD aggregate view's detailed table (see
    app/corporate_calendar/routes.py's index()), which needs all three per
    row rather than just division-only like divisions_legend above.
    """
    codes = sorted({e.created_by for e in events})
    if not codes:
        return {}
    records = employee_repository.get_by_codes(codes)
    result = {}
    for code in codes:
        division = (records[code].department if code in records else None) or "Unassigned"
        result[code] = {
            "name": records[code].name if code in records else code,
            "division": division,
            "color": division_color_hex(division),
            "colorName": division_color_name(division),
        }
    return result


def event_to_calendar_dict(event: CorporateEvent, *, employee_repository=None, include_division: bool = False) -> dict:
    """FullCalendar event-object shape — all-day, since these events are
    date-only (see app/models/corporate_event.py's module docstring).
    FullCalendar's all-day `end` is exclusive, so a same-day event's end is
    the day *after* end_date and a multi-day one spans through end_date.

    Colored via `classNames` (a "tag-<name>" from the shared 8-color
    palette in app/static/css/app.css) rather than an inline hex, so the
    calendar grid's pill events, the read-only detail modal's badges, and
    the "today's agenda" sidebar's accent border can all share one CSS
    rule per color instead of each computing their own style.

    `include_division` (EA/MD aggregate view only) resolves the creator's
    live name/department and colors by division; an employee's own
    calendar (`include_division=False`) has nothing to disambiguate
    divisions with, so it colors by priority instead (reusing
    PRIORITY_BADGE_COLOR, the same mapping the priority badge elsewhere
    uses) — still meaningfully varied rather than one flat color.
    """
    props = {
        "eventType": event.event_type.value,
        "eventTypeLabel": event.event_type_label,
        "priorityLevel": event.priority_level.value,
        "priorityLabel": PRIORITY_LABELS[event.priority_level],
        "priorityColor": PRIORITY_BADGE_COLOR[event.priority_level],
        "mdEligible": event.is_md_eligible,
        "location": event.location or "",
        "organizer": event.organizer or "",
        "description": event.description or "",
        "keyParticipants": event.key_participants or "",
        "estimatedAttendees": event.estimated_attendees,
        "attachmentTypes": [ATTACHMENT_TYPE_LABELS.get(t, t) for t in event.attachment_type_list],
    }
    if include_division:
        record = employee_repository.get_by_code(event.created_by) if employee_repository else None
        division = (record.department if record else None) or "Unassigned"
        props["division"] = division
        props["createdByName"] = record.name if record else event.created_by
        color_name = division_color_name(division)
    else:
        color_name = PRIORITY_BADGE_COLOR[event.priority_level]
    props["colorName"] = color_name

    return {
        "id": event.id,
        "title": event.title,
        "start": event.start_date.isoformat(),
        "end": (event.end_date + timedelta(days=1)).isoformat(),
        "allDay": True,
        "classNames": ["fc-event-pill", "tag-" + color_name],
        "extendedProps": props,
    }


__all__ = [
    "CorporateEventValidationError",
    "CorporateEventPermissionError",
    "create_event",
    "update_event",
    "get_owned_event",
    "reschedule_event",
    "delete_event",
    "list_mine",
    "list_all",
    "divisions_legend",
    "creator_info",
    "find_conflicts",
    "event_to_calendar_dict",
]
