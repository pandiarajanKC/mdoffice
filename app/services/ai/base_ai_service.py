"""Text AI provider abstraction (master spec section 31).

Business logic (app/services/meeting_ai_service.py,
app/services/text_extraction_service.py) depends only on this interface,
never on a provider SDK directly — swapping AI_PROVIDER later (Ollama,
Azure OpenAI, ...) means writing one new adapter class here, not touching
routes, templates, or the human-review workflow.

AI output is always a *suggestion*. Nothing in this layer or its callers
ever saves an AI result as an official record without an explicit human
save/approve action (spec section 56).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SuggestedAction:
    title: str
    description: str | None
    priority: str  # LOW / MEDIUM / HIGH / CRITICAL — always human-confirmed before saving


@dataclass(frozen=True)
class EmailDraft:
    subject: str
    body: str


@dataclass(frozen=True)
class MeetingNotesDraft:
    notes: list[str]
    decisions: list[str]
    actions: list[SuggestedAction]


class AIServiceError(Exception):
    """Raised when a provider call fails or returns something unusable."""


class BaseAIService(ABC):
    provider_name: str
    model_name: str

    @abstractmethod
    def summarize(self, text: str) -> str:
        """Return a structured summary (Key Discussion Points, Decisions,
        Risks/Concerns, Action Items, Follow-up Required — spec section 28).
        """

    @abstractmethod
    def extract_actions(self, text: str) -> list[SuggestedAction]:
        """Return candidate action items. Never assigned to anyone and
        never saved by this layer — the caller always routes these through
        an EA review step first.
        """

    @abstractmethod
    def draft_followup_email(self, text: str) -> EmailDraft:
        """Draft a 'minutes of meeting' follow-up email (subject + body)
        from the meeting's notes/decisions/action items. Always a draft the
        EA reviews and edits before sending — this layer never sends email.
        """

    @abstractmethod
    def split_meeting_notes(self, text: str) -> MeetingNotesDraft:
        """Split one freeform block of live meeting notes into separate
        Notes / Decisions / Action Items. Lets an EA type everything into a
        single box during a live meeting instead of filing three separate
        forms — the result is always a draft reviewed (and editable) before
        it is saved as real MeetingNote/MeetingDecision/Task records.
        """

    @abstractmethod
    def answer_question(self, question: str, context: str) -> str:
        """Answer a spoken/typed question using only the given data context
        (a live snapshot of overdue tasks, meetings, project status, etc. —
        see app/services/assistant_service.py). Powers the voice assistant;
        must stay grounded in `context` rather than inventing facts.
        """
