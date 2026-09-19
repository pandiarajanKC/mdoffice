from datetime import date, datetime, timezone

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models.activity_log import ActivityLog
from app.models.app_user import UserRole
from app.models.notification import Reminder
from app.models.task import Task, TaskAssignee, TaskPriority, TaskStatus, TaskType
from app.services import reminder_service, tag_service, task_service
from app.tasks import tasks_bp
from app.tasks.forms import TaskForm
from app.utils.email_draft import pop_email_draft
from app.utils.rbac import roles_required
from app.utils.task_display import task_source_label

TRACKER_MAX_ROWS = 2000  # safety cap; the DataTable handles sort/search/paging client-side
GENERAL_TASKS_MAX_ROWS = 2000


def _get_task_or_404(task_id: int) -> Task:
    task = db.session.get(Task, task_id)
    if task is None:
        abort(404)
    return task


def _is_assigned(task: Task) -> bool:
    return any(a.employee_code == current_user.employee_code for a in task.assignees)


def _can_view(task: Task) -> bool:
    if current_user.role in (UserRole.EA, UserRole.MD):
        return True
    if current_user.role == UserRole.EMPLOYEE:
        return _is_assigned(task)
    return False


def _can_update(task: Task) -> bool:
    if current_user.role == UserRole.EA:
        return True
    if current_user.role == UserRole.EMPLOYEE:
        return _is_assigned(task)
    return False


def _redirect_back(task: Task):
    if request.referrer:
        return redirect(request.referrer)
    return redirect(url_for("tasks.detail", task_id=task.id))


PRIORITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


@tasks_bp.route("/")
@login_required
@roles_required("MD", "EA")
def tracker():
    """Global follow-up dashboard / Action Tracker (master spec section 22).

    Filtering (search/status/priority/source/employee/overdue) stays
    server-side since it needs DB-level joins against the employee master.
    Sorting and paging are handed to the DataTable client-side instead of a
    `sort=`/`page=` query string, since the filtered set is fetched in full
    (up to TRACKER_MAX_ROWS) rather than one page at a time.
    """
    q = (request.args.get("q") or "").strip()
    status_filter = request.args.get("status", "")
    priority_filter = request.args.get("priority", "")
    employee_code = (request.args.get("employee_code") or "").strip()
    source_type = request.args.get("source_type", "")
    overdue_only = request.args.get("overdue_only") == "1"

    # Action items only — meeting actions and project tasks. Standalone
    # tasks (GENERAL_FOLLOWUP) have their own separate Tasks page/card and
    # are deliberately excluded here so the two never show the same rows
    # twice, same split as the dashboard's Action Items vs Tasks summary
    # cards (see app/dashboard/routes.py's _action_items_summary()).
    query = Task.query.filter(Task.task_type != TaskType.GENERAL_FOLLOWUP)
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(Task.title.ilike(like), Task.description.ilike(like)))
    if status_filter:
        query = query.filter(Task.status == TaskStatus(status_filter))
    if priority_filter:
        query = query.filter(Task.priority == TaskPriority(priority_filter))
    if source_type:
        query = query.filter(Task.task_type == TaskType(source_type))
    if employee_code:
        query = query.join(TaskAssignee).filter(TaskAssignee.employee_code == employee_code)
    if overdue_only:
        query = query.filter(
            Task.due_date.isnot(None),
            Task.due_date < date.today(),
            Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
        )

    total = query.count()
    tasks = (
        query.order_by(db.case((Task.due_date.is_(None), 1), else_=0), Task.due_date.asc())
        .limit(TRACKER_MAX_ROWS)
        .all()
    )

    task_ids = [t.id for t in tasks]
    assignees_by_task: dict[int, list[str]] = {tid: [] for tid in task_ids}
    assignee_details_by_task: dict[int, list[dict]] = {tid: [] for tid in task_ids}
    if task_ids:
        assignments = TaskAssignee.query.filter(TaskAssignee.task_id.in_(task_ids)).all()
        employee_repository = current_app.extensions["employee_repository"]
        profiles = employee_repository.get_by_codes([a.employee_code for a in assignments])
        for a in assignments:
            profile = profiles.get(a.employee_code)
            name = profile.name if profile else a.employee_code
            assignees_by_task[a.task_id].append(name)
            assignee_details_by_task[a.task_id].append({"code": a.employee_code, "name": name})

    today = date.today()
    ages = {t.id: (today - t.created_at.date()).days for t in tasks}
    sources = {t.id: task_source_label(t, viewer_is_employee=False) for t in tasks}

    return render_template(
        "tasks/tracker.html",
        tasks=tasks,
        assignees_by_task=assignees_by_task,
        assignee_details_by_task=assignee_details_by_task,
        ages=ages,
        sources=sources,
        total=total,
        shown=len(tasks),
        priority_rank=PRIORITY_RANK,
        q=q,
        status_filter=status_filter,
        priority_filter=priority_filter,
        employee_code=employee_code,
        source_type=source_type,
        overdue_only=overdue_only,
        is_ea=current_user.role == UserRole.EA,
    )


