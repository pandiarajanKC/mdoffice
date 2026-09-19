"""Meeting, MeetingNote and MeetingDecision — the core of the EA's live
meeting workspace (capture notes/decisions/actions without leaving the page).
"""
from __future__ import annotations

import enum

from app.extensions import db
from app.models.mixins import TimestampMixin


class MeetingStatus(str, enum.Enum):
    SCHEDULED = "SCHEDULED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    ON_HOLD = "ON_HOLD"


class ConfidentialityLevel(str, enum.Enum):
    NORMAL = "NORMAL"
    RESTRICTED = "RESTRICTED"
    CONFIDENTIAL = "CONFIDENTIAL"


class Meeting(TimestampMixin, db.Model):
    __tablename__ = "md_meeting"

    id = db.Column(db.Integer, primary_key=True)
    external_calendar_event_id = db.Column(db.String(200), nullable=True, index=True)
    calendar_provider = db.Column(db.String(30), nullable=True)

    title = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text, nullable=True)

    start_datetime = db.Column(db.DateTime, nullable=False, index=True)
    end_datetime = db.Column(db.DateTime, nullable=False)

    location = db.Column(db.String(300), nullable=True)
    meeting_link = db.Column(db.String(500), nullable=True)
    organizer = db.Column(db.String(200), nullable=True)
    meeting_type = db.Column(db.String(100), nullable=True)
    attendees_text = db.Column(db.Text, nullable=True)
    # Comma-separated real email addresses — kept separate from
    # attendees_text above, which prefers a person's display name over
    # their email when Google Calendar provides one, so it's often not
    # usable for actually sending mail. This is what feeds the follow-up
    # email's "To" field.
    attendees_emails = db.Column(db.Text, nullable=True)

    confidentiality_level = db.Column(
        db.Enum(ConfidentialityLevel, name="md_confidentiality_level"),
        nullable=False,
        default=ConfidentialityLevel.NORMAL,
    )
    status = db.Column(
        db.Enum(MeetingStatus, name="md_meeting_status"),
        nullable=False,
        default=MeetingStatus.SCHEDULED,
        index=True,
    )

    created_by = db.Column(db.String(64), nullable=False)
    updated_by = db.Column(db.String(64), nullable=True)
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)

    notes = db.relationship(
        "MeetingNote", backref="meeting", order_by="MeetingNote.sequence", cascade="all, delete-orphan"
    )
    decisions = db.relationship(
        "MeetingDecision", backref="meeting", order_by="MeetingDecision.decision_date", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Meeting {self.id} {self.title!r}>"


class MeetingNote(TimestampMixin, db.Model):
    __tablename__ = "md_meeting_note"

    id = db.Column(db.Integer, primary_key=True)
    meeting_id = db.Column(db.Integer, db.ForeignKey("md_meeting.id"), nullable=False, index=True)
    note_text = db.Column(db.Text, nullable=False)
    sequence = db.Column(db.Integer, nullable=False, default=0)
    created_by = db.Column(db.String(64), nullable=False)


class MeetingDecision(TimestampMixin, db.Model):
    __tablename__ = "md_meeting_decision"

    id = db.Column(db.Integer, primary_key=True)
    meeting_id = db.Column(db.Integer, db.ForeignKey("md_meeting.id"), nullable=False, index=True)
    decision_text = db.Column(db.Text, nullable=False)
    decision_date = db.Column(db.DateTime, nullable=False)
    created_by = db.Column(db.String(64), nullable=False)
