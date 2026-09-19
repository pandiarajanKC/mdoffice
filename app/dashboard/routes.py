from datetime import date, datetime, time, timedelta

from flask import current_app, redirect, render_template, url_for
from flask_login import current_user, login_required

from app.dashboard import dashboard_bp
from app.models.app_user import UserRole
from app.models.meeting import Meeting, MeetingStatus
from app.models.project import Project, ProjectStatus
from app.models.task import Task, TaskAssignee, TaskPriority, TaskStatus, TaskType
from app.services import analytics_service
from app.utils.task_display import task_source_label

ROADMAP: list[dict] = []

_PRIORITY_RANK = {TaskPriority.CRITICAL: 0, TaskPriority.HIGH: 1, TaskPriority.MEDIUM: 2, TaskPriority.LOW: 3}


def _pct(numerator: int, denominator: int) -> int:
    return round((numerator / denominator) * 100) if denominator else 0


def _project_summary() -> dict:
    total = Project.query.count()
    new = Project.query.filter(Project.status == ProjectStatus.OPEN).count()
    in_progress = Project.query.filter(Project.status == ProjectStatus.IN_PROGRESS).count()
    completed = Project.query.filter(Project.status == ProjectStatus.COMPLETED).count()
    return {
        "total": total, "new": new, "in_progress": in_progress, "completed": completed,
        "progress_pct": _pct(in_progress, total), "progress_label": "In Progress",
    }


def _action_items_summary() -> dict:
    """Task rows tied to a meeting or a project — MEETING_ACTION and
    PROJECT_TASK. Deliberately excludes standalone tasks (GENERAL_FOLLOWUP,
    see _task_summary() below) so the "Action Items" and "Tasks" cards
    never double-count the same rows between them.
    """
    base = Task.query.filter(Task.task_type != TaskType.GENERAL_FOLLOWUP)
    total = base.count()
    new = base.filter(Task.status == TaskStatus.OPEN).count()
    in_progress = base.filter(Task.status == TaskStatus.IN_PROGRESS).count()
    completed = base.filter(Task.status == TaskStatus.COMPLETED).count()
    return {
        "total": total, "new": new, "in_progress": in_progress, "completed": completed,
        "progress_pct": _pct(in_progress, total), "progress_label": "In Progress",
    }


def _task_summary() -> dict:
    """Standalone tasks only (GENERAL_FOLLOWUP) — same scope as the Tasks
    module page, not tied to any meeting or project.
    """
    base = Task.query.filter(Task.task_type == TaskType.GENERAL_FOLLOWUP)
    total = base.count()
    new = base.filter(Task.status == TaskStatus.OPEN).count()
    in_progress = base.filter(Task.status == TaskStatus.IN_PROGRESS).count()
    completed = base.filter(Task.status == TaskStatus.COMPLETED).count()
    return {
        "total": total, "new": new, "in_progress": in_progress, "completed": completed,
        "progress_pct": _pct(in_progress, total), "progress_label": "In Progress",
    }


def _meeting_summary() -> dict:
    """Today's meetings only (same day-range filter as the Meetings
    module's own "Today" tab — see CHRONOLOGICAL_VIEWS in
    app/meetings/routes.py). "new"/scheduled is still computed for the same
    shape as the other summary cards, but isn't shown on the dashboard
    card itself — see show_new=False in components/summary_cards.html.
    """
    today = date.today()
    day_start = datetime.combine(today, time.min)
    day_end = day_start + timedelta(days=1)
    base = Meeting.query.filter(Meeting.start_datetime >= day_start, Meeting.start_datetime < day_end)

    total = base.count()
    scheduled = base.filter(Meeting.status == MeetingStatus.SCHEDULED).count()
    in_progress = base.filter(Meeting.status == MeetingStatus.IN_PROGRESS).count()
    completed = base.filter(Meeting.status == MeetingStatus.COMPLETED).count()
    return {
        "total": total, "new": scheduled, "in_progress": in_progress, "completed": completed,
        "progress_pct": _pct(completed, total), "progress_label": "Completed",
    }


