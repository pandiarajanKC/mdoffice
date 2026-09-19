"""Shared Task business logic — used by Meeting Actions now, and by Project
Tasks / General Follow-ups in later phases, per the unified Task model.
"""
from __future__ import annotations

from datetime import date

from app.extensions import db
from app.models.activity_log import ActivityLog
from app.models.meeting import Meeting
from app.models.mixins import utcnow
from app.models.project import Project
from app.models.task import Task, TaskAssignee, TaskPriority, TaskStatus, TaskType
from app.models.task_progress_update import TaskProgressUpdate
from app.services import notification_service


class TaskValidationError(Exception):
    pass


def create_meeting_action(
    meeting: Meeting,
    *,
    title: str,
    employee_codes: list[str],
    created_by: str,
    description: str | None = None,
    priority: TaskPriority = TaskPriority.MEDIUM,
    due_date: date | None = None,
) -> Task:
    title = (title or "").strip()
    if not title:
        raise TaskValidationError("Action title is required.")
    if not employee_codes:
        raise TaskValidationError("At least one employee must be assigned.")

    task = Task(
        task_type=TaskType.MEETING_ACTION,
        title=title,
        description=(description or "").strip() or None,
        meeting_id=meeting.id,
        priority=priority,
        status=TaskStatus.OPEN,
        due_date=due_date,
        created_by=created_by,
    )
    db.session.add(task)
    db.session.flush()

    for code in dict.fromkeys(employee_codes):  # de-duplicate, keep order
        db.session.add(TaskAssignee(task_id=task.id, employee_code=code, assigned_by=created_by))

    ActivityLog.record(
        entity_type="TASK",
        entity_id=str(task.id),
        action_type="TASK_CREATED",
        performed_by=created_by,
        new_value=f"assigned to: {', '.join(employee_codes)}",
    )
    _notify_assignment(task, employee_codes)
    db.session.commit()
    return task


def create_project_task(
    project: Project,
    *,
    title: str,
    employee_codes: list[str],
    created_by: str,
    description: str | None = None,
    priority: TaskPriority = TaskPriority.MEDIUM,
    start_date: date | None = None,
    due_date: date | None = None,
) -> Task:
    title = (title or "").strip()
    if not title:
        raise TaskValidationError("Task title is required.")
    if not employee_codes:
        raise TaskValidationError("At least one employee must be assigned.")

    task = Task(
        task_type=TaskType.PROJECT_TASK,
        title=title,
        description=(description or "").strip() or None,
        project_id=project.id,
        priority=priority,
        status=TaskStatus.OPEN,
        start_date=start_date,
        due_date=due_date,
        created_by=created_by,
    )
    db.session.add(task)
    db.session.flush()

    for code in dict.fromkeys(employee_codes):
        db.session.add(TaskAssignee(task_id=task.id, employee_code=code, assigned_by=created_by))

    ActivityLog.record(
        entity_type="TASK",
        entity_id=str(task.id),
        action_type="TASK_CREATED",
        performed_by=created_by,
        new_value=f"assigned to: {', '.join(employee_codes)}",
    )
    _notify_assignment(task, employee_codes)
    db.session.commit()

    _sync_project_progress(project)
    return task


def create_general_followup(
    *,
    title: str,
    employee_codes: list[str],
    created_by: str,
    description: str | None = None,
    priority: TaskPriority = TaskPriority.MEDIUM,
    due_date: date | None = None,
) -> Task:
    """A task with no meeting or project behind it — e.g. one created from
    the standalone Smart Action Extractor (spec section 30), where an EA
    pastes text from an email/WhatsApp/etc. with no meeting attached.
    """
    title = (title or "").strip()
    if not title:
        raise TaskValidationError("Action title is required.")
    if not employee_codes:
        raise TaskValidationError("At least one employee must be assigned.")

    task = Task(
        task_type=TaskType.GENERAL_FOLLOWUP,
        title=title,
        description=(description or "").strip() or None,
        priority=priority,
        status=TaskStatus.OPEN,
        due_date=due_date,
        created_by=created_by,
    )
    db.session.add(task)
    db.session.flush()

    for code in dict.fromkeys(employee_codes):
        db.session.add(TaskAssignee(task_id=task.id, employee_code=code, assigned_by=created_by))

    ActivityLog.record(
        entity_type="TASK",
        entity_id=str(task.id),
        action_type="TASK_CREATED",
        performed_by=created_by,
        new_value=f"assigned to: {', '.join(employee_codes)}",
    )
    _notify_assignment(task, employee_codes)
    db.session.commit()
    return task


def _sync_project_progress(project: Project | None) -> None:
    if project is None:
        return
    from app.services import project_service

    project_service.recalculate_progress(project)


def _notify_assignment(task: Task, employee_codes: list[str]) -> None:
    notification_service.notify_many(
        employee_codes,
        notification_type="TASK_ASSIGNED",
        title=f'You were assigned: "{task.title}"',
        entity_type="TASK",
        entity_id=str(task.id),
    )


def update_status(task: Task, *, new_status: TaskStatus, performed_by: str) -> Task:
    old_status = task.status
    if old_status == new_status:
        return task

    task.status = new_status
    task.updated_by = performed_by
    if new_status == TaskStatus.COMPLETED:
        task.completed_at = utcnow()
        task.progress_percent = 100
    elif old_status == TaskStatus.COMPLETED:
        task.completed_at = None

    ActivityLog.record(
        entity_type="TASK",
        entity_id=str(task.id),
        action_type="STATUS_CHANGED",
        performed_by=performed_by,
        old_value=old_status.value,
        new_value=new_status.value,
    )
    _notify_status_change(task, new_status=new_status, performed_by=performed_by)
    db.session.commit()
    if task.task_type == TaskType.PROJECT_TASK:
        _sync_project_progress(task.project)
    return task


