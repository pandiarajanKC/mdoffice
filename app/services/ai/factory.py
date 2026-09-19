"""Picks the configured AI provider (spec section 31: "Configure provider
through AI_PROVIDER. The business logic should not change when provider
changes."). Only OpenAI exists today; adding a local/Ollama provider
later means one new adapter class plus one branch here.
"""
from flask import current_app

from app.services.ai.base_ai_service import BaseAIService
from app.services.ai.base_speech_service import BaseSpeechService
from app.services.ai.base_transcription_service import BaseTranscriptionService


def is_configured() -> bool:
    provider = current_app.config.get("AI_PROVIDER", "")
    if provider == "openai":
        return bool(current_app.config.get("OPENAI_API_KEY"))
    return False


def get_ai_service() -> BaseAIService:
    provider = current_app.config["AI_PROVIDER"]
    if provider == "openai":
        from app.services.ai.openai_service import OpenAIService

        return OpenAIService(current_app.config["OPENAI_API_KEY"], current_app.config["OPENAI_CHAT_MODEL"])
    raise ValueError(f"Unsupported AI_PROVIDER: {provider}")


def get_transcription_service() -> BaseTranscriptionService:
    provider = current_app.config["AI_PROVIDER"]
    if provider == "openai":
        from app.services.ai.openai_transcription_service import OpenAITranscriptionService

        return OpenAITranscriptionService(current_app.config["OPENAI_API_KEY"], current_app.config["OPENAI_TRANSCRIBE_MODEL"])
    raise ValueError(f"Unsupported AI_PROVIDER: {provider}")


def get_speech_service() -> BaseSpeechService:
    provider = current_app.config["AI_PROVIDER"]
    if provider == "openai":
        from app.services.ai.openai_speech_service import OpenAISpeechService

        return OpenAISpeechService(
            current_app.config["OPENAI_API_KEY"], current_app.config["OPENAI_TTS_MODEL"], current_app.config["OPENAI_TTS_VOICE"]
        )
    raise ValueError(f"Unsupported AI_PROVIDER: {provider}")
