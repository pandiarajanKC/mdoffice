"""Smart Action Extractor (master spec section 30) — an EA utility for
pasted text (or, via the standalone Voice Extractor, an audio recording)
from email/WhatsApp/Word/chat with no meeting attached. Shares the same
AIService/transcription provider and human-review discipline as the
meeting AI workflow, just without a Meeting to hang the result off of.
"""
from __future__ import annotations

import time

from app.extensions import db
from app.models.ai_processing_log import AIProcessingLog
from app.services.ai import factory
from app.services.ai.base_ai_service import AIServiceError, SuggestedAction
from app.services.ai.base_transcription_service import TranscriptionError


class TextExtractionError(Exception):
    pass


def _log(*, entity_type: str = "ADHOC_TEXT", operation: str, provider: str, model: str | None, input_length: int,
         started: float, success: bool, error_message: str | None, requested_by: str) -> None:
    db.session.add(
        AIProcessingLog(
            entity_type=entity_type,
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


def summarize_text(text: str, *, requested_by: str) -> str:
    service = factory.get_ai_service()
    started = time.monotonic()
    try:
        result = service.summarize(text)
    except AIServiceError as exc:
        _log(operation="SUMMARIZE", provider=service.provider_name, model=service.model_name, input_length=len(text),
             started=started, success=False, error_message=str(exc), requested_by=requested_by)
        raise TextExtractionError(str(exc)) from exc
    _log(operation="SUMMARIZE", provider=service.provider_name, model=service.model_name, input_length=len(text),
         started=started, success=True, error_message=None, requested_by=requested_by)
    return result


def extract_actions_from_text(text: str, *, requested_by: str) -> list[SuggestedAction]:
    service = factory.get_ai_service()
    started = time.monotonic()
    try:
        actions = service.extract_actions(text)
    except AIServiceError as exc:
        _log(operation="EXTRACT_ACTIONS", provider=service.provider_name, model=service.model_name, input_length=len(text),
             started=started, success=False, error_message=str(exc), requested_by=requested_by)
        raise TextExtractionError(str(exc)) from exc
    _log(operation="EXTRACT_ACTIONS", provider=service.provider_name, model=service.model_name, input_length=len(text),
         started=started, success=True, error_message=None, requested_by=requested_by)
    return actions


def transcribe_audio(audio_path: str, *, requested_by: str) -> str:
    """Standalone transcription for the Voice Extractor tab — same Whisper
    call the Meeting workspace's "Transcript & AI" step uses
    (meeting_ai_service.generate_transcript), just with no Meeting/
    Attachment row to save the result against.
    """
    service = factory.get_transcription_service()
    started = time.monotonic()
    try:
        text = service.transcribe(audio_path)
    except TranscriptionError as exc:
        _log(entity_type="ADHOC_AUDIO", operation="TRANSCRIBE", provider=service.provider_name, model=service.model_name,
             input_length=0, started=started, success=False, error_message=str(exc), requested_by=requested_by)
        raise TextExtractionError(str(exc)) from exc
    _log(entity_type="ADHOC_AUDIO", operation="TRANSCRIBE", provider=service.provider_name, model=service.model_name,
         input_length=len(text), started=started, success=True, error_message=None, requested_by=requested_by)
    return text
