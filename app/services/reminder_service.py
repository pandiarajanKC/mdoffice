"""EA follow-up reminders against a task (master spec section 38)."""
from __future__ import annotations

from datetime import datetime

from app.extensions import db
from app.models.activity_log import ActivityLog
from app.models.mixins import utcnow
from app.models.notification import Reminder
from app.models.task import Task
from app.services import notification_service


class ReminderValidationError(Exception):
    pass


def create_reminder(task: Task, *, remind_at: datetime, note: str | None, created_by: str) -> Reminder:
    if remind_at <= utcnow():
        raise ReminderValidationError("Reminder time must be in the future.")

    reminder = Reminder(task_id=task.id, remind_at=remind_at, note=(note or "").strip() or None, created_by=created_by)
    db.session.add(reminder)
    ActivityLog.record(
        entity_type="TASK", entity_id=str(task.id), action_type="REMINDER_SET", performed_by=created_by,
        new_value=remind_at.strftime("%d %b %Y %I:%M %p"),
    )
    db.session.commit()
    return reminder


def dismiss_reminder(reminder: Reminder, *, performed_by: str) -> None:
    reminder.is_dismissed = True
    ActivityLog.record(
        entity_type="TASK", entity_id=str(reminder.task_id), action_type="REMINDER_DISMISSED", performed_by=performed_by
    )
    db.session.commit()


def due_reminders(now: datetime | None = None) -> list[Reminder]:
    now = now or utcnow()
    # .is_(False) compiles to the invalid "IS 0" on SQL Server (IS only
    # accepts NULL there) - negate the column instead, which compiles to
    # the portable "= 0" on every dialect.
    return Reminder.query.filter(
        Reminder.remind_at <= now, Reminder.notified_at.is_(None), ~Reminder.is_dismissed
    ).all()


def fire_due_reminders() -> int:
    """Turn every due reminder into a notification for the task's
    assignees. Safe to call repeatedly (APScheduler tick) — a reminder is
    only ever fired once because `notified_at` gets set immediately.
    """
    fired = 0
    for reminder in due_reminders():
        task = reminder.task
        recipients = [a.employee_code for a in task.assignees]
        notification_service.notify_many(
            recipients,
            notification_type="REMINDER",
            title=f"Reminder: {task.title}",
            message=reminder.note,
            entity_type="TASK",
            entity_id=str(task.id),
        )
        reminder.notified_at = utcnow()
        fired += 1
    if fired:
        db.session.commit()
    return fired
