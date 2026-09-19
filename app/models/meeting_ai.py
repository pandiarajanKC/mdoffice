"""AI-generated meeting content (master spec sections 27-28). Both
transcript and summary are always human-editable in place — EditedBy/
EditedAt track the last edit, but the AI-generated origin is never hidden
(templates always label this "AI Generated — Review Before Saving" per
section 56 until a human has saved over it).
"""
from app.extensions import db
from app.models.mixins import utcnow


class MeetingTranscript(db.Model):
    __tablename__ = "md_meeting_transcript"

    id = db.Column(db.Integer, primary_key=True)
    meeting_id = db.Column(db.Integer, db.ForeignKey("md_meeting.id"), unique=True, nullable=False, index=True)
    transcript_text = db.Column(db.Text, nullable=False)
    provider = db.Column(db.String(30), nullable=True)
    generated_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    edited_by = db.Column(db.String(64), nullable=True)
    edited_at = db.Column(db.DateTime, nullable=True)

    meeting = db.relationship("Meeting", backref=db.backref("transcript", uselist=False, cascade="all, delete-orphan"))


class MeetingSummary(db.Model):
    __tablename__ = "md_meeting_summary"

    id = db.Column(db.Integer, primary_key=True)
    meeting_id = db.Column(db.Integer, db.ForeignKey("md_meeting.id"), unique=True, nullable=False, index=True)
    summary_text = db.Column(db.Text, nullable=False)
    provider = db.Column(db.String(30), nullable=True)
    generated_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    edited_by = db.Column(db.String(64), nullable=True)
    edited_at = db.Column(db.DateTime, nullable=True)

    meeting = db.relationship("Meeting", backref=db.backref("summary", uselist=False, cascade="all, delete-orphan"))


class MeetingFollowupEmail(db.Model):
    __tablename__ = "md_meeting_followup_email"

    id = db.Column(db.Integer, primary_key=True)
    meeting_id = db.Column(db.Integer, db.ForeignKey("md_meeting.id"), unique=True, nullable=False, index=True)
    subject = db.Column(db.String(300), nullable=False)
    body_text = db.Column(db.Text, nullable=False)
    # Comma-separated recipient email addresses, pre-filled from the
    # meeting's attendees at draft time but editable/saveable like the
    # subject and body — the EA may need to add or drop someone.
    to_emails = db.Column(db.Text, nullable=True)
    provider = db.Column(db.String(30), nullable=True)
    generated_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    edited_by = db.Column(db.String(64), nullable=True)
    edited_at = db.Column(db.DateTime, nullable=True)

    meeting = db.relationship("Meeting", backref=db.backref("followup_email", uselist=False, cascade="all, delete-orphan"))