@tasks_bp.route("/general")
@login_required
@roles_required("MD", "EA")
def general_list():
    """Standalone tasks — no meeting or project behind them. An EA's direct
    way to assign ad-hoc work to anyone (master spec's GENERAL_FOLLOWUP
    task type), independent of the Meetings/Projects workflow.
    """
    q = (request.args.get("q") or "").strip()
    status_filter = request.args.get("status", "")
    priority_filter = request.args.get("priority", "")
    employee_code = (request.args.get("employee_code") or "").strip()
    overdue_only = request.args.get("overdue_only") == "1"

    query = Task.query.filter(Task.task_type == TaskType.GENERAL_FOLLOWUP)
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(Task.title.ilike(like), Task.description.ilike(like)))
    if status_filter:
        query = query.filter(Task.status == TaskStatus(status_filter))
    if priority_filter:
        query = query.filter(Task.priority == TaskPriority(priority_filter))
    if employee_code:
        query = query.join(TaskAssignee).filter(TaskAssignee.employee_code == employee_code)
    if overdue_only:
        query = query.filter(
            Task.due_date.isnot(None),
            Task.due_date < date.today(),
            Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
        )

    total = query.count()
    tasks = (
        query.order_by(db.case((Task.due_date.is_(None), 1), else_=0), Task.due_date.asc())
        .limit(GENERAL_TASKS_MAX_ROWS)
        .all()
    )

    task_ids = [t.id for t in tasks]
    assignees_by_task: dict[int, list[str]] = {tid: [] for tid in task_ids}
    assignee_details_by_task: dict[int, list[dict]] = {tid: [] for tid in task_ids}
    if task_ids:
        assignments = TaskAssignee.query.filter(TaskAssignee.task_id.in_(task_ids)).all()
        employee_repository = current_app.extensions["employee_repository"]
        profiles = employee_repository.get_by_codes([a.employee_code for a in assignments])
        for a in assignments:
            profile = profiles.get(a.employee_code)
            name = profile.name if profile else a.employee_code
            assignees_by_task[a.task_id].append(name)
            assignee_details_by_task[a.task_id].append({"code": a.employee_code, "name": name})

    today = date.today()
    ages = {t.id: (today - t.created_at.date()).days for t in tasks}

    return render_template(
        "tasks/general.html",
        tasks=tasks,
        assignees_by_task=assignees_by_task,
        assignee_details_by_task=assignee_details_by_task,
        ages=ages,
        total=total,
        shown=len(tasks),
        priority_rank=PRIORITY_RANK,
        q=q,
        status_filter=status_filter,
        priority_filter=priority_filter,
        employee_code=employee_code,
        overdue_only=overdue_only,
        is_ea=current_user.role == UserRole.EA,
        form=TaskForm(),
        email_draft=pop_email_draft("task", "tracker"),
    )


