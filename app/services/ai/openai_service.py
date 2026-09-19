"""OpenAI implementation of BaseAIService (chat completions)."""
from __future__ import annotations

import json

from openai import OpenAI

from app.services.ai.base_ai_service import AIServiceError, BaseAIService, EmailDraft, MeetingNotesDraft, SuggestedAction

_VALID_PRIORITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}

_SUMMARY_SYSTEM_PROMPT = """You are an assistant that summarizes executive meeting transcripts or notes.
Produce a structured summary using exactly these section headings, each followed by concise bullet points
(use "-" for bullets; omit a section only if it is genuinely empty):

Key Discussion Points
Decisions
Risks / Concerns
Action Items
Follow-up Required

Be factual and concise. Do not invent information that is not in the source text."""

_ACTIONS_SYSTEM_PROMPT = """You extract candidate action items from a meeting summary or transcript.
Return ONLY a JSON object of the form:
{"actions": [{"title": "...", "description": "...", "priority": "LOW|MEDIUM|HIGH|CRITICAL"}]}
- "title" is a short imperative action (e.g. "Prepare validation report").
- "description" gives brief context, or an empty string if none is needed.
- "priority" reflects urgency implied by the text; default to "MEDIUM" if unclear.
- Only include real, actionable items actually implied by the text. If none exist, return {"actions": []}."""

_FOLLOWUP_EMAIL_SYSTEM_PROMPT = """You draft a professional "Minutes of Meeting" follow-up email to send to
meeting attendees, based on the meeting details, notes, decisions, and action items given to you.
Return ONLY a JSON object of the form: {"subject": "...", "body": "..."}
- "subject" is short and specific, e.g. "Minutes of Meeting: Weekly Ops Review - 05 Sep 2026".
- "body" is plain text (no markdown or HTML): a brief greeting, a "Decisions" section, an "Action Items"
  section listing each item with its owner and due date if given, and a short closing line. Separate
  sections with blank lines rather than markdown headers.
- Be factual and concise. Do not invent information that is not in the source text."""

_SPLIT_NOTES_SYSTEM_PROMPT = """You organize a live, freeform block of meeting notes an assistant typed
while a meeting was happening. Split it into plain discussion notes, decisions made, and action items.
Return ONLY a JSON object of the form:
{"notes": ["..."], "decisions": ["..."], "actions": [{"title": "...", "description": "...", "priority": "LOW|MEDIUM|HIGH|CRITICAL"}]}
- "notes": general discussion points that are neither a decision nor an action — one per array entry.
- "decisions": things the group explicitly agreed/decided — one per array entry, written as a complete sentence.
- "actions": concrete follow-up tasks someone needs to do. "title" is short and imperative (e.g. "Send revised
  budget to Finance"); "description" gives brief context or an empty string; "priority" reflects urgency implied
  by the text, default "MEDIUM" if unclear.
- Every sentence in the source text should end up in exactly one of the three lists, if it fits one at all.
  Skip filler with no informational content (e.g. "ok let's start").
- Do not invent information that is not in the source text. If a category is empty, return an empty array for it."""

_ASSISTANT_SYSTEM_PROMPT = """Your name is AURA, a friendly, warm voice assistant for the Managing Director's
office, helping the Executive Assistant (EA) quickly understand what's going on. If asked your name or who you
are, say you're AURA. You are given a snapshot of current data (meetings, tasks, projects, workload) and a
spoken question. Your reply will be read aloud, so:
- Answer in short, natural, conversational sentences — no markdown, no bullet points, no headers, no lists.
- Answer ONLY using the data provided below. If it doesn't contain the answer, say so honestly (e.g.
  "I don't have that in front of me") rather than guessing or inventing numbers.
- In this system "task" and "action item" are two names for the exact same kind of record — never treat
  a question about one as unanswerable just because the data below is labeled with the other word.
- Be warm and personable, not robotic — brief and to the point, like a helpful colleague, not an essay.
- If the question has nothing to do with meetings, tasks, projects or the office, politely say you can only
  help with that kind of thing here."""


class OpenAIService(BaseAIService):
    provider_name = "openai"

    def __init__(self, api_key: str, model: str):
        self.model_name = model
        self._client = OpenAI(api_key=api_key)

    def summarize(self, text: str) -> str:
        try:
            response = self._client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                temperature=0.2,
            )
            return (response.choices[0].message.content or "").strip()
        except Exception as exc:  # noqa: BLE001 - surface as a domain error
            raise AIServiceError(f"OpenAI summarization failed: {exc}") from exc

    def extract_actions(self, text: str) -> list[SuggestedAction]:
        try:
            response = self._client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": _ACTIONS_SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            raw = (response.choices[0].message.content or "{}").strip()
            data = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            raise AIServiceError(f"OpenAI action extraction failed: {exc}") from exc

        actions = []
        for item in data.get("actions", []):
            title = (item.get("title") or "").strip()
            if not title:
                continue
            priority = (item.get("priority") or "MEDIUM").upper()
            if priority not in _VALID_PRIORITIES:
                priority = "MEDIUM"
            actions.append(
                SuggestedAction(title=title, description=(item.get("description") or "").strip() or None, priority=priority)
            )
        return actions

    def draft_followup_email(self, text: str) -> EmailDraft:
        try:
            response = self._client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": _FOLLOWUP_EMAIL_SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                temperature=0.3,
                response_format={"type": "json_object"},
            )
            raw = (response.choices[0].message.content or "{}").strip()
            data = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            raise AIServiceError(f"OpenAI follow-up email drafting failed: {exc}") from exc

        subject = (data.get("subject") or "").strip() or "Minutes of Meeting"
        body = (data.get("body") or "").strip()
        return EmailDraft(subject=subject, body=body)

    def split_meeting_notes(self, text: str) -> MeetingNotesDraft:
        try:
            response = self._client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": _SPLIT_NOTES_SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            raw = (response.choices[0].message.content or "{}").strip()
            data = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            raise AIServiceError(f"OpenAI note splitting failed: {exc}") from exc

        notes = [n.strip() for n in data.get("notes", []) if (n or "").strip()]
        decisions = [d.strip() for d in data.get("decisions", []) if (d or "").strip()]

        actions = []
        for item in data.get("actions", []):
            title = (item.get("title") or "").strip()
            if not title:
                continue
            priority = (item.get("priority") or "MEDIUM").upper()
            if priority not in _VALID_PRIORITIES:
                priority = "MEDIUM"
            actions.append(
                SuggestedAction(title=title, description=(item.get("description") or "").strip() or None, priority=priority)
            )
        return MeetingNotesDraft(notes=notes, decisions=decisions, actions=actions)

    def answer_question(self, question: str, context: str) -> str:
        try:
            response = self._client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": _ASSISTANT_SYSTEM_PROMPT},
                    {"role": "user", "content": f"CURRENT DATA:\n{context}\n\nQUESTION:\n{question}"},
                ],
                temperature=0.4,
            )
            return (response.choices[0].message.content or "").strip()
        except Exception as exc:  # noqa: BLE001
            raise AIServiceError(f"OpenAI question answering failed: {exc}") from exc
