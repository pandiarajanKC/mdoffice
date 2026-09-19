from datetime import date

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models.activity_log import ActivityLog
from app.models.app_user import UserRole
from app.models.meeting import Meeting
from app.models.meeting_project import MeetingProject
from app.models.project import Project, ProjectMember, ProjectStatus
from app.models.task import Task, TaskAssignee, TaskPriority, TaskType
from app.projects import projects_bp
from app.projects.forms import MemberForm, ProjectForm, ProjectTaskForm, TagMeetingForm
from app.services import project_service, tag_service, task_service
from app.utils.email_draft import pop_email_draft
from app.utils.rbac import roles_required


def _get_project_or_404(project_id: int) -> Project:
    project = db.session.get(Project, project_id)
    if project is None:
        abort(404)
    return project


def _is_ea() -> bool:
    return current_user.role == UserRole.EA


PROJECTS_MAX_ROWS = 2000  # safety cap; the DataTable handles sort/search/paging client-side
PRIORITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
HEALTH_RANK = {"RED": 0, "AMBER": 1, "GREEN": 2}


@projects_bp.route("/")
@login_required
@roles_required("MD", "EA")
def list_view():
    q = (request.args.get("q") or "").strip()
    status_filter = request.args.get("status", "")
    priority_filter = request.args.get("priority", "")
    owner_code = (request.args.get("owner_employee_code") or "").strip()

    query = Project.query
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(Project.name.ilike(like), Project.project_code.ilike(like)))
    if status_filter:
        query = query.filter(Project.status == ProjectStatus(status_filter))
    if priority_filter:
        query = query.filter(Project.priority == TaskPriority(priority_filter))
    if owner_code:
        query = query.filter(Project.owner_employee_code == owner_code)

    total = query.count()
    projects = query.order_by(Project.created_at.desc()).limit(PROJECTS_MAX_ROWS).all()

    employee_repository = current_app.extensions["employee_repository"]
    owner_codes = [p.owner_employee_code for p in projects]
    owners = employee_repository.get_by_codes(owner_codes)
    healths = {p.id: project_service.health(p) for p in projects}

    form = ProjectForm()
    return render_template(
        "projects/list.html",
        projects=projects,
        owners=owners,
        healths=healths,
        q=q,
        status_filter=status_filter,
        priority_filter=priority_filter,
        owner_code=owner_code,
        is_ea=_is_ea(),
        form=form,
        total=total,
        shown=len(projects),
        priority_rank=PRIORITY_RANK,
        health_rank=HEALTH_RANK,
        email_draft=pop_email_draft("project"),
    )


@projects_bp.route("/", methods=["POST"])
@login_required
@roles_required("EA")
def create():
    form = ProjectForm()
    owner_employee_code = (request.form.get("owner_employee_code") or "").strip()

    if not form.validate_on_submit():
        flash("Please provide a valid project name.", "danger")
        return redirect(url_for("projects.list_view"))
    if not owner_employee_code:
        flash("Please select a project owner.", "danger")
        return redirect(url_for("projects.list_view"))

    code = form.project_code.data.strip().upper() if form.project_code.data else project_service.generate_project_code(form.name.data)

    try:
        project = project_service.create_project(
            project_code=code,
            name=form.name.data,
            description=form.description.data,
            owner_employee_code=owner_employee_code,
            start_date=form.start_date.data,
            target_date=form.target_date.data,
            priority=TaskPriority(form.priority.data),
            created_by=current_user.username,
        )
        flash("Project created.", "success")
        return redirect(url_for("projects.detail", project_id=project.id))
    except project_service.ProjectValidationError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("projects.list_view"))


