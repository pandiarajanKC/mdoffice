"""Project business logic: create, team management, meeting tagging,
progress recalculation, and the Green/Amber/Red health indicator
(master spec section 63).
"""
from __future__ import annotations

from datetime import date, timedelta

from app.extensions import db
from app.models.activity_log import ActivityLog
from app.models.meeting import Meeting
from app.models.meeting_project import MeetingProject
from app.models.mixins import utcnow
from app.models.project import Project, ProjectMember, ProjectStatus
from app.models.task import Task, TaskPriority, TaskStatus, TaskType
from app.services import notification_service, task_service

HEALTH_APPROACHING_DAYS = 7


class ProjectValidationError(Exception):
    pass


def generate_project_code(name: str) -> str:
    """Best-effort readable code: first letters of words + a numeric suffix
    that avoids collisions. EA can still type over it before saving.
    """
    letters = "".join(w[0] for w in name.split() if w)[:4].upper() or "PRJ"
    existing = {p.project_code for p in Project.query.filter(Project.project_code.like(f"{letters}%")).all()}
    n = 1
    while f"{letters}-{n:03d}" in existing:
        n += 1
    return f"{letters}-{n:03d}"


def create_project(
    *,
    project_code: str,
    name: str,
    owner_employee_code: str,
    created_by: str,
    description: str | None = None,
    start_date: date | None = None,
    target_date: date | None = None,
    priority: TaskPriority = TaskPriority.MEDIUM,
) -> Project:
    name = (name or "").strip()
    project_code = (project_code or "").strip().upper()
    if not name:
        raise ProjectValidationError("Project name is required.")
    if not project_code:
        raise ProjectValidationError("Project code is required.")
    if not owner_employee_code:
        raise ProjectValidationError("Project owner is required.")
    if Project.query.filter_by(project_code=project_code).first():
        raise ProjectValidationError(f"Project code '{project_code}' is already in use.")

    project = Project(
        project_code=project_code,
        name=name,
        description=(description or "").strip() or None,
        owner_employee_code=owner_employee_code,
        start_date=start_date,
        target_date=target_date,
        priority=priority,
        status=ProjectStatus.OPEN,
        created_by=created_by,
    )
    db.session.add(project)
    db.session.flush()
    ActivityLog.record(
        entity_type="PROJECT", entity_id=str(project.id), action_type="PROJECT_CREATED", performed_by=created_by
    )
    db.session.commit()
    return project


def update_status(project: Project, *, new_status: ProjectStatus, performed_by: str) -> Project:
    old_status = project.status
    if old_status == new_status:
        return project
    project.status = new_status
    project.updated_by = performed_by
    if new_status == ProjectStatus.COMPLETED:
        project.completed_at = utcnow()
    elif old_status == ProjectStatus.COMPLETED:
        project.completed_at = None
    ActivityLog.record(
        entity_type="PROJECT",
        entity_id=str(project.id),
        action_type="STATUS_CHANGED",
        performed_by=performed_by,
        old_value=old_status.value,
        new_value=new_status.value,
    )
    db.session.commit()
    return project


def update_project_details(
    project: Project,
    *,
    name: str,
    owner_employee_code: str,
    performed_by: str,
    description: str | None = None,
    start_date: date | None = None,
    target_date: date | None = None,
    priority: TaskPriority = TaskPriority.MEDIUM,
) -> Project:
    name = (name or "").strip()
    if not name:
        raise ProjectValidationError("Project name is required.")
    if not owner_employee_code:
        raise ProjectValidationError("Project owner is required.")

    project.name = name
    project.description = (description or "").strip() or None
    project.owner_employee_code = owner_employee_code
    project.start_date = start_date
    project.target_date = target_date
    project.priority = priority
    project.updated_by = performed_by

    ActivityLog.record(entity_type="PROJECT", entity_id=str(project.id), action_type="PROJECT_EDITED", performed_by=performed_by)
    db.session.commit()
    return project


