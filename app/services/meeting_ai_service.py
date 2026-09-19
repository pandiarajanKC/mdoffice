"""AI orchestration for a meeting: transcript, summary, and suggested
actions (master spec sections 26-29). Every call is timed and logged to
AIProcessingLog (section 57); AI output is always saved as a draft the EA
can edit, and extracted actions are never auto-saved as real tasks
(section 56) — the route layer always routes them through an explicit
review-and-select step first.
"""
from __future__ import annotations

import time

from app.extensions import db
from app.models.activity_log import ActivityLog
from app.models.ai_processing_log import AIProcessingLog
from app.models.attachment import Attachment
from app.models.meeting import Meeting
from app.models.meeting_ai import MeetingFollowupEmail, MeetingSummary, MeetingTranscript
from app.models.mixins import utcnow
from app.services import attachment_service, meeting_service
from app.services.ai import factory
from app.services.ai.base_ai_service import AIServiceError, MeetingNotesDraft, SuggestedAction
from app.services.ai.base_transcription_service import TranscriptionError


class MeetingAIError(Exception):
    pass


def _log(*, entity_id: str, operation: str, provider: str, model: str | None, input_length: int, started: float,
         success: bool, error_message: str | None, requested_by: str) -> None:
    db.session.add(
        AIProcessingLog(
            entity_type="MEETING",
            entity_id=entity_id,
            operation=operation,
            provider=provider,
            model=model,
            input_length=input_length,
            processing_time_ms=int((time.monotonic() - started) * 1000),
            success=success,
            error_message=error_message,
            requested_by=requested_by,
        )
    )
    db.session.commit()


def generate_transcript(meeting: Meeting, *, attachment: Attachment, requested_by: str) -> MeetingTranscript:
    service = factory.get_transcription_service()
    started = time.monotonic()
    audio_path = attachment_service.attachment_path(attachment)

    try:
        text = service.transcribe(audio_path)
    except TranscriptionError as exc:
        _log(
            entity_id=str(meeting.id), operation="TRANSCRIBE", provider=service.provider_name, model=service.model_name,
            input_length=attachment.file_size, started=started, success=False, error_message=str(exc), requested_by=requested_by,
        )
        raise MeetingAIError(str(exc)) from exc

    _log(
        entity_id=str(meeting.id), operation="TRANSCRIBE", provider=service.provider_name, model=service.model_name,
        input_length=attachment.file_size, started=started, success=True, error_message=None, requested_by=requested_by,
    )

    transcript = meeting.transcript
    if transcript is None:
        transcript = MeetingTranscript(meeting_id=meeting.id, transcript_text=text, provider=service.provider_name)
        db.session.add(transcript)
    else:
        transcript.transcript_text = text
        transcript.provider = service.provider_name
        transcript.generated_at = utcnow()
        transcript.edited_by = None
        transcript.edited_at = None

    ActivityLog.record(entity_type="MEETING", entity_id=str(meeting.id), action_type="TRANSCRIPT_GENERATED", performed_by=requested_by)
    db.session.commit()
    return transcript


def save_transcript_edit(meeting: Meeting, *, text: str, edited_by: str) -> MeetingTranscript:
    transcript = meeting.transcript
    if transcript is None:
        raise MeetingAIError("No transcript exists yet for this meeting.")
    transcript.transcript_text = text
    transcript.edited_by = edited_by
    transcript.edited_at = utcnow()
    ActivityLog.record(entity_type="MEETING", entity_id=str(meeting.id), action_type="TRANSCRIPT_EDITED", performed_by=edited_by)
    db.session.commit()
    return transcript


