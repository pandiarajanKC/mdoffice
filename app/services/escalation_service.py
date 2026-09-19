"""Follow-up escalation rules (master spec section 66):

    Task due tomorrow      -> Employee reminder
    Task overdue by 1 day  -> Employee reminder
    Task overdue by 3 days -> EA notification
    Task overdue by 7 days -> MD dashboard escalation

Each rule fires at most once per (recipient, task) via
notification_service.already_notified — safe to run on every scheduler
tick without spamming anyone.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.extensions import db
from app.models.app_user import AppUser, UserRole
from app.models.task import Task, TaskStatus
from app.services import notification_service

DUE_TOMORROW = "DUE_TOMORROW"
OVERDUE_1D = "OVERDUE_1D"
OVERDUE_3D_EA = "OVERDUE_3D_EA"
OVERDUE_7D_MD = "OVERDUE_7D_MD"


def _open_tasks_with_due_date():
    return Task.query.filter(
        Task.due_date.isnot(None), Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED])
    ).all()


def _notify_assignees_once(task: Task, notification_type: str, title: str) -> int:
    sent = 0
    for assignee in task.assignees:
        if notification_service.already_notified(
            recipient_username=assignee.employee_code,
            notification_type=notification_type,
            entity_type="TASK",
            entity_id=str(task.id),
        ):
            continue
        notification_service.notify(
            recipient_username=assignee.employee_code,
            notification_type=notification_type,
            title=title,
            entity_type="TASK",
            entity_id=str(task.id),
        )
        sent += 1
    return sent


def _notify_role_once(task: Task, role: UserRole, notification_type: str, title: str) -> int:
    sent = 0
    for user in AppUser.query.filter_by(role=role).all():
        if notification_service.already_notified(
            recipient_username=user.username,
            notification_type=notification_type,
            entity_type="TASK",
            entity_id=str(task.id),
        ):
            continue
        notification_service.notify(
            recipient_username=user.username,
            notification_type=notification_type,
            title=title,
            entity_type="TASK",
            entity_id=str(task.id),
        )
        sent += 1
    return sent


def run_escalations() -> dict[str, int]:
    today = date.today()
    counts = {DUE_TOMORROW: 0, OVERDUE_1D: 0, OVERDUE_3D_EA: 0, OVERDUE_7D_MD: 0}

    for task in _open_tasks_with_due_date():
        age = (today - task.due_date).days  # negative = not yet due

        if age == -1:
            counts[DUE_TOMORROW] += _notify_assignees_once(task, DUE_TOMORROW, f'"{task.title}" is due tomorrow')
        elif age == 1:
            counts[OVERDUE_1D] += _notify_assignees_once(task, OVERDUE_1D, f'"{task.title}" is overdue by 1 day')
        elif age == 3:
            counts[OVERDUE_3D_EA] += _notify_role_once(
                task, UserRole.EA, OVERDUE_3D_EA, f'"{task.title}" is overdue by 3 days'
            )
        elif age >= 7:
            counts[OVERDUE_7D_MD] += _notify_role_once(
                task, UserRole.MD, OVERDUE_7D_MD, f'"{task.title}" is overdue by {age} days'
            )

    if any(counts.values()):
        db.session.commit()
    return counts
