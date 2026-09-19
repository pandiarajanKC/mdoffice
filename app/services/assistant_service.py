"""Voice assistant for the EA — "AURA" (AI Tools). Answers a
spoken question grounded in a live snapshot of real data (meetings, tasks,
projects, workload), never a fabricated answer. Speech in, speech out, but
the actual reasoning step is the same text-in/text-out AIService pattern
used throughout this app.
"""
from __future__ import annotations

import time
from datetime import datetime

from app.extensions import db
from app.models.ai_processing_log import AIProcessingLog
from app.models.meeting import Meeting, MeetingStatus
from app.models.project import Project
from app.repositories.employee_repository import EmployeeRepository
from app.services import analytics_service, project_service
from app.services.ai import factory
from app.services.ai.base_ai_service import AIServiceError
from app.services.ai.base_speech_service import SpeechError
from app.services.ai.base_transcription_service import TranscriptionError


class AssistantError(Exception):
    pass


# Whisper's well-documented behavior on silent/near-silent/corrupted audio is
# to hallucinate a short stock phrase rather than return nothing — "you" is
# the most common one, but a handful of others show up too. Treat any of
# these (after stripping punctuation/case) as "no speech detected" instead of
# passing them to the LLM as if they were a real question — otherwise the
# assistant confidently "answers" a word nobody said.
_NO_SPEECH_HALLUCINATIONS = frozenset({
    "", "you", "thank you", "thanks", "bye", "bye bye", "ok", "okay",
    "thank you for watching", "thanks for watching", "please subscribe",
    "subtitles by the amara org community",
})


def _looks_like_silence(text: str) -> bool:
    normalized = text.strip().strip(".!?,").lower()
    return normalized in _NO_SPEECH_HALLUCINATIONS


_PUNCTUATION_TABLE = str.maketrans("", "", ".,!?\"'—")


def _looks_like_self_capture(text: str) -> bool:
    """AURA's own greeting can occasionally bleed into the mic recording
    right as listening starts (speaker-to-mic pickup on a laptop with no
    headset, in the moment before the greeting's audio has fully finished
    physically playing). If most of what was "heard" is just the greeting's
    own wording, that's almost certainly what happened — treat it as no
    question asked rather than forwarding AURA's own words back to the LLM
    as if the EA had said them.

    The greeting is short and made almost entirely of common words ("how",
    "can", "help", "you"), so both thresholds are stricter than a longer,
    more distinctive greeting would need — otherwise an ordinary short
    question like "Can you help me today?" gets mistaken for an echo.
    """
    words = [w for w in text.lower().translate(_PUNCTUATION_TABLE).split() if w]
    if len(words) < 5:
        return False
    greeting_words = set(GREETING_TEXT.lower().translate(_PUNCTUATION_TABLE).split())
    overlap = sum(1 for w in words if w in greeting_words)
    return overlap / len(words) >= 0.8