def generate_summary(meeting: Meeting, *, source_text: str, requested_by: str) -> MeetingSummary:
    service = factory.get_ai_service()
    started = time.monotonic()

    try:
        text = service.summarize(source_text)
    except AIServiceError as exc:
        _log(
            entity_id=str(meeting.id), operation="SUMMARIZE", provider=service.provider_name, model=service.model_name,
            input_length=len(source_text), started=started, success=False, error_message=str(exc), requested_by=requested_by,
        )
        raise MeetingAIError(str(exc)) from exc

    _log(
        entity_id=str(meeting.id), operation="SUMMARIZE", provider=service.provider_name, model=service.model_name,
        input_length=len(source_text), started=started, success=True, error_message=None, requested_by=requested_by,
    )

    summary = meeting.summary
    if summary is None:
        summary = MeetingSummary(meeting_id=meeting.id, summary_text=text, provider=service.provider_name)
        db.session.add(summary)
    else:
        summary.summary_text = text
        summary.provider = service.provider_name
        summary.generated_at = utcnow()
        summary.edited_by = None
        summary.edited_at = None

    ActivityLog.record(entity_type="MEETING", entity_id=str(meeting.id), action_type="SUMMARY_GENERATED", performed_by=requested_by)
    db.session.commit()
    return summary


def save_summary_edit(meeting: Meeting, *, text: str, edited_by: str) -> MeetingSummary:
    summary = meeting.summary
    if summary is None:
        raise MeetingAIError("No summary exists yet for this meeting.")
    summary.summary_text = text
    summary.edited_by = edited_by
    summary.edited_at = utcnow()
    ActivityLog.record(entity_type="MEETING", entity_id=str(meeting.id), action_type="SUMMARY_EDITED", performed_by=edited_by)
    db.session.commit()
    return summary


def extract_suggested_actions(meeting: Meeting, *, source_text: str, requested_by: str) -> list[SuggestedAction]:
    """Returns suggestions only — never persisted as Task rows here. The
    route always renders these back to the EA for review/edit/selection.
    """
    service = factory.get_ai_service()
    started = time.monotonic()

    try:
        actions = service.extract_actions(source_text)
    except AIServiceError as exc:
        _log(
            entity_id=str(meeting.id), operation="EXTRACT_ACTIONS", provider=service.provider_name, model=service.model_name,
            input_length=len(source_text), started=started, success=False, error_message=str(exc), requested_by=requested_by,
        )
        raise MeetingAIError(str(exc)) from exc

    _log(
        entity_id=str(meeting.id), operation="EXTRACT_ACTIONS", provider=service.provider_name, model=service.model_name,
        input_length=len(source_text), started=started, success=True, error_message=None, requested_by=requested_by,
    )
    ActivityLog.record(
        entity_type="MEETING", entity_id=str(meeting.id), action_type="ACTIONS_EXTRACTED", performed_by=requested_by,
        new_value=f"{len(actions)} suggested",
    )
    db.session.commit()
    return actions


def _series_email_candidates(meeting: Meeting) -> str | None:
    """Every plausible email address for this meeting's "To" field: its
    own attendees, plus everyone who's ever shown up in a same-titled
    prior occurrence (see meeting_service.meeting_history — the same data
    that powers the History page's "Everyone who's been in this series").

    That series list is a mix of real emails and plain display names —
    whatever Google Calendar happened to hand back for each attendee — so
    this only keeps entries that actually look like an email address
    (contain "@"); a name like "Alice" isn't something a mailto: link can
    use anyway. Broader than relying on this one occurrence's own
    attendees_emails alone, which is often thin (a just-created meeting,
    or one last synced before that field existed).
    """
    emails: list[str] = []
    seen: set[str] = set()

    def add(raw: str | None) -> None:
        for part in (raw or "").split(","):
            candidate = part.strip()
            if candidate and "@" in candidate and candidate.lower() not in seen:
                seen.add(candidate.lower())
                emails.append(candidate)

    add(meeting.attendees_emails)
    for name in meeting_service.meeting_history(meeting)["participants"]:
        add(name)
    return ", ".join(emails) or None