def _parse_date_arg(field: str) -> date | None:
    raw = (request.form.get(field) or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


@projects_bp.route("/<int:project_id>/edit", methods=["POST"])
@login_required
@roles_required("EA")
def edit(project_id):
    project = _get_project_or_404(project_id)
    owner_employee_code = (request.form.get("owner_employee_code") or "").strip()

    try:
        priority = TaskPriority(request.form.get("priority", TaskPriority.MEDIUM.value))
    except ValueError:
        flash("Invalid priority.", "danger")
        return redirect(request.referrer or url_for("projects.list_view"))

    try:
        project_service.update_project_details(
            project,
            name=request.form.get("name", ""),
            owner_employee_code=owner_employee_code,
            description=request.form.get("description", ""),
            start_date=_parse_date_arg("start_date"),
            target_date=_parse_date_arg("target_date"),
            priority=priority,
            performed_by=current_user.username,
        )
        flash("Project updated.", "success")
    except project_service.ProjectValidationError as exc:
        flash(str(exc), "danger")

    return redirect(request.referrer or url_for("projects.list_view"))


@projects_bp.route("/<int:project_id>/delete", methods=["POST"])
@login_required
@roles_required("EA")
def delete(project_id):
    project = _get_project_or_404(project_id)
    project_service.delete_project(project, performed_by=current_user.username)
    flash("Project deleted.", "info")
    return redirect(request.referrer or url_for("projects.list_view"))


@projects_bp.route("/<int:project_id>")
@login_required
@roles_required("MD", "EA")
def detail(project_id):
    project = _get_project_or_404(project_id)
    tasks = (
        Task.query.filter_by(project_id=project.id, task_type=TaskType.PROJECT_TASK)
        .order_by(db.case((Task.due_date.is_(None), 1), else_=0), Task.due_date.asc())
        .all()
    )

    employee_repository = current_app.extensions["employee_repository"]

    task_ids = [t.id for t in tasks]
    assignees_by_task: dict[int, list] = {tid: [] for tid in task_ids}
    if task_ids:
        assignments = TaskAssignee.query.filter(TaskAssignee.task_id.in_(task_ids)).all()
        profiles = employee_repository.get_by_codes([a.employee_code for a in assignments])
        for a in assignments:
            profile = profiles.get(a.employee_code)
            assignees_by_task[a.task_id].append(profile.name if profile else a.employee_code)

    member_codes = [m.employee_code for m in project.members] + [project.owner_employee_code]
    member_profiles = employee_repository.get_by_codes(member_codes)

    tagged_meetings = [link.meeting for link in project.meeting_links]
    available_meetings_query = Meeting.query
    if tagged_meetings:
        available_meetings_query = available_meetings_query.filter(
            ~Meeting.id.in_([m.id for m in tagged_meetings])
        )
    available_meetings = available_meetings_query.order_by(Meeting.start_datetime.desc()).limit(50).all()
    tag_meeting_form = TagMeetingForm()
    tag_meeting_form.meeting_id.choices = [(m.id, f"{m.title} ({m.start_datetime.strftime('%d %b %Y')})") for m in available_meetings]

    activity_entries = (
        ActivityLog.query.filter(
            db.or_(
                db.and_(ActivityLog.entity_type == "PROJECT", ActivityLog.entity_id == str(project.id)),
                db.and_(ActivityLog.entity_type == "TASK", ActivityLog.entity_id.in_([str(i) for i in task_ids])),
            )
        )
        .order_by(ActivityLog.performed_at.desc())
        .limit(50)
        .all()
    )

    return render_template(
        "projects/detail.html",
        project=project,
        tasks=tasks,
        assignees_by_task=assignees_by_task,
        member_profiles=member_profiles,
        health=project_service.health(project),
        tagged_meetings=tagged_meetings,
        meeting_project_links=project.meeting_links,
        activity_entries=activity_entries,
        tag_assignments=tag_service.get_assignments_for_entity("PROJECT", project.id),
        all_tag_names=tag_service.all_tag_names(),
        is_ea=_is_ea(),
        task_form=ProjectTaskForm(),
        member_form=MemberForm(),
        tag_meeting_form=tag_meeting_form,
    )


@projects_bp.route("/<int:project_id>/status", methods=["POST"])
@login_required
@roles_required("EA")
def update_status(project_id):
    project = _get_project_or_404(project_id)
    try:
        new_status = ProjectStatus(request.form.get("status", ""))
        project_service.update_status(project, new_status=new_status, performed_by=current_user.username)
        flash("Project status updated.", "success")
    except ValueError:
        flash("Invalid status.", "danger")
    return redirect(url_for("projects.detail", project_id=project.id))


@projects_bp.route("/<int:project_id>/members", methods=["POST"])
@login_required
@roles_required("EA")
def add_member(project_id):
    project = _get_project_or_404(project_id)
    employee_code = (request.form.get("employee_code") or "").strip()
    form = MemberForm()
    if not employee_code:
        flash("Please select an employee to add.", "danger")
        return redirect(url_for("projects.detail", project_id=project.id) + "#tab-team")
    try:
        project_service.add_member(project, employee_code=employee_code, role=form.role.data, added_by=current_user.username)
        flash("Team member added.", "success")
    except project_service.ProjectValidationError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("projects.detail", project_id=project.id) + "#tab-team")


