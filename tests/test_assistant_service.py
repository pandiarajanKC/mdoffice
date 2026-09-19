from datetime import date, datetime, timedelta

import pytest

from app.models.ai_processing_log import AIProcessingLog
from app.models.task import TaskStatus
from app.services import assistant_service, meeting_service, project_service, task_service
from tests.fakes import FakeEmployeeRepository, make_employee
from tests.test_ai_services import FakeAIService


def _meeting(db, **overrides):
    start = overrides.pop("start_datetime", datetime.now())
    end = overrides.pop("end_datetime", start + timedelta(hours=1))
    return meeting_service.create_meeting(
        title=overrides.pop("title", "Steering Committee"), start_datetime=start, end_datetime=end,
        created_by="admin1", **overrides,
    )


def test_digest_includes_todays_meeting(app, db):
    _meeting(db, title="Ops Review Today")
    repo = FakeEmployeeRepository([])
    digest = assistant_service.build_data_digest(repo)
    assert "Ops Review Today" in digest


def test_digest_includes_overdue_task_with_assignee_name(app, db):
    meeting = _meeting(db)
    task_service.create_meeting_action(
        meeting, title="Send the report", employee_codes=["1001"], created_by="admin1",
        due_date=date.today() - timedelta(days=3),
    )
    repo = FakeEmployeeRepository([make_employee(code="1001", name="Alice")])
    digest = assistant_service.build_data_digest(repo)
    assert "Send the report" in digest
    assert "Alice" in digest
    assert "3 day(s) overdue" in digest


def test_digest_labels_overdue_section_with_both_task_and_action_item_wording(app, db):
    # A real bug: a question phrased with "action item" got "I don't have
    # that in front of me" even with overdue data present, because the
    # digest only ever said "tasks" — the model didn't reliably treat the
    # two as synonyms. Both words must appear so it can't happen again.
    repo = FakeEmployeeRepository([])
    digest = assistant_service.build_data_digest(repo)
    assert "task" in digest.lower()
    assert "action item" in digest.lower()


def test_digest_excludes_completed_tasks_from_overdue(app, db):
    meeting = _meeting(db)
    task = task_service.create_meeting_action(
        meeting, title="Old but done", employee_codes=["1001"], created_by="admin1",
        due_date=date.today() - timedelta(days=5),
    )
    task_service.update_status(task, new_status=TaskStatus.COMPLETED, performed_by="1001")
    repo = FakeEmployeeRepository([make_employee(code="1001")])
    digest = assistant_service.build_data_digest(repo)
    assert "Old but done" not in digest


def test_digest_includes_project_status_and_health(app, db):
    project_service.create_project(project_code="P-1", name="Dealer Portal", owner_employee_code="1001", created_by="admin1")
    repo = FakeEmployeeRepository([make_employee(code="1001")])
    digest = assistant_service.build_data_digest(repo)
    assert "Dealer Portal" in digest
    assert "health" in digest.lower()


def test_ask_returns_grounded_answer_and_logs(app, db, monkeypatch):
    monkeypatch.setattr(
        "app.services.assistant_service.factory.get_ai_service",
        lambda: FakeAIService(answer="You have nothing overdue today."),
    )
    repo = FakeEmployeeRepository([])
    answer = assistant_service.ask("What's overdue?", requested_by="admin1", employee_repository=repo)
    assert answer == "You have nothing overdue today."

    log = AIProcessingLog.query.filter_by(operation="ASK", entity_type="VOICE_ASSISTANT").first()
    assert log is not None
    assert log.success is True
    assert log.requested_by == "admin1"


def test_ask_failure_logs_and_raises(app, db, monkeypatch):
    from app.services.ai.base_ai_service import AIServiceError

    monkeypatch.setattr(
        "app.services.assistant_service.factory.get_ai_service",
        lambda: FakeAIService(raise_error=AIServiceError("rate limited")),
    )
    repo = FakeEmployeeRepository([])
    with pytest.raises(assistant_service.AssistantError):
        assistant_service.ask("What's overdue?", requested_by="admin1", employee_repository=repo)

    log = AIProcessingLog.query.filter_by(operation="ASK", entity_type="VOICE_ASSISTANT").first()
    assert log.success is False


