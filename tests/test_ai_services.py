import io
from datetime import datetime, timedelta

import pytest
from werkzeug.datastructures import FileStorage

from app.models.ai_processing_log import AIProcessingLog
from app.models.task import TaskPriority, TaskType
from app.services import attachment_service, meeting_ai_service, meeting_service, task_service, text_extraction_service
from app.services.ai.base_ai_service import AIServiceError, BaseAIService, EmailDraft, MeetingNotesDraft, SuggestedAction
from app.services.ai.base_transcription_service import BaseTranscriptionService, TranscriptionError


class FakeAIService(BaseAIService):
    provider_name = "fake"
    model_name = "fake-model"

    def __init__(self, summary="MEETING SUMMARY\n\nKey Discussion Points\n- Talked about things",
                 actions=None, email_draft=None, notes_draft=None, answer="Here's what I found.", raise_error=None):
        self._summary = summary
        self._actions = actions if actions is not None else [SuggestedAction(title="Do the thing", description="context", priority="HIGH")]
        self._email_draft = email_draft or EmailDraft(subject="Minutes of Meeting", body="Decisions:\n- None yet")
        self._notes_draft = notes_draft or MeetingNotesDraft(
            notes=["Discussed the roadmap"], decisions=["Approved the budget"],
            actions=[SuggestedAction(title="Send revised numbers", description=None, priority="MEDIUM")],
        )
        self._answer = answer
        self._raise_error = raise_error

    def summarize(self, text):
        if self._raise_error:
            raise self._raise_error
        return self._summary

    def extract_actions(self, text):
        if self._raise_error:
            raise self._raise_error
        return self._actions

    def draft_followup_email(self, text):
        if self._raise_error:
            raise self._raise_error
        return self._email_draft

    def split_meeting_notes(self, text):
        if self._raise_error:
            raise self._raise_error
        return self._notes_draft

    def answer_question(self, question, context):
        if self._raise_error:
            raise self._raise_error
        return self._answer


class FakeTranscriptionService(BaseTranscriptionService):
    provider_name = "fake"
    model_name = "fake-whisper"

    def __init__(self, text="Speaker 1: Let's discuss the roadmap.", raise_error=None):
        self._text = text
        self._raise_error = raise_error

    def transcribe(self, audio_path, *, language=None, prompt=None):
        if self._raise_error:
            raise self._raise_error
        return self._text


def _meeting(db):
    return meeting_service.create_meeting(
        title="Steering Committee", start_datetime=datetime.now(), end_datetime=datetime.now() + timedelta(hours=1),
        created_by="admin1",
    )


def _fake_attachment(db, meeting):
    from app.models.attachment import Attachment

    a = Attachment(
        entity_type="MEETING", entity_id=str(meeting.id), original_filename="rec.mp3",
        stored_filename="abc123.mp3", mime_type="audio/mpeg", file_size=1024, uploaded_by="admin1",
    )
    db.session.add(a)
    db.session.commit()
    return a


# ---------- transcript ----------

def test_generate_transcript_creates_record(app, db, monkeypatch, tmp_path):
    meeting = _meeting(db)
    attachment = _fake_attachment(db, meeting)
    audio_file = tmp_path / "abc123.mp3"
    audio_file.write_bytes(b"fake audio bytes")

    monkeypatch.setattr("app.services.meeting_ai_service.factory.get_transcription_service", lambda: FakeTranscriptionService())
    monkeypatch.setattr("app.services.meeting_ai_service.attachment_service.attachment_path", lambda a: str(audio_file))

    transcript = meeting_ai_service.generate_transcript(meeting, attachment=attachment, requested_by="admin1")
    assert transcript.transcript_text == "Speaker 1: Let's discuss the roadmap."
    assert transcript.provider == "fake"
    assert meeting.transcript is transcript

    log = AIProcessingLog.query.filter_by(operation="TRANSCRIBE").first()
    assert log.success is True
    assert log.provider == "fake"
    assert log.requested_by == "admin1"


