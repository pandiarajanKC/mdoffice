"""Many-to-many mapping between Meeting and Project (master spec section 18)."""
from __future__ import annotations

from app.extensions import db
from app.models.mixins import utcnow


class MeetingProject(db.Model):
    __tablename__ = "md_meeting_project"

    id = db.Column(db.Integer, primary_key=True)
    meeting_id = db.Column(db.Integer, db.ForeignKey("md_meeting.id"), nullable=False, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey("md_project.id"), nullable=False, index=True)
    tagged_by = db.Column(db.String(64), nullable=False)
    tagged_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    meeting = db.relationship("Meeting", backref="project_links")
    project = db.relationship("Project", backref="meeting_links")

    __table_args__ = (db.UniqueConstraint("meeting_id", "project_id", name="uq_meeting_project"),)