def _employee_task_stats(employee_code: str) -> dict:
    """Real, employee-scoped counts — only tasks assigned to this employee
    (master spec section 19), never another employee's records.
    """
    today = date.today()
    week_end = today + timedelta(days=7)

    my_tasks = (
        Task.query.join(TaskAssignee)
        .filter(TaskAssignee.employee_code == employee_code)
        .all()
    )

    open_count = sum(1 for t in my_tasks if t.status == TaskStatus.OPEN)
    in_progress = sum(1 for t in my_tasks if t.status == TaskStatus.IN_PROGRESS)
    completed = sum(1 for t in my_tasks if t.status == TaskStatus.COMPLETED)
    on_hold = sum(1 for t in my_tasks if t.status == TaskStatus.ON_HOLD)
    overdue = sum(1 for t in my_tasks if t.is_overdue)
    due_this_week = sum(
        1 for t in my_tasks
        if t.due_date and today <= t.due_date < week_end and t.status not in (TaskStatus.COMPLETED, TaskStatus.CANCELLED)
    )

    open_tasks = [t for t in my_tasks if t.status in (TaskStatus.OPEN, TaskStatus.IN_PROGRESS)]
    priority_tasks = sorted(
        open_tasks, key=lambda t: (_PRIORITY_RANK[t.priority], t.due_date or date.max)
    )[:5]

    upcoming_due = sorted(
        (t for t in my_tasks if t.due_date and t.due_date >= today and t.status not in (TaskStatus.COMPLETED, TaskStatus.CANCELLED)),
        key=lambda t: t.due_date,
    )[:5]

    recently_updated = sorted(my_tasks, key=lambda t: t.updated_at, reverse=True)[:5]

    by_project: dict[str, int] = {}
    by_meeting: dict[str, int] = {}
    for t in my_tasks:
        if t.project_id:
            by_project[t.project.name] = by_project.get(t.project.name, 0) + 1
        elif t.meeting_id:
            label = task_source_label(t, viewer_is_employee=True).removeprefix("Meeting: ")
            by_meeting[label] = by_meeting.get(label, 0) + 1

    return {
        "assigned": len(my_tasks),
        "open": open_count,
        "in_progress": in_progress,
        "completed": completed,
        "on_hold": on_hold,
        "overdue": overdue,
        "due_this_week": due_this_week,
        "priority_tasks": priority_tasks,
        "upcoming_due": upcoming_due,
        "recently_updated": recently_updated,
        "by_project": by_project,
        "by_meeting": by_meeting,
    }


@dashboard_bp.route("/")
@login_required
def index():
    if current_user.role == UserRole.MD:
        return redirect(url_for("dashboard.md_dashboard"))
    if current_user.role == UserRole.EA:
        return redirect(url_for("dashboard.ea_dashboard"))
    return redirect(url_for("dashboard.employee_dashboard"))


@dashboard_bp.route("/md")
@login_required
def md_dashboard():
    if current_user.role != UserRole.MD:
        return redirect(url_for("dashboard.index"))
    employee_repository = current_app.extensions["employee_repository"]
    return render_template(
        "dashboard/md_dashboard.html",
        project_summary=_project_summary(), action_items_summary=_action_items_summary(),
        task_summary=_task_summary(), meeting_summary=_meeting_summary(),
        todays_meetings=analytics_service.todays_meetings(),
        overdue=analytics_service.todays_and_overdue_tasks(employee_repository),
    )


@dashboard_bp.route("/ea")
@login_required
def ea_dashboard():
    if current_user.role != UserRole.EA:
        return redirect(url_for("dashboard.index"))
    employee_repository = current_app.extensions["employee_repository"]
    return render_template(
        "dashboard/ea_dashboard.html",
        project_summary=_project_summary(), action_items_summary=_action_items_summary(),
        task_summary=_task_summary(), meeting_summary=_meeting_summary(),
        todays_meetings=analytics_service.todays_meetings(),
        overdue=analytics_service.todays_and_overdue_tasks(employee_repository),
    )


@dashboard_bp.route("/me")
@login_required
def employee_dashboard():
    if current_user.role != UserRole.EMPLOYEE:
        return redirect(url_for("dashboard.index"))

    employee_repository = current_app.extensions["employee_repository"]
    profile = employee_repository.get_by_code(current_user.employee_code)
    stats = _employee_task_stats(current_user.employee_code)
    return render_template("dashboard/employee_dashboard.html", roadmap=ROADMAP, profile=profile, stats=stats)