def test_generate_transcript_records_failure(app, db, monkeypatch, tmp_path):
    meeting = _meeting(db)
    attachment = _fake_attachment(db, meeting)
    monkeypatch.setattr(
        "app.services.meeting_ai_service.factory.get_transcription_service",
        lambda: FakeTranscriptionService(raise_error=TranscriptionError("quota exceeded")),
    )
    monkeypatch.setattr("app.services.meeting_ai_service.attachment_service.attachment_path", lambda a: "/nonexistent")

    with pytest.raises(meeting_ai_service.MeetingAIError):
        meeting_ai_service.generate_transcript(meeting, attachment=attachment, requested_by="admin1")

    log = AIProcessingLog.query.filter_by(operation="TRANSCRIBE").first()
    assert log.success is False
    assert "quota" in log.error_message


def test_regenerating_transcript_clears_previous_edit_marker(app, db, monkeypatch, tmp_path):
    meeting = _meeting(db)
    attachment = _fake_attachment(db, meeting)
    monkeypatch.setattr("app.services.meeting_ai_service.factory.get_transcription_service", lambda: FakeTranscriptionService())
    monkeypatch.setattr("app.services.meeting_ai_service.attachment_service.attachment_path", lambda a: "/fake")

    meeting_ai_service.generate_transcript(meeting, attachment=attachment, requested_by="admin1")
    meeting_ai_service.save_transcript_edit(meeting, text="edited text", edited_by="admin1")
    assert meeting.transcript.edited_by == "admin1"

    meeting_ai_service.generate_transcript(meeting, attachment=attachment, requested_by="admin1")
    assert meeting.transcript.edited_by is None  # fresh AI output supersedes the old edit marker


def test_save_transcript_edit_requires_existing_transcript(app, db):
    meeting = _meeting(db)
    with pytest.raises(meeting_ai_service.MeetingAIError):
        meeting_ai_service.save_transcript_edit(meeting, text="x", edited_by="admin1")


# ---------- summary ----------

def test_generate_summary_creates_record(app, db, monkeypatch):
    meeting = _meeting(db)
    monkeypatch.setattr("app.services.meeting_ai_service.factory.get_ai_service", lambda: FakeAIService())

    summary = meeting_ai_service.generate_summary(meeting, source_text="raw notes", requested_by="admin1")
    assert "Key Discussion Points" in summary.summary_text
    assert meeting.summary is summary


def test_save_summary_edit(app, db, monkeypatch):
    meeting = _meeting(db)
    monkeypatch.setattr("app.services.meeting_ai_service.factory.get_ai_service", lambda: FakeAIService())
    meeting_ai_service.generate_summary(meeting, source_text="raw notes", requested_by="admin1")

    meeting_ai_service.save_summary_edit(meeting, text="Human-corrected summary", edited_by="admin1")
    assert meeting.summary.summary_text == "Human-corrected summary"
    assert meeting.summary.edited_by == "admin1"


def test_summarize_failure_logs_and_raises(app, db, monkeypatch):
    meeting = _meeting(db)
    monkeypatch.setattr(
        "app.services.meeting_ai_service.factory.get_ai_service",
        lambda: FakeAIService(raise_error=AIServiceError("rate limited")),
    )
    with pytest.raises(meeting_ai_service.MeetingAIError):
        meeting_ai_service.generate_summary(meeting, source_text="x", requested_by="admin1")
    assert meeting.summary is None
    log = AIProcessingLog.query.filter_by(operation="SUMMARIZE").first()
    assert log.success is False


# ---------- action extraction (never auto-saved) ----------

def test_extract_suggested_actions_does_not_create_tasks(app, db, monkeypatch):
    meeting = _meeting(db)
    monkeypatch.setattr("app.services.meeting_ai_service.factory.get_ai_service", lambda: FakeAIService())

    suggestions = meeting_ai_service.extract_suggested_actions(meeting, source_text="summary text", requested_by="admin1")
    assert len(suggestions) == 1
    assert suggestions[0].title == "Do the thing"
    assert len(meeting.tasks) == 0  # nothing persisted — EA must review and explicitly create


def test_extract_actions_logs_ai_processing(app, db, monkeypatch):
    meeting = _meeting(db)
    monkeypatch.setattr("app.services.meeting_ai_service.factory.get_ai_service", lambda: FakeAIService())
    meeting_ai_service.extract_suggested_actions(meeting, source_text="x", requested_by="admin1")

    log = AIProcessingLog.query.filter_by(operation="EXTRACT_ACTIONS").first()
    assert log is not None
    assert log.entity_type == "MEETING"
    assert log.entity_id == str(meeting.id)