def _notify_status_change(task: Task, *, new_status: TaskStatus, performed_by: str) -> None:
    if new_status == TaskStatus.COMPLETED:
        if task.created_by != performed_by:
            notification_service.notify(
                recipient_username=task.created_by,
                notification_type="TASK_COMPLETED",
                title=f'Completed: "{task.title}"',
                entity_type="TASK",
                entity_id=str(task.id),
            )
        return

    other_assignees = [a.employee_code for a in task.assignees if a.employee_code != performed_by]
    notification_service.notify_many(
        other_assignees,
        notification_type="STATUS_CHANGED",
        title=f'Status changed to {new_status.value.replace("_", " ").title()}: "{task.title}"',
        entity_type="TASK",
        entity_id=str(task.id),
    )


def add_progress_update(
    task: Task,
    *,
    update_text: str,
    performed_by: str,
    employee_code: str | None = None,
    progress_percent: int | None = None,
) -> TaskProgressUpdate:
    update_text = (update_text or "").strip()
    if not update_text:
        raise TaskValidationError("Update text is required.")

    entry = TaskProgressUpdate(
        task_id=task.id,
        employee_code=employee_code,
        performed_by=performed_by,
        update_text=update_text,
        progress_percent=progress_percent,
    )
    db.session.add(entry)
    ActivityLog.record(
        entity_type="TASK", entity_id=str(task.id), action_type="COMMENT_ADDED", performed_by=performed_by
    )
    db.session.commit()

    if progress_percent is not None:
        update_progress(task, progress_percent=progress_percent, performed_by=performed_by)

    return entry


def update_progress(task: Task, *, progress_percent: int, performed_by: str) -> Task:
    progress_percent = max(0, min(100, progress_percent))
    task.progress_percent = progress_percent
    task.updated_by = performed_by
    if progress_percent == 100 and task.status != TaskStatus.COMPLETED:
        task.status = TaskStatus.COMPLETED
        task.completed_at = utcnow()
    ActivityLog.record(
        entity_type="TASK",
        entity_id=str(task.id),
        action_type="PROGRESS_UPDATED",
        performed_by=performed_by,
        new_value=f"{progress_percent}%",
    )
    db.session.commit()
    if task.task_type == TaskType.PROJECT_TASK:
        _sync_project_progress(task.project)
    return task


def nudge_assignees(task: Task, *, sent_by: str) -> int:
    """Immediately notify every assignee to follow up — the EA's one-click
    alternative to chasing someone manually. Unlike a scheduled Reminder,
    this fires right away and isn't persisted as its own record.
    """
    recipients = [a.employee_code for a in task.assignees]
    if not recipients:
        raise TaskValidationError("This task has no assignees to nudge.")

    notification_service.notify_many(
        recipients,
        notification_type="NUDGE",
        title=f"Reminder to follow up: {task.title}",
        message=f"{sent_by} is checking in on this task.",
        entity_type="TASK",
        entity_id=str(task.id),
    )
    ActivityLog.record(entity_type="TASK", entity_id=str(task.id), action_type="NUDGED", performed_by=sent_by)
    db.session.commit()
    return len(recipients)


def update_task_details(
    task: Task,
    *,
    title: str,
    employee_codes: list[str],
    performed_by: str,
    description: str | None = None,
    priority: TaskPriority = TaskPriority.MEDIUM,
    due_date: date | None = None,
) -> Task:
    """Edit a task/action item's own fields and reassign it — works the
    same regardless of task_type (meeting action, project task, or
    standalone), since all three share this one Task shape.
    """
    title = (title or "").strip()
    if not title:
        raise TaskValidationError("Title is required.")
    if not employee_codes:
        raise TaskValidationError("At least one employee must be assigned.")

    task.title = title
    task.description = (description or "").strip() or None
    task.priority = priority
    task.due_date = due_date
    task.updated_by = performed_by

    new_codes = list(dict.fromkeys(employee_codes))  # de-duplicate, keep order
    existing_codes = {a.employee_code for a in task.assignees}
    for assignee in list(task.assignees):
        if assignee.employee_code not in new_codes:
            db.session.delete(assignee)
    for code in new_codes:
        if code not in existing_codes:
            db.session.add(TaskAssignee(task_id=task.id, employee_code=code, assigned_by=performed_by))

    ActivityLog.record(entity_type="TASK", entity_id=str(task.id), action_type="TASK_EDITED", performed_by=performed_by)
    db.session.commit()
    return task


def delete_task(task: Task, *, performed_by: str) -> None:
    """Permanently removes a task/action item. TaskAssignee and Reminder
    rows cascade automatically (see Task.assignees / Reminder.task);
    TaskProgressUpdate has no ORM-level cascade configured, so it's
    deleted explicitly first to avoid an FK violation on commit.
    """
    task_id = task.id
    TaskProgressUpdate.query.filter_by(task_id=task_id).delete()
    db.session.delete(task)
    ActivityLog.record(entity_type="TASK", entity_id=str(task_id), action_type="TASK_DELETED", performed_by=performed_by)
    db.session.commit()
