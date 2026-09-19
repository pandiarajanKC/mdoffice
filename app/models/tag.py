"""Tag Master (EA-managed labels attachable to meetings, projects and
tasks — tasks covers both meeting action items and standalone follow-ups,
see app/models/task.py's "Unified Task Architecture" note).

This is unrelated to the pre-existing "Tag Meeting"/"Tag to Project"
buttons (app/models/meeting_project.py), which link a meeting to a
project — that feature predates this one and happens to reuse the same
English verb. Keep the two apart in code: this module's Tag/TagAssignment
back the free-form label system exposed via the "Tags" nav item and the
app/tags blueprint.
"""
from __future__ import annotations

from app.extensions import db
from app.models.mixins import utcnow

# Cycled automatically for newly created tags when no color is chosen;
# each name must match a .tag-<color> rule in app/static/css/app.css.
TAG_COLORS = ["violet", "pink", "teal", "orange", "sky", "success", "danger", "warning"]

# entity_type values used across TagAssignment rows — kept in one place so
# routes/services/templates never hand-roll the string list separately.
ENTITY_TYPES = ("MEETING", "PROJECT", "TASK")


class Tag(db.Model):
    __tablename__ = "md_tag"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True, index=True)
    color = db.Column(db.String(20), nullable=False, default="violet")
    created_by = db.Column(db.String(64), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    assignments = db.relationship("TagAssignment", backref="tag", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Tag {self.id} {self.name!r}>"


class TagAssignment(db.Model):
    """One (tag, entity) link. ``entity_type``/``entity_id`` follow the same
    generic convention as ActivityLog/Attachment: entity_type is one of
    ``Tag.ENTITY_TYPES`` and entity_id is the target row's id as a string.
    """
    __tablename__ = "md_tag_assignment"

    id = db.Column(db.Integer, primary_key=True)
    tag_id = db.Column(db.Integer, db.ForeignKey("md_tag.id"), nullable=False, index=True)
    entity_type = db.Column(db.String(20), nullable=False, index=True)
    entity_id = db.Column(db.String(50), nullable=False, index=True)
    tagged_by = db.Column(db.String(64), nullable=False)
    tagged_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("tag_id", "entity_type", "entity_id", name="uq_tag_assignment"),
    )

    def __repr__(self) -> str:
        return f"<TagAssignment tag={self.tag_id} {self.entity_type}:{self.entity_id}>"