# ---------- follow-up email drafting (always a draft — never auto-sent) ----------

def test_draft_followup_email_creates_record(app, db, monkeypatch):
    meeting = _meeting(db)
    draft = EmailDraft(subject="Minutes of Meeting: Steering Committee", body="Decisions:\n- Approved budget")
    monkeypatch.setattr("app.services.meeting_ai_service.factory.get_ai_service", lambda: FakeAIService(email_draft=draft))

    email = meeting_ai_service.draft_followup_email(meeting, source_text="notes and decisions", requested_by="admin1")
    assert email.subject == "Minutes of Meeting: Steering Committee"
    assert "Approved budget" in email.body_text
    assert meeting.followup_email is email


def test_draft_followup_email_regenerate_overwrites_previous_draft(app, db, monkeypatch):
    meeting = _meeting(db)
    monkeypatch.setattr(
        "app.services.meeting_ai_service.factory.get_ai_service",
        lambda: FakeAIService(email_draft=EmailDraft(subject="First draft", body="v1")),
    )
    meeting_ai_service.draft_followup_email(meeting, source_text="x", requested_by="admin1")

    monkeypatch.setattr(
        "app.services.meeting_ai_service.factory.get_ai_service",
        lambda: FakeAIService(email_draft=EmailDraft(subject="Second draft", body="v2")),
    )
    meeting_ai_service.draft_followup_email(meeting, source_text="x", requested_by="admin1")

    assert meeting.followup_email.subject == "Second draft"
    assert meeting.followup_email.body_text == "v2"
    assert meeting.followup_email.edited_by is None  # regenerating clears any prior human edit marker


def test_save_followup_email_edit(app, db, monkeypatch):
    meeting = _meeting(db)
    monkeypatch.setattr("app.services.meeting_ai_service.factory.get_ai_service", lambda: FakeAIService())
    meeting_ai_service.draft_followup_email(meeting, source_text="x", requested_by="admin1")

    meeting_ai_service.save_followup_email_edit(meeting, subject="Edited subject", body="Edited body", edited_by="admin1")
    assert meeting.followup_email.subject == "Edited subject"
    assert meeting.followup_email.edited_by == "admin1"


def test_draft_followup_email_prefills_to_from_meeting_attendees(app, db, monkeypatch):
    meeting = _meeting(db)
    meeting.attendees_emails = "alice@example.com, bob@example.com"
    monkeypatch.setattr("app.services.meeting_ai_service.factory.get_ai_service", lambda: FakeAIService())

    email = meeting_ai_service.draft_followup_email(meeting, source_text="x", requested_by="admin1")
    assert email.to_emails == "alice@example.com, bob@example.com"


def test_draft_followup_email_to_broadens_across_the_series(app, db, monkeypatch):
    # A real scenario from the History page: a prior same-titled occurrence's
    # attendees_text happens to be real email addresses (no displayName was
    # ever supplied for them), which is a broader/more complete recipient
    # list than this occurrence's own (possibly thin) attendees_emails.
    past = meeting_service.create_meeting(
        title="Steering Committee", start_datetime=datetime.now() - timedelta(days=7),
        end_datetime=datetime.now() - timedelta(days=7, hours=-1), created_by="admin1",
    )
    past.attendees_text = "carol@vendor.com, dave@vendor.com"

    meeting = meeting_service.create_meeting(
        title="Steering Committee", start_datetime=datetime.now(), end_datetime=datetime.now() + timedelta(hours=1),
        created_by="admin1", attendees_emails="alice@example.com",
    )
    monkeypatch.setattr("app.services.meeting_ai_service.factory.get_ai_service", lambda: FakeAIService())

    email = meeting_ai_service.draft_followup_email(meeting, source_text="x", requested_by="admin1")
    assert email.to_emails == "alice@example.com, carol@vendor.com, dave@vendor.com"


