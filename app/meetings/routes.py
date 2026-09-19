from datetime import date, datetime, time, timedelta

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.meetings import meetings_bp
from app.meetings.forms import ActionForm, DecisionForm, MeetingForm, NoteForm
from app.models.activity_log import ActivityLog
from app.models.app_user import UserRole
from app.models.attachment import Attachment
from app.models.meeting import Meeting, MeetingNote, MeetingStatus
from app.models.meeting_project import MeetingProject
from app.models.meeting_template import MeetingTemplate
from app.models.project import Project
from app.models.task import Task, TaskAssignee, TaskPriority, TaskType
from app.services import meeting_service, project_service, tag_service, task_service
from app.services.ai import factory as ai_factory
from app.utils.email_draft import pop_email_draft
from app.utils.meeting_display import parse_attendees
from app.utils.rbac import roles_required


def _parse_time(value: str):
    value = (value or "").strip()
    for fmt in ("%I:%M %p", "%H:%M"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"'{value}' is not a valid time (expected e.g. 10:00 AM or 14:00).")


def _get_meeting_or_404(meeting_id: int) -> Meeting:
    meeting = db.session.get(Meeting, meeting_id)
    if meeting is None:
        abort(404)
    return meeting


def _is_ea() -> bool:
    return current_user.role == UserRole.EA


def _parse_date_arg(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


# These three read as a schedule — earliest date first, and within the
# same date, earliest start time first (sorting by the single
# start_datetime column ascending gives both for free, i.e. same-day
# meetings naturally come out FIFO by time). "all" mixes past and future
# together, so it keeps the newest-first order instead.
CHRONOLOGICAL_VIEWS = {"today", "upcoming", "completed"}


def _meeting_view_queries():
    # Portable day-range filter: SQL Server's dialect can compile a
    # cast-to-Date ambiguously without a live connection to detect the
    # server version, so compare against a full datetime range instead.
    today = datetime.now().date()
    day_start = datetime.combine(today, time.min)
    day_end = day_start + timedelta(days=1)
    return {
        "all": Meeting.query,
        "today": Meeting.query.filter(Meeting.start_datetime >= day_start, Meeting.start_datetime < day_end),
        "upcoming": Meeting.query.filter(Meeting.start_datetime >= datetime.now(), Meeting.status != MeetingStatus.CANCELLED),
        "completed": Meeting.query.filter(Meeting.status == MeetingStatus.COMPLETED),
    }


@meetings_bp.route("/")
@login_required
@roles_required("MD", "EA")
def list_view():
    view = request.args.get("view", "today")
    view_queries = _meeting_view_queries()
    query = view_queries.get(view, view_queries["today"])

    date_from_raw = (request.args.get("date_from") or "").strip()
    date_to_raw = (request.args.get("date_to") or "").strip()
    date_from = _parse_date_arg(date_from_raw)
    date_to = _parse_date_arg(date_to_raw)
    if date_from:
        query = query.filter(Meeting.start_datetime >= datetime.combine(date_from, time.min))
    if date_to:
        query = query.filter(Meeting.start_datetime < datetime.combine(date_to, time.min) + timedelta(days=1))

    order = Meeting.start_datetime.asc() if view in CHRONOLOGICAL_VIEWS else Meeting.start_datetime.desc()
    meetings = query.order_by(order).limit(100).all()
    followups = {m.id: meeting_service.followup_status(m) for m in meetings}
    effectiveness_by_meeting = {
        m.id: meeting_service.effectiveness_score(m) for m in meetings if m.status == MeetingStatus.COMPLETED
    }
    attendees_by_meeting = {m.id: parse_attendees(m.attendees_text) for m in meetings}
    view_counts = {key: q.count() for key, q in view_queries.items()}

    templates = [
        {
            "id": t.id, "name": t.name, "title": t.title, "description": t.description or "",
            "location": t.location or "", "organizer": t.organizer or "", "meeting_type": t.meeting_type or "",
            "confidentiality_level": t.confidentiality_level.value, "duration_minutes": t.duration_minutes,
        }
        for t in meeting_service.list_templates()
    ]

    form = MeetingForm()
    return render_template(
        "meetings/list.html", meetings=meetings, view=view, followups=followups, form=form, is_ea=_is_ea(),
        view_counts=view_counts, templates=templates, email_draft=pop_email_draft("meeting"),
        effectiveness_by_meeting=effectiveness_by_meeting, attendees_by_meeting=attendees_by_meeting,
        date_from=date_from_raw, date_to=date_to_raw,
    )


@meetings_bp.route("/", methods=["POST"])
@login_required
@roles_required("EA")
def create():
    form = MeetingForm()
    if not form.validate_on_submit():
        for field, errors in form.errors.items():
            for err in errors:
                flash(f"{field}: {err}", "danger")
        return redirect(url_for("meetings.list_view"))

    try:
        start_time = _parse_time(form.start_time.data)
        end_time = _parse_time(form.end_time.data)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("meetings.list_view"))

    meeting_date = form.meeting_date.data
    start_dt = datetime.combine(meeting_date, start_time)
    end_dt = datetime.combine(meeting_date, end_time)
    if end_dt <= start_dt:
        flash("End time must be after start time.", "danger")
        return redirect(url_for("meetings.list_view"))

    meeting = meeting_service.create_meeting(
        title=form.title.data,
        description=form.description.data,
        start_datetime=start_dt,
        end_datetime=end_dt,
        location=form.location.data,
        meeting_link=form.meeting_link.data,
        organizer=form.organizer.data,
        meeting_type=form.meeting_type.data,
        attendees_emails=form.attendees_emails.data,
        confidentiality_level=form.confidentiality_level.data,
        created_by=current_user.username,
    )
    flash("Meeting created.", "success")
    return redirect(url_for("meetings.workspace", meeting_id=meeting.id))