@projects_bp.route("/<int:project_id>/members/<int:member_id>/remove", methods=["POST"])
@login_required
@roles_required("EA")
def remove_member(project_id, member_id):
    project = _get_project_or_404(project_id)
    member = db.session.get(ProjectMember, member_id)
    if member is None or member.project_id != project.id:
        abort(404)
    project_service.remove_member(project, member, performed_by=current_user.username)
    flash("Team member removed.", "info")
    return redirect(url_for("projects.detail", project_id=project.id) + "#tab-team")


@projects_bp.route("/<int:project_id>/tasks", methods=["POST"])
@login_required
@roles_required("EA")
def add_task(project_id):
    project = _get_project_or_404(project_id)
    form = ProjectTaskForm()
    employee_codes = [c for c in request.form.getlist("employee_codes") if c.strip()]

    if not form.validate_on_submit():
        flash("Please provide a task title.", "danger")
        return redirect(url_for("projects.detail", project_id=project.id) + "#tab-tasks")

    try:
        task_service.create_project_task(
            project,
            title=form.title.data,
            description=form.description.data,
            employee_codes=employee_codes,
            priority=TaskPriority(form.priority.data),
            start_date=form.start_date.data,
            due_date=form.due_date.data,
            created_by=current_user.username,
        )
        flash("Task created.", "success")
    except task_service.TaskValidationError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("projects.detail", project_id=project.id) + "#tab-tasks")


@projects_bp.route("/<int:project_id>/tag-meeting", methods=["POST"])
@login_required
@roles_required("EA")
def tag_meeting(project_id):
    project = _get_project_or_404(project_id)
    meeting_id = request.form.get("meeting_id", type=int)
    meeting = db.session.get(Meeting, meeting_id) if meeting_id else None
    if meeting is None:
        flash("Please select a meeting to tag.", "danger")
        return redirect(url_for("projects.detail", project_id=project.id) + "#tab-meetings")
    try:
        project_service.tag_meeting(project, meeting, performed_by=current_user.username)
        flash("Meeting tagged to project.", "success")
    except project_service.ProjectValidationError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("projects.detail", project_id=project.id) + "#tab-meetings")


@projects_bp.route("/<int:project_id>/untag-meeting/<int:link_id>", methods=["POST"])
@login_required
@roles_required("EA")
def untag_meeting(project_id, link_id):
    project = _get_project_or_404(project_id)
    link = db.session.get(MeetingProject, link_id)
    if link is None or link.project_id != project.id:
        abort(404)
    project_service.untag_meeting(link, performed_by=current_user.username)
    flash("Meeting untagged.", "info")
    return redirect(url_for("projects.detail", project_id=project.id) + "#tab-meetings")
