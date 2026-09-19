"""In-app notifications (master spec section 37) and task reminders
(section 38). `recipient_username` doubles as an employee code for
EMPLOYEE recipients — the two identifier spaces are the same by
construction (an employee's AppUser.username is always their
AssociateCode, see auth_service), so a notification can target an
employee who has never logged in yet and has no AppUser row.
"""
from app.extensions import db
from app.models.mixins import utcnow


class Notification(db.Model):
    __tablename__ = "md_notification"

    id = db.Column(db.Integer, primary_key=True)
    recipient_username = db.Column(db.String(64), nullable=False, index=True)
    notification_type = db.Column(db.String(50), nullable=False, index=True)
    title = db.Column(db.String(300), nullable=False)
    message = db.Column(db.Text, nullable=True)
    entity_type = db.Column(db.String(50), nullable=True, index=True)
    entity_id = db.Column(db.String(50), nullable=True, index=True)
    is_read = db.Column(db.Boolean, nullable=False, default=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    read_at = db.Column(db.DateTime, nullable=True)


class Reminder(db.Model):
    __tablename__ = "md_reminder"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("md_task.id"), nullable=False, index=True)
    remind_at = db.Column(db.DateTime, nullable=False, index=True)
    note = db.Column(db.Text, nullable=True)
    created_by = db.Column(db.String(64), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    is_dismissed = db.Column(db.Boolean, nullable=False, default=False)
    notified_at = db.Column(db.DateTime, nullable=True)

    task = db.relationship("Task", backref=db.backref("reminders", cascade="all, delete-orphan"))