def delete_project(project: Project, *, performed_by: str) -> None:
    """Permanently removes a project and everything that hangs off it.
    ProjectMember rows cascade automatically (see Project.members); project
    tasks and meeting-tag links have plain FKs with no ORM-level cascade,
    so they're removed explicitly first to avoid a constraint violation on
    commit — project tasks go through task_service.delete_task so their
    own assignees/reminders/progress-updates are cleaned up too.
    """
    project_id = project.id
    for task in list(project.tasks):
        if task.task_type == TaskType.PROJECT_TASK:
            task_service.delete_task(task, performed_by=performed_by)

    MeetingProject.query.filter_by(project_id=project_id).delete()

    db.session.delete(project)
    ActivityLog.record(entity_type="PROJECT", entity_id=str(project_id), action_type="PROJECT_DELETED", performed_by=performed_by)
    db.session.commit()


def add_member(project: Project, *, employee_code: str, role: str, added_by: str) -> ProjectMember:
    if any(m.employee_code == employee_code for m in project.members):
        raise ProjectValidationError("This employee is already on the project team.")
    member = ProjectMember(
        project_id=project.id, employee_code=employee_code, role=(role or "Member").strip() or "Member", added_by=added_by
    )
    db.session.add(member)
    ActivityLog.record(
        entity_type="PROJECT", entity_id=str(project.id), action_type="MEMBER_ADDED", performed_by=added_by, new_value=employee_code
    )
    notification_service.notify(
        recipient_username=employee_code,
        notification_type="PROJECT_ASSIGNED",
        title=f'You were added to project "{project.name}"',
        entity_type="PROJECT",
        entity_id=str(project.id),
    )
    db.session.commit()
    return member


def remove_member(project: Project, member: ProjectMember, *, performed_by: str) -> None:
    employee_code = member.employee_code
    db.session.delete(member)
    ActivityLog.record(
        entity_type="PROJECT", entity_id=str(project.id), action_type="MEMBER_REMOVED", performed_by=performed_by, old_value=employee_code
    )
    db.session.commit()


def tag_meeting(project: Project, meeting: Meeting, *, performed_by: str) -> MeetingProject:
    existing = MeetingProject.query.filter_by(project_id=project.id, meeting_id=meeting.id).first()
    if existing:
        raise ProjectValidationError("This meeting is already tagged to the project.")
    link = MeetingProject(project_id=project.id, meeting_id=meeting.id, tagged_by=performed_by)
    db.session.add(link)
    ActivityLog.record(
        entity_type="PROJECT", entity_id=str(project.id), action_type="MEETING_TAGGED", performed_by=performed_by, new_value=meeting.title
    )
    db.session.commit()
    return link


def untag_meeting(link: MeetingProject, *, performed_by: str) -> None:
    project_id = link.project_id
    db.session.delete(link)
    ActivityLog.record(
        entity_type="PROJECT", entity_id=str(project_id), action_type="MEETING_UNTAGGED", performed_by=performed_by
    )
    db.session.commit()


def recalculate_progress(project: Project) -> Project:
    """Progress is derived from project task completion — never hand-typed,
    so it can never drift from reality (master spec section 76).
    """
    tasks = [t for t in project.tasks if t.task_type == TaskType.PROJECT_TASK]
    if tasks:
        completed = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED)
        project.progress_percent = round(completed / len(tasks) * 100)
    else:
        project.progress_percent = 0
    db.session.commit()
    return project


def health(project: Project) -> str:
    """GREEN / AMBER / RED per master spec section 63."""
    if project.status in (ProjectStatus.COMPLETED, ProjectStatus.CANCELLED):
        return "GREEN"

    tasks = [t for t in project.tasks if t.task_type == TaskType.PROJECT_TASK]
    today = date.today()

    has_overdue_critical_high = any(
        t.is_overdue and t.priority in (TaskPriority.CRITICAL, TaskPriority.HIGH) for t in tasks
    )
    target_missed = bool(project.target_date and project.target_date < today and project.status != ProjectStatus.COMPLETED)
    if has_overdue_critical_high or target_missed:
        return "RED"

    has_overdue_medium = any(t.is_overdue and t.priority == TaskPriority.MEDIUM for t in tasks)
    approaching_target = bool(
        project.target_date and today <= project.target_date <= today + timedelta(days=HEALTH_APPROACHING_DAYS)
    )
    if has_overdue_medium or approaching_target:
        return "AMBER"

    return "GREEN"
