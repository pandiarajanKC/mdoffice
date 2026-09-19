"""Employee progress updates / comments on a Task (master spec section 23).

A single generic table for every task type, per the spec's own note to
avoid duplicating this per module.
"""
from app.extensions import db
from app.models.mixins import utcnow


class TaskProgressUpdate(db.Model):
    __tablename__ = "md_task_progress_update"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("md_task.id"), nullable=False, index=True)
    employee_code = db.Column(db.String(50), nullable=True, index=True)
    performed_by = db.Column(db.String(64), nullable=False)
    update_text = db.Column(db.Text, nullable=False)
    progress_percent = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)

    task = db.relationship("Task", backref=db.backref("progress_updates", order_by="TaskProgressUpdate.created_at.desc()"))
