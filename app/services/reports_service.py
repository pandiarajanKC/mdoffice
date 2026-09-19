"""Report data computation (master spec section 39). Every function
returns real numbers computed from actual records — no placeholders.

Each report function returns ``(summary: dict | None, rows: list[dict])``
so the same data can drive both the HTML table and the Excel export
without recomputing anything.
"""
from __future__ import annotations

from datetime import date

from app.extensions import db
from app.models.meeting import Meeting, MeetingStatus
from app.models.project import Project
from app.models.task import Task, TaskAssignee, TaskStatus, TaskType
from app.repositories.employee_repository import EmployeeRepository
from app.services import analytics_service, meeting_service, project_service
from app.utils.task_display import task_source_label

AGEING_BUCKETS = ["0-7", "8-15", "16-30", "31-60", "60+"]


def _assignee_names(task: Task, employee_repository: EmployeeRepository) -> str:
    codes = [a.employee_code for a in task.assignees]
    profiles = employee_repository.get_by_codes(codes)
    return ", ".join(profiles[c].name if c in profiles else c for c in codes) or "Unassigned"


def action_status_report(employee_repository: EmployeeRepository):
    dist = analytics_service.task_status_distribution()
    total = sum(dist.values()) or 1
    rows = [
        {"status": status, "count": count, "percent": round(count / total * 100, 1)}
        for status, count in dist.items()
    ]
    return {"total": sum(dist.values())}, rows


def employee_pending_report(employee_repository: EmployeeRepository):
    today = date.today()
    query_rows = (
        db.session.query(
            TaskAssignee.employee_code,
            db.func.sum(db.case((Task.status == TaskStatus.OPEN, 1), else_=0)),
            db.func.sum(db.case((Task.status == TaskStatus.IN_PROGRESS, 1), else_=0)),
            db.func.sum(db.case((Task.status == TaskStatus.ON_HOLD, 1), else_=0)),
            db.func.sum(
                db.case(
                    (
                        db.and_(
                            Task.due_date.isnot(None),
                            Task.due_date < today,
                            Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
        )
        .join(Task, Task.id == TaskAssignee.task_id)
        .filter(Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]))
        .group_by(TaskAssignee.employee_code)
        .all()
    )

    codes = [r[0] for r in query_rows]
    profiles = employee_repository.get_by_codes(codes)

    rows = []
    for code, open_c, in_progress, on_hold, overdue in query_rows:
        profile = profiles.get(code)
        rows.append(
            {
                "employee_code": code,
                "name": profile.name if profile else code,
                "department": profile.department if profile else None,
                "open": open_c or 0,
                "in_progress": in_progress or 0,
                "on_hold": on_hold or 0,
                "overdue": overdue or 0,
                "total_pending": (open_c or 0) + (in_progress or 0) + (on_hold or 0),
            }
        )
    rows.sort(key=lambda r: r["total_pending"], reverse=True)
    return {"employees_with_pending": len(rows)}, rows


def overdue_actions_report(employee_repository: EmployeeRepository):
    today = date.today()
    tasks = (
        Task.query.filter(
            Task.due_date.isnot(None),
            Task.due_date < today,
            Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
        )
        .order_by(Task.due_date.asc())
        .all()
    )
    rows = [
        {
            "title": t.title,
            "source": task_source_label(t, viewer_is_employee=False),
            "assignees": _assignee_names(t, employee_repository),
            "priority": t.priority.value,
            "due_date": t.due_date,
            "days_overdue": (today - t.due_date).days,
            "status": t.status.value,
        }
        for t in tasks
    ]
    return {"total_overdue": len(rows)}, rows


def meeting_actions_report(employee_repository: EmployeeRepository):
    meetings = Meeting.query.order_by(Meeting.start_datetime.desc()).all()
    rows = []
    for m in meetings:
        actions = [t for t in m.tasks if t.task_type == TaskType.MEETING_ACTION]
        if not actions:
            continue
        rows.append(
            {
                "meeting": m.title,
                "date": m.start_datetime.date(),
                "total_actions": len(actions),
                "open": sum(1 for t in actions if t.status == TaskStatus.OPEN),
                "in_progress": sum(1 for t in actions if t.status == TaskStatus.IN_PROGRESS),
                "completed": sum(1 for t in actions if t.status == TaskStatus.COMPLETED),
                "overdue": sum(1 for t in actions if t.is_overdue),
                "followup_status": meeting_service.followup_status(m),
            }
        )
    return {"meetings_with_actions": len(rows)}, rows


def project_actions_report(employee_repository: EmployeeRepository):
    projects = Project.query.order_by(Project.name.asc()).all()
    rows = []
    for p in projects:
        tasks = [t for t in p.tasks if t.task_type == TaskType.PROJECT_TASK]
        rows.append(
            {
                "project": p.name,
                "code": p.project_code,
                "total_tasks": len(tasks),
                "open": sum(1 for t in tasks if t.status == TaskStatus.OPEN),
                "in_progress": sum(1 for t in tasks if t.status == TaskStatus.IN_PROGRESS),
                "completed": sum(1 for t in tasks if t.status == TaskStatus.COMPLETED),
                "overdue": sum(1 for t in tasks if t.is_overdue),
                "progress_percent": p.progress_percent,
            }
        )
    return {"total_projects": len(rows)}, rows


def completed_actions_report(employee_repository: EmployeeRepository):
    tasks = Task.query.filter(Task.status == TaskStatus.COMPLETED).order_by(Task.completed_at.desc()).all()
    rows = []
    for t in tasks:
        days_to_complete = (t.completed_at.date() - t.created_at.date()).days if t.completed_at else None
        rows.append(
            {
                "title": t.title,
                "source": task_source_label(t, viewer_is_employee=False),
                "assignees": _assignee_names(t, employee_repository),
                "completed_date": t.completed_at.date() if t.completed_at else None,
                "days_to_complete": days_to_complete,
            }
        )
    return {"total_completed": len(rows)}, rows


def ageing_report(employee_repository: EmployeeRepository):
    today = date.today()
    buckets = analytics_service.task_ageing_buckets()
    open_tasks = Task.query.filter(Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED])).all()
    rows = [
        {
            "title": t.title,
            "source": task_source_label(t, viewer_is_employee=False),
            "assignees": _assignee_names(t, employee_repository),
            "priority": t.priority.value,
            "status": t.status.value,
            "age_days": (today - t.created_at.date()).days,
        }
        for t in open_tasks
    ]
    rows.sort(key=lambda r: r["age_days"], reverse=True)
    return buckets, rows


