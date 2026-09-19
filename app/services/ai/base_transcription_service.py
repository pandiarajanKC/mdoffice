"""Audio transcription provider abstraction (master spec section 26).

Kept separate from BaseAIService since transcription takes an audio file
rather than text, and a deployment might reasonably mix providers (e.g.
local Whisper for audio + a hosted API for summarization) even though
this app currently uses OpenAI for both.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class TranscriptionError(Exception):
    pass


class BaseTranscriptionService(ABC):
    provider_name: str
    model_name: str

    @abstractmethod
    def transcribe(self, audio_path: str, *, language: str | None = None, prompt: str | None = None) -> str:
        """Return the transcript text for the audio file at this path.

        `language` pins the spoken language instead of letting the model
        guess it from a few seconds of audio (a common source of garbled
        output on short clips). `prompt` is Whisper's steering mechanism —
        a short sample of expected vocabulary/phrasing that nudges word
        choice on ambiguous audio without changing what's actually said.
        Both are optional so existing callers (full meeting recordings,
        where neither is needed) are unaffected.
        """