def draft_followup_email(meeting: Meeting, *, source_text: str, requested_by: str) -> MeetingFollowupEmail:
    service = factory.get_ai_service()
    started = time.monotonic()

    try:
        draft = service.draft_followup_email(source_text)
    except AIServiceError as exc:
        _log(
            entity_id=str(meeting.id), operation="DRAFT_FOLLOWUP_EMAIL", provider=service.provider_name, model=service.model_name,
            input_length=len(source_text), started=started, success=False, error_message=str(exc), requested_by=requested_by,
        )
        raise MeetingAIError(str(exc)) from exc

    _log(
        entity_id=str(meeting.id), operation="DRAFT_FOLLOWUP_EMAIL", provider=service.provider_name, model=service.model_name,
        input_length=len(source_text), started=started, success=True, error_message=None, requested_by=requested_by,
    )

    email = meeting.followup_email
    if email is None:
        email = MeetingFollowupEmail(
            meeting_id=meeting.id, subject=draft.subject, body_text=draft.body, provider=service.provider_name,
            to_emails=_series_email_candidates(meeting),  # pre-filled, broadened across the series; editable after
        )
        db.session.add(email)
    else:
        # Regenerating only touches subject/body — leave "To" as the EA
        # already set it, rather than clobbering an edit they made. But if
        # it's still empty (a draft made before this field existed, or
        # before the meeting/series had any known attendee emails at
        # draft time), backfill it now instead of leaving it blank
        # forever — regenerating is the one moment an EA would notice and
        # fix a stale draft anyway.
        if not email.to_emails:
            email.to_emails = _series_email_candidates(meeting)
        email.subject = draft.subject
        email.body_text = draft.body
        email.provider = service.provider_name
        email.generated_at = utcnow()
        email.edited_by = None
        email.edited_at = None

    ActivityLog.record(entity_type="MEETING", entity_id=str(meeting.id), action_type="FOLLOWUP_EMAIL_DRAFTED", performed_by=requested_by)
    db.session.commit()
    return email


def save_followup_email_edit(
    meeting: Meeting, *, subject: str, body: str, to_emails: str | None = None, edited_by: str
) -> MeetingFollowupEmail:
    email = meeting.followup_email
    if email is None:
        raise MeetingAIError("No follow-up email draft exists yet for this meeting.")
    email.subject = subject
    email.body_text = body
    if to_emails is not None:
        email.to_emails = to_emails.strip() or None
    email.edited_by = edited_by
    email.edited_at = utcnow()
    ActivityLog.record(entity_type="MEETING", entity_id=str(meeting.id), action_type="FOLLOWUP_EMAIL_EDITED", performed_by=edited_by)
    db.session.commit()
    return email


def split_notes(meeting: Meeting, *, source_text: str, requested_by: str) -> MeetingNotesDraft:
    """Split one freeform block of live meeting notes into Notes /
    Decisions / Action Items. Returns a draft only — nothing is persisted
    here, matching extract_suggested_actions: the route always renders
    this back to the EA for review/edit before any MeetingNote,
    MeetingDecision or Task row is actually created.
    """
    service = factory.get_ai_service()
    started = time.monotonic()

    try:
        draft = service.split_meeting_notes(source_text)
    except AIServiceError as exc:
        _log(
            entity_id=str(meeting.id), operation="SPLIT_NOTES", provider=service.provider_name, model=service.model_name,
            input_length=len(source_text), started=started, success=False, error_message=str(exc), requested_by=requested_by,
        )
        raise MeetingAIError(str(exc)) from exc

    _log(
        entity_id=str(meeting.id), operation="SPLIT_NOTES", provider=service.provider_name, model=service.model_name,
        input_length=len(source_text), started=started, success=True, error_message=None, requested_by=requested_by,
    )
    ActivityLog.record(
        entity_type="MEETING", entity_id=str(meeting.id), action_type="NOTES_SPLIT", performed_by=requested_by,
        new_value=f"{len(draft.notes)} notes, {len(draft.decisions)} decisions, {len(draft.actions)} actions suggested",
    )
    db.session.commit()
    return draft