def test_draft_followup_email_to_ignores_plain_names_in_series_history(app, db, monkeypatch):
    past = meeting_service.create_meeting(
        title="Steering Committee", start_datetime=datetime.now() - timedelta(days=7),
        end_datetime=datetime.now() - timedelta(days=7, hours=-1), created_by="admin1",
    )
    past.attendees_text = "Alice, Bob"  # display names only — not usable in a mailto: link

    meeting = meeting_service.create_meeting(
        title="Steering Committee", start_datetime=datetime.now(), end_datetime=datetime.now() + timedelta(hours=1),
        created_by="admin1",
    )
    monkeypatch.setattr("app.services.meeting_ai_service.factory.get_ai_service", lambda: FakeAIService())

    email = meeting_ai_service.draft_followup_email(meeting, source_text="x", requested_by="admin1")
    assert email.to_emails is None


def test_save_followup_email_edit_updates_to_emails(app, db, monkeypatch):
    meeting = _meeting(db)
    monkeypatch.setattr("app.services.meeting_ai_service.factory.get_ai_service", lambda: FakeAIService())
    meeting_ai_service.draft_followup_email(meeting, source_text="x", requested_by="admin1")

    meeting_ai_service.save_followup_email_edit(
        meeting, subject="s", body="b", to_emails="new@example.com, other@example.com", edited_by="admin1"
    )
    assert meeting.followup_email.to_emails == "new@example.com, other@example.com"


def test_regenerating_followup_email_does_not_clobber_edited_recipients(app, db, monkeypatch):
    meeting = _meeting(db)
    meeting.attendees_emails = "alice@example.com"
    monkeypatch.setattr("app.services.meeting_ai_service.factory.get_ai_service", lambda: FakeAIService())
    meeting_ai_service.draft_followup_email(meeting, source_text="x", requested_by="admin1")
    meeting_ai_service.save_followup_email_edit(meeting, subject="s", body="b", to_emails="curated@example.com", edited_by="admin1")

    meeting_ai_service.draft_followup_email(meeting, source_text="x", requested_by="admin1")  # regenerate
    assert meeting.followup_email.to_emails == "curated@example.com"


def test_regenerating_followup_email_backfills_empty_to_field(app, db, monkeypatch):
    # A real bug: a draft made before attendee emails were known (or
    # before this field existed at all) had to_emails permanently stuck
    # empty forever, since regenerating deliberately never touched it —
    # meant to protect an EA's edit, but that guard shouldn't also protect
    # emptiness. Regenerating should backfill it once real data exists.
    meeting = _meeting(db)
    monkeypatch.setattr("app.services.meeting_ai_service.factory.get_ai_service", lambda: FakeAIService())
    meeting_ai_service.draft_followup_email(meeting, source_text="x", requested_by="admin1")
    assert meeting.followup_email.to_emails is None  # nothing known yet at first draft

    meeting.attendees_emails = "alice@example.com"  # attendee data becomes known later
    meeting_ai_service.draft_followup_email(meeting, source_text="x", requested_by="admin1")  # regenerate
    assert meeting.followup_email.to_emails == "alice@example.com"


def test_save_followup_email_edit_without_draft_raises(app, db):
    meeting = _meeting(db)
    with pytest.raises(meeting_ai_service.MeetingAIError):
        meeting_ai_service.save_followup_email_edit(meeting, subject="x", body="y", edited_by="admin1")


def test_draft_followup_email_failure_logs_and_raises(app, db, monkeypatch):
    meeting = _meeting(db)
    monkeypatch.setattr(
        "app.services.meeting_ai_service.factory.get_ai_service",
        lambda: FakeAIService(raise_error=AIServiceError("rate limited")),
    )
    with pytest.raises(meeting_ai_service.MeetingAIError):
        meeting_ai_service.draft_followup_email(meeting, source_text="x", requested_by="admin1")
    assert meeting.followup_email is None
    log = AIProcessingLog.query.filter_by(operation="DRAFT_FOLLOWUP_EMAIL").first()
    assert log.success is False


# ---------- live notes splitting (draft only — never auto-saved) ----------

def test_split_notes_returns_draft_without_persisting(app, db, monkeypatch):
    meeting = _meeting(db)
    draft = MeetingNotesDraft(
        notes=["Discussed Q3 roadmap"], decisions=["Increase marketing spend by 10%"],
        actions=[SuggestedAction(title="Send revised numbers", description=None, priority="MEDIUM")],
    )
    monkeypatch.setattr("app.services.meeting_ai_service.factory.get_ai_service", lambda: FakeAIService(notes_draft=draft))

    result = meeting_ai_service.split_notes(meeting, source_text="raw live notes", requested_by="admin1")
    assert result.notes == ["Discussed Q3 roadmap"]
    assert result.decisions == ["Increase marketing spend by 10%"]
    assert result.actions[0].title == "Send revised numbers"
    assert len(meeting.notes) == 0  # nothing persisted — EA must review and explicitly save
    assert len(meeting.decisions) == 0
    assert len(meeting.tasks) == 0


