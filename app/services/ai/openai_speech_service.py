"""OpenAI text-to-speech implementation of BaseSpeechService."""
from __future__ import annotations

from openai import OpenAI

from app.services.ai.base_speech_service import BaseSpeechService, SpeechError


class OpenAISpeechService(BaseSpeechService):
    provider_name = "openai"

    def __init__(self, api_key: str, model: str, voice: str):
        self.model_name = model
        self._voice = voice
        self._client = OpenAI(api_key=api_key)

    def synthesize(self, text: str) -> bytes:
        try:
            response = self._client.audio.speech.create(model=self.model_name, voice=self._voice, input=text)
            return response.content
        except Exception as exc:  # noqa: BLE001
            raise SpeechError(f"OpenAI speech synthesis failed: {exc}") from exc