def test_transcribe_question_wraps_transcription_error(app, monkeypatch):
    from app.services.ai.base_transcription_service import TranscriptionError

    class FailingTranscriber:
        provider_name = "fake"
        model_name = "fake"

        def transcribe(self, audio_path, *, language=None, prompt=None):
            raise TranscriptionError("bad audio")

    monkeypatch.setattr("app.services.assistant_service.factory.get_transcription_service", lambda: FailingTranscriber())
    with pytest.raises(assistant_service.AssistantError):
        assistant_service.transcribe_question("/tmp/does-not-matter.webm")


@pytest.mark.parametrize("hallucination", ["You", "you.", "Thank you.", "THANKS FOR WATCHING", "  ", "Okay."])
def test_transcribe_question_filters_whisper_silence_hallucinations(app, monkeypatch, hallucination):
    class StubTranscriber:
        provider_name = "fake"
        model_name = "fake"

        def transcribe(self, audio_path, *, language=None, prompt=None):
            return hallucination

    monkeypatch.setattr("app.services.assistant_service.factory.get_transcription_service", lambda: StubTranscriber())
    assert assistant_service.transcribe_question("/tmp/does-not-matter.webm") == ""


@pytest.mark.parametrize("bleed_through", [
    assistant_service.GREETING_TEXT,
    "how can I help you today",
    "Hi I am AURA how can I help",
])
def test_transcribe_question_filters_greeting_bleed_through(app, monkeypatch, bleed_through):
    # A real bug: AURA's own greeting could bleed into the mic recording
    # right as listening started (speaker-to-mic pickup), get transcribed,
    # and get answered as if the EA had asked it.
    class StubTranscriber:
        provider_name = "fake"
        model_name = "fake"

        def transcribe(self, audio_path, *, language=None, prompt=None):
            return bleed_through

    monkeypatch.setattr("app.services.assistant_service.factory.get_transcription_service", lambda: StubTranscriber())
    assert assistant_service.transcribe_question("/tmp/does-not-matter.webm") == ""


@pytest.mark.parametrize("real_question", [
    "Can you help me today",
    "Can you help me with tasks today",
    "What tasks are overdue right now",
])
def test_transcribe_question_does_not_false_positive_on_short_real_questions(app, monkeypatch, real_question):
    # The greeting is short and made of common words ("how", "can", "help",
    # "you"), so an ordinary short real question sharing a few of them must
    # not get mistaken for an echo of the greeting.
    class StubTranscriber:
        provider_name = "fake"
        model_name = "fake"

        def transcribe(self, audio_path, *, language=None, prompt=None):
            return real_question

    monkeypatch.setattr("app.services.assistant_service.factory.get_transcription_service", lambda: StubTranscriber())
    assert assistant_service.transcribe_question("/tmp/does-not-matter.webm") == real_question


def test_transcribe_question_keeps_real_speech(app, monkeypatch):
    class StubTranscriber:
        provider_name = "fake"
        model_name = "fake"

        def transcribe(self, audio_path, *, language=None, prompt=None):
            return "What tasks are overdue right now?"

    monkeypatch.setattr("app.services.assistant_service.factory.get_transcription_service", lambda: StubTranscriber())
    assert assistant_service.transcribe_question("/tmp/does-not-matter.webm") == "What tasks are overdue right now?"


def test_transcribe_question_pins_language_and_sends_domain_prompt(app, monkeypatch):
    captured = {}

    class RecordingTranscriber:
        provider_name = "fake"
        model_name = "fake"

        def transcribe(self, audio_path, *, language=None, prompt=None):
            captured["language"] = language
            captured["prompt"] = prompt
            return "What tasks are overdue right now?"

    monkeypatch.setattr("app.services.assistant_service.factory.get_transcription_service", lambda: RecordingTranscriber())
    assistant_service.transcribe_question("/tmp/does-not-matter.webm")
    assert captured["language"] == "en"
    assert captured["prompt"]  # a non-empty steering prompt was sent


def test_synthesize_answer_wraps_speech_error(app, monkeypatch):
    from app.services.ai.base_speech_service import SpeechError

    class FailingSpeaker:
        provider_name = "fake"
        model_name = "fake"

        def synthesize(self, text):
            raise SpeechError("tts down")

    monkeypatch.setattr("app.services.assistant_service.factory.get_speech_service", lambda: FailingSpeaker())
    with pytest.raises(assistant_service.AssistantError):
        assistant_service.synthesize_answer("hello")