def build_workspace_context(meeting: Meeting, **extra) -> dict:
    """Shared by the plain workspace view and the AI routes (which
    re-render the same template with extra context: an upload form, a
    transcript/summary draft, or AI-suggested actions awaiting review) so
    the two never drift out of sync with each other.
    """
    # SQL Server has no NULLS LAST clause, so push NULL due dates to the
    # end with a CASE expression instead — portable across every dialect.
    tasks = (
        Task.query.filter_by(meeting_id=meeting.id, task_type=TaskType.MEETING_ACTION)
        .order_by(db.case((Task.due_date.is_(None), 1), else_=0), Task.due_date.asc())
        .all()
    )
    checklist = meeting_service.completion_checklist(meeting)
    employee_repository = current_app.extensions["employee_repository"]

    task_ids = [t.id for t in tasks]
    assignees_by_task: dict[int, list] = {tid: [] for tid in task_ids}
    if task_ids:
        assignments = TaskAssignee.query.filter(TaskAssignee.task_id.in_(task_ids)).all()
        profiles = employee_repository.get_by_codes([a.employee_code for a in assignments])
        for a in assignments:
            profile = profiles.get(a.employee_code)
            assignees_by_task[a.task_id].append(profile.name if profile else a.employee_code)

    prep_brief = meeting_service.prep_brief(meeting)
    if prep_brief and prep_brief["open_actions"]:
        brief_task_ids = [t.id for t in prep_brief["open_actions"]]
        brief_assignments = TaskAssignee.query.filter(TaskAssignee.task_id.in_(brief_task_ids)).all()
        brief_profiles = employee_repository.get_by_codes([a.employee_code for a in brief_assignments])
        brief_assignees_by_task: dict[int, list] = {tid: [] for tid in brief_task_ids}
        for a in brief_assignments:
            profile = brief_profiles.get(a.employee_code)
            brief_assignees_by_task[a.task_id].append(profile.name if profile else a.employee_code)
        prep_brief["assignees_by_task"] = brief_assignees_by_task

    activity_entries = (
        ActivityLog.query.filter(
            db.or_(
                db.and_(ActivityLog.entity_type == "MEETING", ActivityLog.entity_id == str(meeting.id)),
                db.and_(ActivityLog.entity_type == "TASK", ActivityLog.entity_id.in_([str(i) for i in task_ids])),
            )
        )
        .order_by(ActivityLog.performed_at.desc())
        .limit(50)
        .all()
    )

    tagged_projects = [link.project for link in meeting.project_links]
    tagged_project_ids = [p.id for p in tagged_projects]
    available_projects_query = Project.query
    if tagged_project_ids:
        available_projects_query = available_projects_query.filter(~Project.id.in_(tagged_project_ids))
    available_projects = available_projects_query.order_by(Project.name.asc()).limit(100).all()

    latest_audio = (
        Attachment.query.filter_by(entity_type="MEETING", entity_id=str(meeting.id))
        .order_by(Attachment.uploaded_at.desc())
        .first()
    )

    context = dict(
        meeting=meeting,
        tasks=tasks,
        assignees_by_task=assignees_by_task,
        checklist=checklist,
        activity_entries=activity_entries,
        tagged_projects=tagged_projects,
        meeting_project_links=meeting.project_links,
        available_projects=available_projects,
        tag_assignments=tag_service.get_assignments_for_entity("MEETING", meeting.id),
        all_tag_names=tag_service.all_tag_names(),
        attendees=parse_attendees(meeting.attendees_text),
        is_ea=_is_ea(),
        note_form=NoteForm(),
        decision_form=DecisionForm(),
        action_form=ActionForm(),
        ai_suggestions=None,
        notes_draft=None,
        latest_audio=latest_audio,
        ai_configured=ai_factory.is_configured(),
        prep_brief=prep_brief,
    )
    context.update(extra)
    return context


