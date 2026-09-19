"""Audit trail for every AI call (master spec section 57). Records enough
to answer "who ran what, on what, with which model, did it succeed" —
deliberately does not store the raw prompt/response twice over, since
the transcript/summary/task rows already hold the human-reviewed result.
"""
from app.extensions import db
from app.models.mixins import utcnow


class AIProcessingLog(db.Model):
    __tablename__ = "md_ai_processing_log"

    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(50), nullable=True, index=True)
    entity_id = db.Column(db.String(50), nullable=True, index=True)
    operation = db.Column(db.String(50), nullable=False)
    provider = db.Column(db.String(30), nullable=False)
    model = db.Column(db.String(100), nullable=True)
    input_length = db.Column(db.Integer, nullable=True)
    processing_time_ms = db.Column(db.Integer, nullable=True)
    success = db.Column(db.Boolean, nullable=False, default=True)
    error_message = db.Column(db.Text, nullable=True)
    requested_by = db.Column(db.String(64), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
