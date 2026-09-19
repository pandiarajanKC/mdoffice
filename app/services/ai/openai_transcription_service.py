"""OpenAI Whisper implementation of BaseTranscriptionService."""
from __future__ import annotations

import os

from openai import OpenAI

from app.services.ai.base_transcription_service import BaseTranscriptionService, TranscriptionError

# OpenAI's documented Whisper format list doesn't include ".opus" directly,
# even though a WhatsApp voice note saved as .opus is just Opus audio in an
# Ogg container — a format Whisper does accept. Relabel the filename (not
# the file on disk) so the API recognizes it, without touching what's
# actually stored for playback/record-keeping.
_EXTENSION_REMAP_FOR_API = {".opus": ".ogg"}


class OpenAITranscriptionService(BaseTranscriptionService):
    provider_name = "openai"

    def __init__(self, api_key: str, model: str):
        self.model_name = model
        self._client = OpenAI(api_key=api_key)

    def transcribe(self, audio_path: str, *, language: str | None = None, prompt: str | None = None) -> str:
        try:
            filename = os.path.basename(audio_path)
            stem, ext = os.path.splitext(filename)
            api_filename = stem + _EXTENSION_REMAP_FOR_API.get(ext.lower(), ext)

            kwargs = {}
            if language:
                kwargs["language"] = language
            if prompt:
                kwargs["prompt"] = prompt

            with open(audio_path, "rb") as audio_file:
                response = self._client.audio.transcriptions.create(
                    model=self.model_name, file=(api_filename, audio_file), **kwargs
                )
            return (response.text or "").strip()
        except Exception as exc:  # noqa: BLE001
            raise TranscriptionError(f"OpenAI transcription failed: {exc}") from exc
