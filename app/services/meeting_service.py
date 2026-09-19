"""Meeting workspace business logic: create, start, complete, notes, decisions.

Kept deliberately fast/low-friction per the master spec's UX priority — the EA
should be able to run an entire meeting (start -> notes -> decisions ->
actions -> complete) without leaving the workspace page.
"""
from __future__ import annotations

from datetime import date, datetime

from app.extensions import db
from app.models.activity_log import ActivityLog
from app.models.meeting import ConfidentialityLevel, Meeting, MeetingDecision, MeetingNote, MeetingStatus
from app.models.meeting_template import MeetingTemplate
from app.models.mixins import utcnow
from app.models.task import OPEN_STATUSES, TaskStatus, TaskType
from app.utils.meeting_display import parse_attendees


class MeetingWorkflowError(Exception):
    """Raised when an action is attempted in an invalid meeting state."""


class MeetingTemplateError(Exception):
    """Raised for template name conflicts or other template validation issues."""


def create_meeting(
    *,
    title: str,
    start_datetime: datetime,
    end_datetime: datetime,
    created_by: str,
    description: str | None = None,
    location: str | None = None,
    meeting_link: str | None = None,
    organizer: str | None = None,
    meeting_type: str | None = None,
    attendees_emails: str | None = None,
    confidentiality_level: ConfidentialityLevel = ConfidentialityLevel.NORMAL,
) -> Meeting:
    meeting = Meeting(
        title=title.strip(),
        description=(description or "").strip() or None,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        location=(location or "").strip() or None,
        meeting_link=(meeting_link or "").strip() or None,
        organizer=(organizer or "").strip() or None,
        meeting_type=(meeting_type or "").strip() or None,
        attendees_emails=(attendees_emails or "").strip() or None,
        confidentiality_level=confidentiality_level,
        status=MeetingStatus.SCHEDULED,
        created_by=created_by,
    )
    db.session.add(meeting)
    db.session.flush()
    ActivityLog.record(
        entity_type="MEETING", entity_id=str(meeting.id), action_type="MEETING_CREATED", performed_by=created_by
    )
    db.session.commit()
    return meeting


def start_meeting(meeting: Meeting, *, performed_by: str) -> Meeting:
    if meeting.status not in (MeetingStatus.SCHEDULED, MeetingStatus.ON_HOLD):
        raise MeetingWorkflowError(f"Cannot start a meeting that is {meeting.status.value}.")
    meeting.status = MeetingStatus.IN_PROGRESS
    meeting.started_at = utcnow()
    meeting.updated_by = performed_by
    ActivityLog.record(
        entity_type="MEETING", entity_id=str(meeting.id), action_type="MEETING_STARTED", performed_by=performed_by
    )
    db.session.commit()
    return meeting


def complete_meeting(meeting: Meeting, *, performed_by: str) -> Meeting:
    if meeting.status != MeetingStatus.IN_PROGRESS:
        raise MeetingWorkflowError("Only a meeting that is In Progress can be completed.")
    meeting.status = MeetingStatus.COMPLETED
    meeting.completed_at = utcnow()
    meeting.updated_by = performed_by
    ActivityLog.record(
        entity_type="MEETING", entity_id=str(meeting.id), action_type="MEETING_COMPLETED", performed_by=performed_by
    )
    db.session.commit()
    return meeting


def reopen_meeting(meeting: Meeting, *, performed_by: str) -> Meeting:
    if meeting.status != MeetingStatus.COMPLETED:
        raise MeetingWorkflowError("Only a completed meeting can be reopened.")
    meeting.status = MeetingStatus.IN_PROGRESS
    meeting.completed_at = None
    meeting.updated_by = performed_by
    ActivityLog.record(
        entity_type="MEETING", entity_id=str(meeting.id), action_type="MEETING_REOPENED", performed_by=performed_by
    )
    db.session.commit()
    return meeting


