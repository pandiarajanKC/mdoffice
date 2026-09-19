"""Generic file attachment metadata (master spec section 42). Server-side
filenames only — the original filename is never trusted for storage, and
the physical path is never exposed to the browser directly (served
through a route that checks access, not a static file URL).
"""
from app.extensions import db
from app.models.mixins import utcnow


class Attachment(db.Model):
    __tablename__ = "md_attachment"

    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(50), nullable=False, index=True)
    entity_id = db.Column(db.String(50), nullable=False, index=True)
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False, unique=True)
    mime_type = db.Column(db.String(100), nullable=True)
    file_size = db.Column(db.Integer, nullable=False)
    uploaded_by = db.Column(db.String(64), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=utcnow, nullable=False)