@tasks_bp.route("/general", methods=["POST"])
@login_required
@roles_required("EA")
def create_general():
    form = TaskForm()
    employee_codes = [c for c in request.form.getlist("employee_codes") if c.strip()]
    # Set only when this task started life as "Convert to Action Tracker"
    # from an email — same creation form as a plain task, the EA just
    # lands somewhere else afterward. See app/utils/email_draft.py.
    land_on_tracker = request.form.get("after_create") == "tracker"

    if not form.validate_on_submit():
        flash("Please provide a task title.", "danger")
        return redirect(url_for("tasks.general_list"))

    try:
        task_service.create_general_followup(
            title=form.title.data,
            description=form.description.data,
            employee_codes=employee_codes,
            priority=TaskPriority(form.priority.data),
            due_date=form.due_date.data,
            created_by=current_user.username,
        )
        flash("Task created.", "success")
    except task_service.TaskValidationError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("tasks.general_list"))

    if land_on_tracker:
        return redirect(url_for("tasks.tracker"))
    return redirect(url_for("tasks.general_list"))


@tasks_bp.route("/mine")
@login_required
def mine():
    if current_user.role != UserRole.EMPLOYEE:
        return redirect(url_for("dashboard.index"))

    status_filter = request.args.get("status", "")
    query = Task.query.join(TaskAssignee).filter(TaskAssignee.employee_code == current_user.employee_code)
    if status_filter:
        query = query.filter(Task.status == TaskStatus(status_filter))

    tasks = query.order_by(db.case((Task.due_date.is_(None), 1), else_=0), Task.due_date.asc()).all()

    return render_template(
        "tasks/mine.html",
        tasks=tasks,
        status_filter=status_filter,
        task_source_label=lambda t: task_source_label(t, viewer_is_employee=True),
    )


@tasks_bp.route("/<int:task_id>")
@login_required
def detail(task_id):
    task = _get_task_or_404(task_id)
    if not _can_view(task):
        abort(403)

    employee_repository = current_app.extensions["employee_repository"]
    assignee_codes = [a.employee_code for a in task.assignees]
    profiles = employee_repository.get_by_codes(assignee_codes)
    assignee_names = [profiles[c].name if c in profiles else c for c in assignee_codes]

    is_employee_viewer = current_user.role == UserRole.EMPLOYEE

    activity_entries = (
        ActivityLog.query.filter_by(entity_type="TASK", entity_id=str(task.id))
        .order_by(ActivityLog.performed_at.desc())
        .limit(30)
        .all()
    )

    return render_template(
        "tasks/detail.html",
        task=task,
        assignee_names=assignee_names,
        source_label=task_source_label(task, viewer_is_employee=is_employee_viewer),
        can_update=_can_update(task),
        is_employee_viewer=is_employee_viewer,
        is_ea=current_user.role == UserRole.EA,
        activity_entries=activity_entries,
        reminders=[r for r in task.reminders if not r.is_dismissed],
        tag_assignments=tag_service.get_assignments_for_entity("TASK", task.id),
        all_tag_names=tag_service.all_tag_names(),
    )


@tasks_bp.route("/<int:task_id>/reminders", methods=["POST"])
@login_required
@roles_required("EA")
def add_reminder(task_id):
    task = _get_task_or_404(task_id)
    raw_datetime = request.form.get("remind_at", "")
    try:
        # The <input type="datetime-local"> value is naive wall-clock time
        # in the browser's local timezone. Every stored timestamp elsewhere
        # in this app is naive UTC (see app.models.mixins.utcnow), so this
        # is the one place that boundary gets crossed: treat the typed
        # value as the *server's* local time (single-office deployment
        # assumption) and convert to naive UTC before it ever reaches the
        # service layer, which always works in UTC.
        local_naive = datetime.fromisoformat(raw_datetime)
        remind_at = local_naive.astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        flash("Please provide a valid reminder date and time.", "danger")
        return _redirect_back(task)

    try:
        reminder_service.create_reminder(task, remind_at=remind_at, note=request.form.get("note"), created_by=current_user.username)
        flash("Reminder set.", "success")
    except reminder_service.ReminderValidationError as exc:
        flash(str(exc), "danger")
    return _redirect_back(task)


@tasks_bp.route("/<int:task_id>/reminders/<int:reminder_id>/dismiss", methods=["POST"])
@login_required
@roles_required("EA")
def dismiss_reminder(task_id, reminder_id):
    reminder = db.session.get(Reminder, reminder_id)
    if reminder is None or reminder.task_id != task_id:
        abort(404)
    reminder_service.dismiss_reminder(reminder, performed_by=current_user.username)
    flash("Reminder dismissed.", "info")
    return _redirect_back(reminder.task)