def employee_completion_rate_report(employee_repository: EmployeeRepository):
    all_assignments = TaskAssignee.query.all()
    by_employee: dict[str, list[Task]] = {}
    for a in all_assignments:
        by_employee.setdefault(a.employee_code, []).append(a.task)

    profiles = employee_repository.get_by_codes(list(by_employee.keys()))
    rows = []
    for code, tasks in by_employee.items():
        total = len(tasks)
        completed = [t for t in tasks if t.status == TaskStatus.COMPLETED]
        on_time = [t for t in completed if t.due_date and t.completed_at and t.completed_at.date() <= t.due_date]
        with_due = [t for t in completed if t.due_date]
        completion_rate = round(len(completed) / total * 100, 1) if total else 0.0
        on_time_rate = round(len(on_time) / len(with_due) * 100, 1) if with_due else None
        avg_days = (
            round(sum((t.completed_at.date() - t.created_at.date()).days for t in completed) / len(completed), 1)
            if completed
            else None
        )
        profile = profiles.get(code)
        rows.append(
            {
                "employee_code": code,
                "name": profile.name if profile else code,
                "department": profile.department if profile else None,
                "total_assigned": total,
                "completed": len(completed),
                "completion_rate": completion_rate,
                "on_time_rate": on_time_rate,
                "avg_days_to_complete": avg_days,
            }
        )
    rows.sort(key=lambda r: r["completion_rate"], reverse=True)
    return {"employees_tracked": len(rows)}, rows


def project_progress_report(employee_repository: EmployeeRepository):
    projects = Project.query.order_by(Project.created_at.desc()).all()
    codes = [p.owner_employee_code for p in projects]
    profiles = employee_repository.get_by_codes(codes)
    rows = []
    for p in projects:
        profile = profiles.get(p.owner_employee_code)
        rows.append(
            {
                "code": p.project_code,
                "project": p.name,
                "owner": profile.name if profile else p.owner_employee_code,
                "status": p.status.value,
                "priority": p.priority.value,
                "progress_percent": p.progress_percent,
                "health": project_service.health(p),
                "target_date": p.target_date,
            }
        )
    return {"total_projects": len(rows)}, rows


def meeting_compliance_report(employee_repository: EmployeeRepository):
    meetings = (
        Meeting.query.filter(Meeting.status == MeetingStatus.COMPLETED)
        .order_by(Meeting.completed_at.desc())
        .all()
    )
    rows = []
    compliant_count = 0
    for m in meetings:
        actions = [t for t in m.tasks if t.task_type == TaskType.MEETING_ACTION]
        followup = meeting_service.followup_status(m)
        compliant = followup in ("No Actions", "All Actions Completed")
        if compliant:
            compliant_count += 1
        rows.append(
            {
                "meeting": m.title,
                "date": m.start_datetime.date(),
                "total_actions": len(actions),
                "completed_actions": sum(1 for t in actions if t.status == TaskStatus.COMPLETED),
                "followup_status": followup,
                "compliant": "Yes" if compliant else "No",
            }
        )
    compliance_rate = round(compliant_count / len(rows) * 100, 1) if rows else None
    return {
        "total_completed_meetings": len(rows),
        "fully_compliant": compliant_count,
        "compliance_rate": compliance_rate,
    }, rows