@meetings_bp.route("/<int:meeting_id>")
@login_required
@roles_required("MD", "EA")
def workspace(meeting_id):
    meeting = _get_meeting_or_404(meeting_id)
    return render_template("meetings/workspace.html", **build_workspace_context(meeting))


@meetings_bp.route("/<int:meeting_id>/history")
@login_required
@roles_required("MD", "EA")
def history(meeting_id):
    meeting = _get_meeting_or_404(meeting_id)
    data = meeting_service.meeting_history(meeting)
    series = data["series"]

    action_tasks_by_meeting = {
        m.id: [t for t in m.tasks if t.task_type == TaskType.MEETING_ACTION] for m in series
    }
    all_codes = {
        a.employee_code
        for tasks in action_tasks_by_meeting.values()
        for t in tasks
        for a in t.assignees
    }
    employee_repository = current_app.extensions["employee_repository"]
    profiles = employee_repository.get_by_codes(list(all_codes)) if all_codes else {}
    assignees_by_task = {
        t.id: [profiles[a.employee_code].name if a.employee_code in profiles else a.employee_code for a in t.assignees]
        for tasks in action_tasks_by_meeting.values()
        for t in tasks
    }

    # Split at "this one" so the template can render two timeline segments —
    # this occurrence and everything more recent than it, then older history
    # below — rather than one flat list, which is what actually lets the
    # vertical timeline's spine change color partway down like the reference.
    current_index = next((i for i, m in enumerate(series) if m.id == meeting.id), 0)
    recent_series = series[: current_index + 1]
    older_series = series[current_index + 1:]

    return render_template(
        "meetings/history.html",
        meeting=meeting,
        series=series,
        recent_series=recent_series,
        older_series=older_series,
        action_tasks_by_meeting=action_tasks_by_meeting,
        assignees_by_task=assignees_by_task,
        participants=data["participants"],
    )


@meetings_bp.route("/<int:meeting_id>/start", methods=["POST"])
@login_required
@roles_required("EA")
def start(meeting_id):
    meeting = _get_meeting_or_404(meeting_id)
    try:
        meeting_service.start_meeting(meeting, performed_by=current_user.username)
        flash("Meeting started.", "success")
    except meeting_service.MeetingWorkflowError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("meetings.workspace", meeting_id=meeting.id))


@meetings_bp.route("/<int:meeting_id>/complete", methods=["POST"])
@login_required
@roles_required("EA")
def complete(meeting_id):
    meeting = _get_meeting_or_404(meeting_id)
    try:
        meeting_service.complete_meeting(meeting, performed_by=current_user.username)
        flash("Meeting completed.", "success")
    except meeting_service.MeetingWorkflowError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("meetings.workspace", meeting_id=meeting.id))


@meetings_bp.route("/<int:meeting_id>/reopen", methods=["POST"])
@login_required
@roles_required("EA")
def reopen(meeting_id):
    meeting = _get_meeting_or_404(meeting_id)
    try:
        meeting_service.reopen_meeting(meeting, performed_by=current_user.username)
        flash("Meeting reopened.", "info")
    except meeting_service.MeetingWorkflowError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("meetings.workspace", meeting_id=meeting.id))


@meetings_bp.route("/<int:meeting_id>/notes", methods=["POST"])
@login_required
@roles_required("EA")
def add_note(meeting_id):
    meeting = _get_meeting_or_404(meeting_id)
    form = NoteForm()
    if form.validate_on_submit():
        meeting_service.add_note(meeting, note_text=form.note_text.data, created_by=current_user.username)
    else:
        flash("Note text is required.", "danger")
    return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-notes")


@meetings_bp.route("/<int:meeting_id>/notes/<int:note_id>/delete", methods=["POST"])
@login_required
@roles_required("EA")
def delete_note(meeting_id, note_id):
    note = db.session.get(MeetingNote, note_id)
    if note is None or note.meeting_id != meeting_id:
        abort(404)
    meeting_service.delete_note(note, performed_by=current_user.username)
    return redirect(url_for("meetings.workspace", meeting_id=meeting_id) + "#tab-notes")


