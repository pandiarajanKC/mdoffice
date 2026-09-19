"""Generic audit trail used across every module (auth, meetings, tasks, ...).

A single table keeps reporting and querying simple, per the master spec's
instruction to avoid duplicating audit logic per-entity.
"""
from app.extensions import db
from app.models.mixins import utcnow


class ActivityLog(db.Model):
    __tablename__ = "md_activity_log"

    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(50), nullable=False, index=True)
    entity_id = db.Column(db.String(50), nullable=True, index=True)
    action_type = db.Column(db.String(50), nullable=False)
    old_value = db.Column(db.Text, nullable=True)
    new_value = db.Column(db.Text, nullable=True)
    performed_by = db.Column(db.String(64), nullable=False)
    performed_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    ip_address = db.Column(db.String(64), nullable=True)

    @classmethod
    def record(
        cls,
        *,
        entity_type: str,
        action_type: str,
        performed_by: str,
        entity_id: str | None = None,
        old_value: str | None = None,
        new_value: str | None = None,
        ip_address: str | None = None,
    ) -> "ActivityLog":
        entry = cls(
            entity_type=entity_type,
            entity_id=entity_id,
            action_type=action_type,
            old_value=old_value,
            new_value=new_value,
            performed_by=performed_by,
            ip_address=ip_address,
        )
        db.session.add(entry)
        return entry