@tasks_bp.route("/<int:task_id>/nudge", methods=["POST"])
@login_required
@roles_required("EA")
def nudge(task_id):
    task = _get_task_or_404(task_id)
    try:
        count = task_service.nudge_assignees(task, sent_by=current_user.username)
        flash(f"Reminder sent to {count} assignee(s).", "success")
    except task_service.TaskValidationError as exc:
        flash(str(exc), "danger")
    return _redirect_back(task)


@tasks_bp.route("/<int:task_id>/comment", methods=["POST"])
@login_required
def add_comment(task_id):
    task = _get_task_or_404(task_id)
    if not _can_update(task):
        abort(403)

    update_text = request.form.get("update_text", "")
    raw_progress = request.form.get("progress_percent", "").strip()
    progress_percent = None
    if raw_progress:
        try:
            progress_percent = max(0, min(100, int(raw_progress)))
        except ValueError:
            flash("Progress must be a number between 0 and 100.", "danger")
            return _redirect_back(task)

    employee_code = current_user.employee_code if current_user.role == UserRole.EMPLOYEE else None
    try:
        task_service.add_progress_update(
            task,
            update_text=update_text,
            performed_by=current_user.username,
            employee_code=employee_code,
            progress_percent=progress_percent,
        )
        flash("Update added.", "success")
    except task_service.TaskValidationError as exc:
        flash(str(exc), "danger")
    return _redirect_back(task)


@tasks_bp.route("/<int:task_id>/status", methods=["POST"])
@login_required
def update_status(task_id):
    task = _get_task_or_404(task_id)
    if not _can_update(task):
        abort(403)

    raw_status = request.form.get("status", "")
    try:
        new_status = TaskStatus(raw_status)
    except ValueError:
        flash("Invalid status.", "danger")
        return _redirect_back(task)

    task_service.update_status(task, new_status=new_status, performed_by=current_user.username)
    flash("Task status updated.", "success")
    return _redirect_back(task)


@tasks_bp.route("/<int:task_id>/progress", methods=["POST"])
@login_required
def update_progress(task_id):
    task = _get_task_or_404(task_id)
    if not _can_update(task):
        abort(403)

    try:
        progress = int(request.form.get("progress_percent", "0"))
    except ValueError:
        flash("Invalid progress value.", "danger")
        return _redirect_back(task)

    task_service.update_progress(task, progress_percent=progress, performed_by=current_user.username)
    flash("Progress updated.", "success")
    return _redirect_back(task)


@tasks_bp.route("/<int:task_id>/edit", methods=["POST"])
@login_required
@roles_required("EA")
def edit(task_id):
    task = _get_task_or_404(task_id)
    employee_codes = [c for c in request.form.getlist("employee_codes") if c.strip()]

    raw_due_date = (request.form.get("due_date") or "").strip()
    due_date = None
    if raw_due_date:
        try:
            due_date = date.fromisoformat(raw_due_date)
        except ValueError:
            flash("Invalid due date.", "danger")
            return _redirect_back(task)

    try:
        priority = TaskPriority(request.form.get("priority", TaskPriority.MEDIUM.value))
    except ValueError:
        flash("Invalid priority.", "danger")
        return _redirect_back(task)

    try:
        task_service.update_task_details(
            task,
            title=request.form.get("title", ""),
            description=request.form.get("description", ""),
            priority=priority,
            due_date=due_date,
            employee_codes=employee_codes,
            performed_by=current_user.username,
        )
        flash("Task updated.", "success")
    except task_service.TaskValidationError as exc:
        flash(str(exc), "danger")

    return _redirect_back(task)


@tasks_bp.route("/<int:task_id>/delete", methods=["POST"])
@login_required
@roles_required("EA")
def delete(task_id):
    task = _get_task_or_404(task_id)
    task_service.delete_task(task, performed_by=current_user.username)
    flash("Task deleted.", "info")
    if request.referrer:
        return redirect(request.referrer)
    return redirect(url_for("tasks.tracker"))