@meetings_bp.route("/<int:meeting_id>/notes/<int:note_id>/convert-to-decision", methods=["POST"])
@login_required
@roles_required("EA")
def convert_note_to_decision(meeting_id, note_id):
    note = db.session.get(MeetingNote, note_id)
    if note is None or note.meeting_id != meeting_id:
        abort(404)
    meeting_service.convert_note_to_decision(note, performed_by=current_user.username)
    flash("Note converted to a decision.", "success")
    return redirect(url_for("meetings.workspace", meeting_id=meeting_id) + "#tab-decisions")


@meetings_bp.route("/<int:meeting_id>/decisions", methods=["POST"])
@login_required
@roles_required("EA")
def add_decision(meeting_id):
    meeting = _get_meeting_or_404(meeting_id)
    form = DecisionForm()
    if form.validate_on_submit():
        meeting_service.add_decision(meeting, decision_text=form.decision_text.data, created_by=current_user.username)
    else:
        flash("Decision text is required.", "danger")
    return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-decisions")


@meetings_bp.route("/<int:meeting_id>/actions", methods=["POST"])
@login_required
@roles_required("EA")
def add_action(meeting_id):
    meeting = _get_meeting_or_404(meeting_id)
    form = ActionForm()
    employee_codes = [c for c in request.form.getlist("employee_codes") if c.strip()]

    if not form.validate_on_submit():
        flash("Please provide an action title.", "danger")
        return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-actions")

    try:
        task_service.create_meeting_action(
            meeting,
            title=form.title.data,
            description=form.description.data,
            employee_codes=employee_codes,
            priority=TaskPriority(form.priority.data),
            due_date=form.due_date.data,
            created_by=current_user.username,
        )
        # Set only when this came from a note's "Convert to Action" button
        # (see the notes tab) — the note stays put until an assignee is
        # actually picked, since an action item requires one.
        source_note_id = (request.form.get("source_note_id") or "").strip()
        if source_note_id.isdigit():
            source_note = db.session.get(MeetingNote, int(source_note_id))
            if source_note is not None and source_note.meeting_id == meeting.id:
                meeting_service.delete_note(source_note, performed_by=current_user.username)
        flash("Action item created.", "success")
    except task_service.TaskValidationError as exc:
        flash(str(exc), "danger")

    return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-actions")


@meetings_bp.route("/<int:meeting_id>/tag-project", methods=["POST"])
@login_required
@roles_required("EA")
def tag_project(meeting_id):
    meeting = _get_meeting_or_404(meeting_id)
    project_id = request.form.get("project_id", type=int)
    project = db.session.get(Project, project_id) if project_id else None
    if project is None:
        flash("Please select a project to tag.", "danger")
        return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-overview")
    try:
        project_service.tag_meeting(project, meeting, performed_by=current_user.username)
        flash("Project tagged to meeting.", "success")
    except project_service.ProjectValidationError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-overview")


@meetings_bp.route("/<int:meeting_id>/untag-project/<int:link_id>", methods=["POST"])
@login_required
@roles_required("EA")
def untag_project(meeting_id, link_id):
    link = db.session.get(MeetingProject, link_id)
    if link is None or link.meeting_id != meeting_id:
        abort(404)
    project_service.untag_meeting(link, performed_by=current_user.username)
    flash("Project untagged.", "info")
    return redirect(url_for("meetings.workspace", meeting_id=meeting_id) + "#tab-overview")


@meetings_bp.route("/<int:meeting_id>/save-as-template", methods=["POST"])
@login_required
@roles_required("EA")
def save_as_template(meeting_id):
    meeting = _get_meeting_or_404(meeting_id)
    try:
        meeting_service.save_as_template(meeting, name=request.form.get("template_name", ""), created_by=current_user.username)
        flash("Saved as a meeting template — use it from the New Meeting form next time.", "success")
    except meeting_service.MeetingTemplateError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-overview")


@meetings_bp.route("/templates")
@login_required
@roles_required("EA")
def templates():
    return render_template("meetings/templates.html", templates=meeting_service.list_templates())


@meetings_bp.route("/templates/<int:template_id>/delete", methods=["POST"])
@login_required
@roles_required("EA")
def delete_template(template_id):
    template = db.session.get(MeetingTemplate, template_id)
    if template is None:
        abort(404)
    meeting_service.delete_template(template)
    flash("Template deleted.", "info")
    return redirect(url_for("meetings.templates"))
