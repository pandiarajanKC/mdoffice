"""Text-to-speech provider abstraction, mirroring base_transcription_service.py's
reasoning: kept separate from BaseAIService since this returns audio bytes
rather than text, and a deployment might reasonably mix providers.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class SpeechError(Exception):
    pass


class BaseSpeechService(ABC):
    provider_name: str
    model_name: str

    @abstractmethod
    def synthesize(self, text: str) -> bytes:
        """Return audio bytes (mp3) speaking the given text aloud."""
