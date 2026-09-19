"""Reusable meeting templates — an EA convenience so a recurring series
(a weekly ops review, a monthly board prep) doesn't mean retyping the same
title/type/location/duration every time a new instance is scheduled.
"""
from app.extensions import db
from app.models.meeting import ConfidentialityLevel
from app.models.mixins import TimestampMixin


class MeetingTemplate(TimestampMixin, db.Model):
    __tablename__ = "md_meeting_template"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, unique=True)

    title = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text, nullable=True)
    duration_minutes = db.Column(db.Integer, nullable=False, default=60)
    location = db.Column(db.String(300), nullable=True)
    meeting_link = db.Column(db.String(500), nullable=True)
    organizer = db.Column(db.String(200), nullable=True)
    meeting_type = db.Column(db.String(100), nullable=True)
    confidentiality_level = db.Column(
        db.Enum(ConfidentialityLevel, name="md_confidentiality_level"),
        nullable=False,
        default=ConfidentialityLevel.NORMAL,
    )

    created_by = db.Column(db.String(64), nullable=False)

    def __repr__(self) -> str:
        return f"<MeetingTemplate {self.name!r}>"