def test_split_notes_logs_ai_processing(app, db, monkeypatch):
    meeting = _meeting(db)
    monkeypatch.setattr("app.services.meeting_ai_service.factory.get_ai_service", lambda: FakeAIService())
    meeting_ai_service.split_notes(meeting, source_text="x", requested_by="admin1")

    log = AIProcessingLog.query.filter_by(operation="SPLIT_NOTES").first()
    assert log is not None
    assert log.entity_type == "MEETING"
    assert log.entity_id == str(meeting.id)


def test_split_notes_failure_logs_and_raises(app, db, monkeypatch):
    meeting = _meeting(db)
    monkeypatch.setattr(
        "app.services.meeting_ai_service.factory.get_ai_service",
        lambda: FakeAIService(raise_error=AIServiceError("rate limited")),
    )
    with pytest.raises(meeting_ai_service.MeetingAIError):
        meeting_ai_service.split_notes(meeting, source_text="x", requested_by="admin1")
    log = AIProcessingLog.query.filter_by(operation="SPLIT_NOTES").first()
    assert log.success is False


# ---------- standalone text extraction (Smart Action Extractor) ----------

def test_text_extraction_summarize(app, db, monkeypatch):
    monkeypatch.setattr("app.services.text_extraction_service.factory.get_ai_service", lambda: FakeAIService())
    result = text_extraction_service.summarize_text("some pasted text", requested_by="admin1")
    assert "Key Discussion Points" in result
    log = AIProcessingLog.query.filter_by(entity_type="ADHOC_TEXT").first()
    assert log.success is True
    assert log.entity_id is None


def test_text_extraction_extract_actions_failure(app, db, monkeypatch):
    monkeypatch.setattr(
        "app.services.text_extraction_service.factory.get_ai_service",
        lambda: FakeAIService(raise_error=AIServiceError("down")),
    )
    with pytest.raises(text_extraction_service.TextExtractionError):
        text_extraction_service.extract_actions_from_text("text", requested_by="admin1")


def test_text_extraction_transcribe_audio(app, db, monkeypatch):
    monkeypatch.setattr(
        "app.services.text_extraction_service.factory.get_transcription_service",
        lambda: FakeTranscriptionService(text="Let's discuss the roadmap."),
    )
    result = text_extraction_service.transcribe_audio("/fake/path.mp3", requested_by="admin1")
    assert result == "Let's discuss the roadmap."
    log = AIProcessingLog.query.filter_by(entity_type="ADHOC_AUDIO").first()
    assert log.success is True
    assert log.entity_id is None
    assert log.operation == "TRANSCRIBE"


def test_text_extraction_transcribe_audio_failure(app, db, monkeypatch):
    monkeypatch.setattr(
        "app.services.text_extraction_service.factory.get_transcription_service",
        lambda: FakeTranscriptionService(raise_error=TranscriptionError("down")),
    )
    with pytest.raises(text_extraction_service.TextExtractionError):
        text_extraction_service.transcribe_audio("/fake/path.mp3", requested_by="admin1")
    log = AIProcessingLog.query.filter_by(entity_type="ADHOC_AUDIO").first()
    assert log.success is False


def test_save_audio_to_temp_writes_and_validates(app):
    data = io.BytesIO(b"fake audio bytes")
    f = FileStorage(stream=data, filename="voice.mp3", content_type="audio/mpeg")
    tmp_path = attachment_service.save_audio_to_temp(f)
    try:
        import os

        assert os.path.exists(tmp_path)
        assert tmp_path.endswith(".mp3")
        with open(tmp_path, "rb") as fh:
            assert fh.read() == b"fake audio bytes"
    finally:
        import os

        os.remove(tmp_path)


def test_save_audio_to_temp_rejects_bad_extension(app):
    data = io.BytesIO(b"not audio")
    f = FileStorage(stream=data, filename="notes.txt", content_type="text/plain")
    with pytest.raises(attachment_service.AttachmentValidationError):
        attachment_service.save_audio_to_temp(f)