def _log(*, operation: str, provider: str, model: str | None, input_length: int, started: float,
         success: bool, error_message: str | None, requested_by: str) -> None:
    db.session.add(
        AIProcessingLog(
            entity_type="VOICE_ASSISTANT",
            entity_id=None,
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


def build_data_digest(employee_repository: EmployeeRepository) -> str:
    """A live, bounded snapshot of current org state — the only source of
    truth the assistant is allowed to answer from (spec-consistent with the
    rest of this app: never let an AI feature invent numbers).
    """
    lines: list[str] = []

    todays = analytics_service.todays_meetings()
    lines.append(f"Today's meetings ({len(todays)}):")
    if todays:
        for m in todays:
            lines.append(f"- \"{m.title}\" at {m.start_datetime.strftime('%I:%M %p')}, status {m.status.value}"
                         + (f", location {m.location}" if m.location else ""))
    else:
        lines.append("- none scheduled today")

    upcoming_count = Meeting.query.filter(
        Meeting.start_datetime > datetime.now(), Meeting.status != MeetingStatus.CANCELLED
    ).count()
    lines.append(f"\nUpcoming meetings after today: {upcoming_count}")

    overdue = analytics_service.overdue_tasks(employee_repository, limit=15)
    # Spell out both words this app uses for the same thing ("task" on the
    # Tasks module, "action item" on the dashboard/Action Tracker) right in
    # the line itself — otherwise a question phrased with one word can get
    # answered as "I don't have that" even though the number is right here,
    # because the model won't reliably infer the two are synonymous here.
    lines.append(f"\nOverdue tasks / action items ({overdue['total']} total, most overdue first, up to 15 listed):")
    if overdue["rows"]:
        for row in overdue["rows"]:
            lines.append(
                f"- \"{row['task'].title}\" — {row['days_overdue']} day(s) overdue, "
                f"assigned to {row['assignees']}, from {row['source_label']}"
            )
    else:
        lines.append("- none, nothing is overdue right now")

    projects = Project.query.order_by(Project.created_at.desc()).limit(30).all()
    lines.append(f"\nProjects ({len(projects)} shown, most recent first):")
    if projects:
        for p in projects:
            health = project_service.health(p)
            target = f", target date {p.target_date.strftime('%d %b %Y')}" if p.target_date else ""
            lines.append(
                f"- \"{p.name}\" ({p.project_code}): status {p.status.value}, health {health}, "
                f"{p.progress_percent}% complete, priority {p.priority.value}{target}"
            )
    else:
        lines.append("- no projects yet")

    workload = analytics_service.employee_workload(employee_repository, limit=10)
    lines.append(f"\nEmployee workload, top {len(workload)} by open items:")
    if workload:
        for w in workload:
            lines.append(f"- {w['name']}: {w['open']} open, {w['in_progress']} in progress, {w['overdue']} overdue")
    else:
        lines.append("- no assigned tasks yet")

    task_dist = analytics_service.task_status_distribution()
    lines.append("\nAll tasks / action items by status: " + ", ".join(f"{k.replace('_', ' ').title()}={v}" for k, v in task_dist.items()))

    pending_followup = analytics_service.meetings_with_pending_followup(limit=10)
    lines.append(f"\nCompleted meetings with pending follow-up ({len(pending_followup)}):")
    if pending_followup:
        for item in pending_followup:
            lines.append(f"- \"{item['meeting'].title}\": {item['followup_status']}")
    else:
        lines.append("- none — everything from completed meetings is wrapped up")

    return "\n".join(lines)


# Whisper's transcription quality on short clips improves noticeably when
# it isn't also guessing the spoken language, and its documented "prompt"
# mechanism lets a short sample of expected wording bias word choice on
# unclear audio toward this app's own vocabulary (project/task/meeting
# terms, "EA"/"MD") instead of the nearest-sounding generic word.
_QUESTION_LANGUAGE = "en"
_QUESTION_PROMPT = (
    "Questions about MD Office: meetings, action items, tasks, projects, "
    "overdue items, follow-ups, employee workload, dashboard, EA, MD."
)


def transcribe_question(audio_path: str) -> str:
    service = factory.get_transcription_service()
    try:
        text = service.transcribe(audio_path, language=_QUESTION_LANGUAGE, prompt=_QUESTION_PROMPT)
    except TranscriptionError as exc:
        raise AssistantError(str(exc)) from exc
    if _looks_like_silence(text) or _looks_like_self_capture(text):
        return ""
    return text


def ask(question: str, *, requested_by: str, employee_repository: EmployeeRepository) -> str:
    service = factory.get_ai_service()
    context = build_data_digest(employee_repository)
    started = time.monotonic()
    try:
        answer = service.answer_question(question, context)
    except AIServiceError as exc:
        _log(operation="ASK", provider=service.provider_name, model=service.model_name, input_length=len(question),
             started=started, success=False, error_message=str(exc), requested_by=requested_by)
        raise AssistantError(str(exc)) from exc

    _log(operation="ASK", provider=service.provider_name, model=service.model_name, input_length=len(question),
         started=started, success=True, error_message=None, requested_by=requested_by)
    return answer


def synthesize_answer(text: str) -> bytes:
    service = factory.get_speech_service()
    try:
        return service.synthesize(text)
    except SpeechError as exc:
        raise AssistantError(str(exc)) from exc


GREETING_TEXT = "Hi, I am AURA. How can I help you?"

# The greeting is the same fixed line every time, so it's synthesized once
# per server run and reused — no reason to pay OpenAI TTS latency/cost on
# every single first-click. Cleared only by a process restart.
_greeting_audio_cache: bytes | None = None


def get_greeting() -> tuple[str, bytes]:
    global _greeting_audio_cache
    if _greeting_audio_cache is None:
        _greeting_audio_cache = synthesize_answer(GREETING_TEXT)
    return GREETING_TEXT, _greeting_audio_cache