def completion_checklist(meeting: Meeting) -> dict:
    """Advisory checklist shown before completing a meeting (spec section 61).

    Purely informational — completion is never blocked by it.
    """
    tasks = meeting.tasks
    return {
        "notes_captured": len(meeting.notes) > 0,
        "decisions_captured": len(meeting.decisions) > 0,
        "actions_assigned": len(tasks) > 0,
        "due_dates_added": bool(tasks) and all(t.due_date is not None for t in tasks),
    }


def add_note(meeting: Meeting, *, note_text: str, created_by: str) -> MeetingNote:
    next_sequence = (max((n.sequence for n in meeting.notes), default=0)) + 1
    note = MeetingNote(meeting_id=meeting.id, note_text=note_text.strip(), sequence=next_sequence, created_by=created_by)
    db.session.add(note)
    ActivityLog.record(
        entity_type="MEETING", entity_id=str(meeting.id), action_type="NOTE_ADDED", performed_by=created_by
    )
    db.session.commit()
    return note


def delete_note(note: MeetingNote, *, performed_by: str) -> None:
    meeting_id = note.meeting_id
    db.session.delete(note)
    ActivityLog.record(
        entity_type="MEETING", entity_id=str(meeting_id), action_type="NOTE_DELETED", performed_by=performed_by
    )
    db.session.commit()


def add_decision(meeting: Meeting, *, decision_text: str, created_by: str, decision_date: date | None = None) -> MeetingDecision:
    decision = MeetingDecision(
        meeting_id=meeting.id,
        decision_text=decision_text.strip(),
        decision_date=decision_date or utcnow(),
        created_by=created_by,
    )
    db.session.add(decision)
    ActivityLog.record(
        entity_type="MEETING", entity_id=str(meeting.id), action_type="DECISION_ADDED", performed_by=created_by
    )
    db.session.commit()
    return decision


def convert_note_to_decision(note: MeetingNote, *, performed_by: str) -> MeetingDecision:
    """Reclassify a captured note as a decision instead — for when the EA
    (or the AI splitter) filed something as a note that turns out to have
    actually been decided. The note itself is removed once converted.
    """
    decision = add_decision(note.meeting, decision_text=note.note_text, created_by=performed_by)
    delete_note(note, performed_by=performed_by)
    return decision


def followup_status(meeting: Meeting) -> str:
    """Calculated follow-up indicator shown on the meeting list (spec section 62)."""
    tasks = [t for t in meeting.tasks if t.task_type == TaskType.MEETING_ACTION]
    if not tasks:
        return "No Actions"
    if any(t.is_overdue for t in tasks):
        return "Actions Overdue"
    if all(t.status.value == "COMPLETED" for t in tasks):
        return "All Actions Completed"
    if any(t.status in OPEN_STATUSES for t in tasks):
        return "Actions In Progress" if any(t.status.value == "IN_PROGRESS" for t in tasks) else "Actions Pending"
    return "Actions Pending"


def prep_brief(meeting: Meeting) -> dict | None:
    """Carry-over context from the last related meeting, shown before this
    one happens so the EA/MD don't have to go dig it up themselves.

    "Related" means the most recent completed meeting before this one that
    either shares the exact same title (the common signal for a recurring
    series, since there's no formal recurrence concept yet) or shares at
    least one tagged project. Returns None if this meeting is already
    completed/cancelled, or no related prior meeting exists.
    """
    if meeting.status not in (MeetingStatus.SCHEDULED, MeetingStatus.IN_PROGRESS):
        return None

    tagged_project_ids = {link.project_id for link in meeting.project_links}

    candidates = (
        Meeting.query.filter(
            Meeting.id != meeting.id,
            Meeting.status == MeetingStatus.COMPLETED,
            Meeting.start_datetime < meeting.start_datetime,
        )
        .order_by(Meeting.start_datetime.desc())
        .limit(200)
        .all()
    )

    previous_meeting = None
    for candidate in candidates:
        candidate_project_ids = {link.project_id for link in candidate.project_links}
        if candidate.title == meeting.title or (tagged_project_ids and tagged_project_ids & candidate_project_ids):
            previous_meeting = candidate
            break  # candidates are already ordered most-recent-first

    if previous_meeting is None:
        return None

    open_actions = [
        t for t in previous_meeting.tasks
        if t.task_type == TaskType.MEETING_ACTION and t.status in OPEN_STATUSES
    ]

    return {
        "previous_meeting": previous_meeting,
        "open_actions": open_actions,
        "decisions": previous_meeting.decisions,
    }