def test_create_general_followup_from_reviewed_suggestion(app, db):
    task = task_service.create_general_followup(
        title="Coordinate vendor approval", employee_codes=["1001"], created_by="admin1", priority=TaskPriority.HIGH,
    )
    assert task.task_type == TaskType.GENERAL_FOLLOWUP
    assert task.meeting_id is None
    assert task.project_id is None
    assert len(task.assignees) == 1


def test_create_general_followup_requires_assignee(app, db):
    with pytest.raises(task_service.TaskValidationError):
        task_service.create_general_followup(title="X", employee_codes=[], created_by="admin1")


# ---------- attachment upload validation ----------

def test_attachment_rejects_bad_extension(app):
    with app.test_request_context():
        f = FileStorage(stream=io.BytesIO(b"data"), filename="meeting.txt", content_type="text/plain")
        with pytest.raises(attachment_service.AttachmentValidationError):
            attachment_service.save_audio_attachment(f, entity_type="MEETING", entity_id="1", uploaded_by="admin1")


def test_attachment_rejects_bad_mime(app):
    with app.test_request_context():
        f = FileStorage(stream=io.BytesIO(b"data"), filename="meeting.mp3", content_type="text/plain")
        with pytest.raises(attachment_service.AttachmentValidationError):
            attachment_service.save_audio_attachment(f, entity_type="MEETING", entity_id="1", uploaded_by="admin1")


def test_attachment_rejects_empty_file(app):
    with app.test_request_context():
        f = FileStorage(stream=io.BytesIO(b""), filename="meeting.mp3", content_type="audio/mpeg")
        with pytest.raises(attachment_service.AttachmentValidationError):
            attachment_service.save_audio_attachment(f, entity_type="MEETING", entity_id="1", uploaded_by="admin1")


def test_attachment_rejects_oversized_file(app):
    app.config["MAX_AUDIO_SIZE_MB"] = 0  # anything is "too big"
    with app.test_request_context():
        f = FileStorage(stream=io.BytesIO(b"some bytes"), filename="meeting.mp3", content_type="audio/mpeg")
        with pytest.raises(attachment_service.AttachmentValidationError):
            attachment_service.save_audio_attachment(f, entity_type="MEETING", entity_id="1", uploaded_by="admin1")


def test_attachment_accepts_valid_audio(app, db, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    with app.test_request_context():
        f = FileStorage(stream=io.BytesIO(b"fake mp3 bytes"), filename="../../evil.mp3", content_type="audio/mpeg")
        attachment = attachment_service.save_audio_attachment(f, entity_type="MEETING", entity_id="42", uploaded_by="admin1")
        assert attachment.original_filename == "../../evil.mp3"  # kept only as display metadata
        assert attachment.stored_filename != "../../evil.mp3"  # never trusted for the actual file path
        assert attachment.stored_filename.endswith(".mp3")
        assert attachment.file_size == len(b"fake mp3 bytes")


def test_attachment_accepts_whatsapp_opus_voice_note(app, db, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    with app.test_request_context():
        f = FileStorage(stream=io.BytesIO(b"fake opus bytes"), filename="PTT-20260101-WA0001.opus", content_type="audio/ogg")
        attachment = attachment_service.save_audio_attachment(f, entity_type="MEETING", entity_id="42", uploaded_by="admin1")
        assert attachment.stored_filename.endswith(".opus")


def test_openai_transcription_relabels_opus_as_ogg_for_the_api(app, tmp_path, monkeypatch):
    from app.services.ai.openai_transcription_service import OpenAITranscriptionService

    audio_path = tmp_path / "recording.opus"
    audio_path.write_bytes(b"fake opus bytes")

    captured = {}

    class FakeTranscriptions:
        def create(self, *, model, file):
            captured["filename"] = file[0]
            return type("R", (), {"text": "hello"})()

    class FakeAudio:
        transcriptions = FakeTranscriptions()

    service = OpenAITranscriptionService(api_key="x", model="whisper-1")
    service._client = type("C", (), {"audio": FakeAudio()})()

    text = service.transcribe(str(audio_path))
    assert text == "hello"
    assert captured["filename"] == "recording.ogg"  # .opus relabeled so OpenAI recognizes it
