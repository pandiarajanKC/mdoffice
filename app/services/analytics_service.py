"""Shared read-only analytics queries for the MD and EA dashboards
(master spec sections 20-21). Every figure here is computed from real
records — no fabricated numbers, per the project's own ground rules.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from app.extensions import db
from app.models.meeting import Meeting, MeetingStatus
from app.models.project import Project, ProjectStatus
from app.models.task import Task, TaskAssignee, TaskPriority, TaskStatus, TaskType
from app.repositories.employee_repository import EmployeeRepository
from app.services import meeting_service
from app.utils.task_display import task_source_label


def task_status_distribution() -> dict[str, int]:
    rows = db.session.query(Task.status, db.func.count(Task.id)).group_by(Task.status).all()
    counts = {s.value: 0 for s in TaskStatus}
    for status, count in rows:
        counts[status.value] = count
    return counts


def project_status_distribution() -> dict[str, int]:
    rows = db.session.query(Project.status, db.func.count(Project.id)).group_by(Project.status).all()
    counts = {s.value: 0 for s in ProjectStatus}
    for status, count in rows:
        counts[status.value] = count
    return counts


def employee_workload(employee_repository: EmployeeRepository, limit: int = 10) -> list[dict]:
    """Employee | Open | In Progress | Overdue — top N by total open load."""
    today = date.today()
    rows = (
        db.session.query(
            TaskAssignee.employee_code,
            db.func.sum(db.case((Task.status == TaskStatus.OPEN, 1), else_=0)),
            db.func.sum(db.case((Task.status == TaskStatus.IN_PROGRESS, 1), else_=0)),
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

    scored = sorted(rows, key=lambda r: (r[1] or 0) + (r[2] or 0), reverse=True)[:limit]
    codes = [r[0] for r in scored]
    profiles = employee_repository.get_by_codes(codes)

    return [
        {
            "employee_code": code,
            "name": profiles[code].name if code in profiles else code,
            "open": open_count or 0,
            "in_progress": in_progress or 0,
            "overdue": overdue or 0,
        }
        for code, open_count, in_progress, overdue in scored
    ]


def meetings_with_pending_followup(limit: int = 10) -> list[dict]:
    """Completed meetings whose actions are not all wrapped up yet."""
    meetings = (
        Meeting.query.filter(Meeting.status == MeetingStatus.COMPLETED)
        .order_by(Meeting.completed_at.desc())
        .limit(50)
        .all()
    )
    pending = []
    for meeting in meetings:
        status = meeting_service.followup_status(meeting)
        if status not in ("No Actions", "All Actions Completed"):
            pending.append({"meeting": meeting, "followup_status": status})
        if len(pending) >= limit:
            break
    return pending


def recently_completed_tasks(limit: int = 8, *, priority: TaskPriority | None = None) -> list[Task]:
    query = Task.query.filter(Task.status == TaskStatus.COMPLETED)
    if priority is not None:
        query = query.filter(Task.priority == priority)
    return query.order_by(Task.completed_at.desc()).limit(limit).all()


def todays_meetings() -> list[Meeting]:
    today = date.today()
    day_start = datetime.combine(today, time.min)
    day_end = day_start + timedelta(days=1)
    return (
        Meeting.query.filter(Meeting.start_datetime >= day_start, Meeting.start_datetime < day_end)
        .order_by(Meeting.start_datetime.asc())
        .all()
    )


def overdue_tasks(employee_repository: EmployeeRepository, limit: int = 8) -> dict:
    """Open/in-progress tasks past their due date, most overdue first.

    Returns {"total": <true overdue count>, "items": <top `limit`, decorated>}
    so the dashboard can show an accurate count even when the list itself
    is capped. MD/EA see every task's true source (task_source_label with
    viewer_is_employee=False) since confidentiality masking only applies
    to employee viewers, never to the MD/EA who manage those meetings.
    """
    today = date.today()
    base_query = Task.query.filter(
        Task.due_date.isnot(None),
        Task.due_date < today,
        Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
    )
    total = base_query.count()
    tasks = base_query.order_by(Task.due_date.asc()).limit(limit).all()

    codes = {a.employee_code for t in tasks for a in t.assignees}
    profiles = employee_repository.get_by_codes(list(codes)) if codes else {}

    rows = []
    for t in tasks:
        names = [profiles[a.employee_code].name if a.employee_code in profiles else a.employee_code for a in t.assignees]
        rows.append({
            "task": t,
            "source_label": task_source_label(t, viewer_is_employee=False),
            "assignees": ", ".join(names) if names else "Unassigned",
            "days_overdue": (today - t.due_date).days,
        })
    # "rows", not "items" — a dict's .items() shadows a same-named key when
    # Jinja does attribute-then-item lookup (`overdue.items` would silently
    # resolve to the bound method, not this list).
    return {"total": total, "rows": rows}


def todays_and_overdue_tasks(employee_repository: EmployeeRepository, limit: int = 8) -> dict:
    """Open/in-progress tasks due today OR already overdue, for the
    dashboard's "Today's Tasks & Overdue Tasks" card — a superset of
    overdue_tasks() above (kept separate since assistant_service still
    needs a pure overdue count/list for AURA's grounding data).

    Sorted by due_date ascending: overdue tasks (due before today) sort
    ahead of today's on their own, since an earlier date always compares
    smaller — no separate ordering logic needed for the two groups.
    """
    today = date.today()
    base_query = Task.query.filter(
        Task.due_date.isnot(None),
        Task.due_date <= today,
        Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
    )
    total = base_query.count()
    tasks = base_query.order_by(Task.due_date.asc()).limit(limit).all()

    codes = {a.employee_code for t in tasks for a in t.assignees}
    profiles = employee_repository.get_by_codes(list(codes)) if codes else {}

    rows = []
    for t in tasks:
        names = [profiles[a.employee_code].name if a.employee_code in profiles else a.employee_code for a in t.assignees]
        is_overdue = t.due_date < today
        rows.append({
            "task": t,
            "source_label": task_source_label(t, viewer_is_employee=False),
            "assignees": ", ".join(names) if names else "Unassigned",
            "is_overdue": is_overdue,
            "days_overdue": (today - t.due_date).days if is_overdue else 0,
        })
    return {"total": total, "rows": rows}


def task_ageing_buckets() -> dict[str, int]:
    """Master spec section 65 — age = today - created date, for open tasks only."""
    today = date.today()
    open_tasks = Task.query.filter(Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED])).all()
    buckets = {"0-7": 0, "8-15": 0, "16-30": 0, "31-60": 0, "60+": 0}
    for task in open_tasks:
        age = (today - task.created_at.date()).days
        if age <= 7:
            buckets["0-7"] += 1
        elif age <= 15:
            buckets["8-15"] += 1
        elif age <= 30:
            buckets["16-30"] += 1
        elif age <= 60:
            buckets["31-60"] += 1
        else:
            buckets["60+"] += 1
    return buckets