def meeting_history(meeting: Meeting) -> dict:
    """Every meeting sharing this one's exact title (case-insensitive),
    most recent first, plus everyone who's ever attended one of them.

    There's no formal recurrence concept yet (see prep_brief above), so
    "same title" is the whole series-matching signal — good enough for an
    EA who names a recurring meeting consistently, per the app's existing
    convention. attendees_text/organizer are free text from calendar sync
    or manual entry, so this is a best-effort text aggregation, not a
    resolved list of employee/contact records.
    """
    normalized_title = meeting.title.strip().lower()
    series = (
        Meeting.query.filter(db.func.lower(Meeting.title) == normalized_title)
        .order_by(Meeting.start_datetime.desc())
        .all()
    )

    participants: set[str] = set()
    for m in series:
        if m.organizer:
            participants.add(m.organizer.strip())
        participants.update(parse_attendees(m.attendees_text))

    return {"series": series, "participants": sorted(participants, key=str.lower)}


def effectiveness_score(meeting: Meeting) -> dict | None:
    """A 0-100 proxy for how effective a completed meeting was, built
    entirely from data this app already has. Never a substitute for asking
    the people who were in the room — it can't see whether the discussion
    itself was good — but a consistent signal for spotting meetings that
    produce nothing lasting versus ones whose outcomes actually stick.

    Five weighted components:
    - Follow-through (35%): of the action items this meeting created, how
      many actually got completed? The strongest signal of all — decisions
      that never get executed didn't accomplish much regardless of how the
      discussion felt.
    - Output recorded (20%): decisions + notes logged, relative to how
      long the meeting ran (benchmark: one item per 15 minutes).
    - Cost efficiency (20%): everything produced, relative to attendee-
      hours spent (benchmark: one item per attendee-hour) — a large, long
      meeting needs more to show for it than a small, short one.
    - Timeliness (15%): started close to schedule, and a follow-up email
      was drafted promptly afterward.
    - Recurrence trend (10%, only when a same-titled predecessor exists):
      did that predecessor's action items get resolved, or is the backlog
      just piling up meeting after meeting? Omitted (and its weight
      redistributed) for a one-off meeting with no prior occurrence.

    Returns None for a meeting that isn't COMPLETED yet.
    """
    if meeting.status != MeetingStatus.COMPLETED:
        return None

    action_tasks = [t for t in meeting.tasks if t.task_type == TaskType.MEETING_ACTION]
    decisions_count = len(meeting.decisions)
    notes_count = len(meeting.notes)

    started = meeting.started_at or meeting.start_datetime
    ended = meeting.completed_at or meeting.end_datetime
    duration_minutes = max(1.0, (ended - started).total_seconds() / 60.0)

    attendee_count = len([a for a in (meeting.attendees_text or "").split(",") if a.strip()]) or 1
    attendee_hours = max(0.05, attendee_count * duration_minutes / 60.0)

    components = []

    if action_tasks:
        completed = sum(1 for t in action_tasks if t.status == TaskStatus.COMPLETED)
        follow_through = round(completed / len(action_tasks) * 100)
        detail = f"{completed} of {len(action_tasks)} action item(s) completed"
    elif decisions_count:
        follow_through = 60
        detail = "Decisions were made, but no action items were created from them"
    else:
        follow_through = 30
        detail = "No action items or decisions came out of this meeting"
    components.append({"label": "Follow-through", "weight": 35, "score": follow_through, "detail": detail})

    output_count = decisions_count + notes_count
    expected_output = duration_minutes / 15.0
    density = round(min(100, (output_count / expected_output) * 100)) if expected_output > 0 else 100
    components.append({
        "label": "Output recorded", "weight": 20, "score": density,
        "detail": f"{output_count} decision(s)/note(s) over {round(duration_minutes)} min",
    })

    total_output = output_count + len(action_tasks)
    efficiency = round(min(100, (total_output / attendee_hours) * 100))
    components.append({
        "label": "Cost efficiency", "weight": 20, "score": efficiency,
        "detail": f"{total_output} item(s) produced across ~{attendee_count} attendee(s)",
    })

    start_delay = max(0.0, (meeting.started_at - meeting.start_datetime).total_seconds() / 60.0) if meeting.started_at else 0.0
    start_score = max(0, round(100 - (start_delay / 30.0) * 100))
    followup_email = meeting.followup_email
    if followup_email and meeting.completed_at:
        followup_delay_hours = max(0.0, (followup_email.generated_at - meeting.completed_at).total_seconds() / 3600.0)
        followup_score = max(0, round(100 - (followup_delay_hours / 24.0) * 100))
    else:
        followup_score = 0
    timeliness = round((start_score + followup_score) / 2)
    components.append({
        "label": "Timeliness", "weight": 15, "score": timeliness,
        "detail": f"Started {round(start_delay)} min after schedule; "
                  + ("follow-up email drafted" if followup_email else "no follow-up email drafted yet"),
    })

    older = [
        m for m in meeting_history(meeting)["series"]
        if m.id != meeting.id and m.start_datetime < meeting.start_datetime
    ]
    if older:
        previous_actions = [t for t in older[0].tasks if t.task_type == TaskType.MEETING_ACTION]
        if previous_actions:
            resolved = sum(1 for t in previous_actions if t.status == TaskStatus.COMPLETED)
            recurrence = round(resolved / len(previous_actions) * 100)
            detail = f"{resolved} of {len(previous_actions)} action(s) from the prior occurrence resolved since"
        else:
            recurrence = 100
            detail = "The prior occurrence had no action items to carry forward"
        components.append({"label": "Recurrence trend", "weight": 10, "score": recurrence, "detail": detail})

    total_weight = sum(c["weight"] for c in components)
    for c in components:
        c["points"] = round(c["score"] * c["weight"] / total_weight, 1)

    score = round(sum(c["points"] for c in components))
    tier = "great" if score >= 80 else "okay" if score >= 50 else "poor"

    return {"score": score, "tier": tier, "components": components}


def list_templates() -> list[MeetingTemplate]:
    return MeetingTemplate.query.order_by(MeetingTemplate.name.asc()).all()


def save_as_template(meeting: Meeting, *, name: str, created_by: str) -> MeetingTemplate:
    """Capture an existing meeting's shape (title/type/location/duration/
    confidentiality) as a reusable template — the low-friction way to set
    one up, since an EA always has a real meeting to base it on rather than
    filling out a blank template form from scratch.
    """
    name = name.strip()
    if not name:
        raise MeetingTemplateError("Give the template a name.")
    if MeetingTemplate.query.filter_by(name=name).first():
        raise MeetingTemplateError(f'A template named "{name}" already exists.')

    duration_minutes = max(15, int((meeting.end_datetime - meeting.start_datetime).total_seconds() // 60))
    template = MeetingTemplate(
        name=name,
        title=meeting.title,
        description=meeting.description,
        duration_minutes=duration_minutes,
        location=meeting.location,
        meeting_link=meeting.meeting_link,
        organizer=meeting.organizer,
        meeting_type=meeting.meeting_type,
        confidentiality_level=meeting.confidentiality_level,
        created_by=created_by,
    )
    db.session.add(template)
    db.session.commit()
    return template


def delete_template(template: MeetingTemplate) -> None:
    db.session.delete(template)
    db.session.commit()
