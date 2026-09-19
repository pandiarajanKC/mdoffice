"""Corporate Calendar — see app/services/corporate_calendar_service.py for
the module-level explanation of the EA/MD-aggregate vs employee-own split,
and app/models/corporate_event.py for the field-by-field mapping to the
source "Corporate Calendar Event Submission Form".

create()/edit()/delete() each render one of two ways: a full page (direct
navigation, e.g. a bookmark or a page refresh) or a bare HTML fragment
injected into the modal on mine.html (an XMLHttpRequest fetch — see
app/static/js/app.js's wireCorporateEventForm). Same form, same
validation, same routes either way — only the wrapper differs.
"""
from __future__ import annotations

from datetime import date

from flask import current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from markupsafe import escape

from app.corporate_calendar import corporate_calendar_bp
from app.corporate_calendar.forms import CorporateEventForm
from app.models.corporate_event import (
    ATTACHMENT_TYPE_LABELS,
    CONFLICT_STATUS_LABELS,
    MD_INVOLVEMENT_LABELS,
    PRIORITY_BADGE_COLOR,
    PRIORITY_LABELS,
    ConflictStatus,
    EventType,
    MDInvolvement,
    PriorityLevel,
)
from app.services import corporate_calendar_service as cc_service
from app.utils.rbac import roles_required


def _employee_repository():
    return current_app.extensions["employee_repository"]


def _wants_fragment() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _yn(value: str | None) -> bool:
    return value == "yes"


def _fields_from_form(form: CorporateEventForm) -> dict:
    return dict(
        title=form.title.data,
        event_type=EventType(form.event_type.data),
        event_type_other=form.event_type_other.data,
        start_date=form.start_date.data,
        end_date=form.end_date.data,
        location=form.location.data,
        organizer=form.organizer.data,
        description=form.description.data,
        priority_level=PriorityLevel(form.priority_level.data),
        md_presence_required=MDInvolvement(form.md_presence_required.data),
        involves_external_stakeholder=_yn(form.involves_external_stakeholder.data),
        has_critical_impact=_yn(form.has_critical_impact.data),
        key_participants=form.key_participants.data,
        estimated_attendees=form.estimated_attendees.data,
        pre_event_actions=form.pre_event_actions.data,
        documents_to_prepare=form.documents_to_prepare.data,
        budget_involved=_yn(form.budget_involved.data),
        budget_amount=form.budget_amount.data,
        preparation_deadline=form.preparation_deadline.data,
        conflicts_with_events=ConflictStatus(form.conflicts_with_events.data) if form.conflicts_with_events.data else None,
        conflict_notes=form.conflict_notes.data,
        attachment_types=form.attachment_types.data,
        attachment_notes=form.attachment_notes.data,
    )


def _populate_form_from_event(form: CorporateEventForm, event) -> None:
    """obj=event on construction already copied every plain field (title,
    dates, location, organizer, description, event_type_other, ...) since
    they match the model 1:1 in name and type. Only the enum/boolean fields
    need converting back to the plain strings the Radio/Select widgets
    expect — see app/models/corporate_event.py's module docstring on why
    str(SomeEnum) can't be relied on for this (use .value, always).
    """
    form.event_type.data = event.event_type.value
    form.priority_level.data = event.priority_level.value
    form.md_presence_required.data = event.md_presence_required.value
    form.involves_external_stakeholder.data = "yes" if event.involves_external_stakeholder else "no"
    form.has_critical_impact.data = "yes" if event.has_critical_impact else "no"
    form.budget_involved.data = "yes" if event.budget_involved else "no"
    form.conflicts_with_events.data = event.conflicts_with_events.value if event.conflicts_with_events else ConflictStatus.NOT_SURE.value
    form.attachment_types.data = event.attachment_type_list


# ---------------------------------------------------------- EA / MD: read-only aggregate ----

@corporate_calendar_bp.route("/")
@login_required
@roles_required("MD", "EA")
def index():
    repo = _employee_repository()
    legend = cc_service.divisions_legend(repo)
    # The calendar grid itself pulls its (date-range-scoped) events from
    # events.json; the detailed table below it is server-rendered from the
    # full list instead, since the whole point of that table (per the EA/MD
    # request it was built for) is seeing everything at once, not just
    # whatever's in the currently-navigated month/week.
    events = cc_service.list_all()
    creators = cc_service.creator_info(events, repo)
    conflicts = cc_service.find_conflicts(events)
    clashing_count = sum(1 for e in events if conflicts.get(e.id))
    return render_template(
        "corporate_calendar/index.html", legend=legend, events=events, creators=creators, conflicts=conflicts,
        clashing_count=clashing_count,
        PRIORITY_LABELS=PRIORITY_LABELS, PRIORITY_BADGE_COLOR=PRIORITY_BADGE_COLOR,
        MD_INVOLVEMENT_LABELS=MD_INVOLVEMENT_LABELS, CONFLICT_STATUS_LABELS=CONFLICT_STATUS_LABELS,
        ATTACHMENT_TYPE_LABELS=ATTACHMENT_TYPE_LABELS,
    )


@corporate_calendar_bp.route("/events.json")
@login_required
@roles_required("MD", "EA")
def events_json():
    events = cc_service.list_all(start=request.args.get("start"), end=request.args.get("end"))
    repo = _employee_repository()
    return jsonify([cc_service.event_to_calendar_dict(e, employee_repository=repo, include_division=True) for e in events])


# ---------------------------------------------------------- Employee: own calendar, full CRUD ----

@corporate_calendar_bp.route("/mine")
@login_required
@roles_required("EMPLOYEE")
def mine():
    return render_template("corporate_calendar/mine.html")


@corporate_calendar_bp.route("/mine/events.json")
@login_required
@roles_required("EMPLOYEE")
def my_events_json():
    events = cc_service.list_mine(current_user.username, start=request.args.get("start"), end=request.args.get("end"))
    return jsonify([cc_service.event_to_calendar_dict(e) for e in events])


@corporate_calendar_bp.route("/create", methods=["GET", "POST"])
@login_required
@roles_required("EMPLOYEE")
def create():
    fragment = _wants_fragment()
    template = "corporate_calendar/_event_form_fields.html" if fragment else "corporate_calendar/event_form.html"
    form = CorporateEventForm()

    if request.method == "GET":
        # Pre-fill from a day clicked/dragged on the calendar grid, if any.
        start_arg = request.args.get("start")
        end_arg = request.args.get("end")
        if start_arg:
            try:
                form.start_date.data = date.fromisoformat(start_arg)
                form.end_date.data = date.fromisoformat(end_arg) if end_arg else form.start_date.data
            except ValueError:
                pass
        return render_template(template, form=form, event=None, service_error=None)

    service_error = None
    if form.validate_on_submit():
        try:
            cc_service.create_event(created_by=current_user.username, **_fields_from_form(form))
            if fragment:
                return "", 204
            flash("Event added to the Corporate Calendar.", "success")
            return redirect(url_for("corporate_calendar.mine"))
        except cc_service.CorporateEventValidationError as exc:
            service_error = str(exc)
            if not fragment:
                flash(service_error, "danger")
    elif not fragment:
        flash("Please fill in every field marked *.", "danger")

    return render_template(template, form=form, event=None, service_error=service_error)


@corporate_calendar_bp.route("/<int:event_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("EMPLOYEE")
def edit(event_id):
    fragment = _wants_fragment()

    try:
        event = cc_service.get_owned_event(event_id, requested_by=current_user.username)
    except (cc_service.CorporateEventValidationError, cc_service.CorporateEventPermissionError) as exc:
        if fragment:
            return f'<div class="alert alert-danger m-3">{escape(str(exc))}</div>', 200
        flash(str(exc), "danger")
        return redirect(url_for("corporate_calendar.mine"))

    template = "corporate_calendar/_event_form_fields.html" if fragment else "corporate_calendar/event_form.html"

    if request.method == "POST":
        form = CorporateEventForm()
        service_error = None
        if form.validate_on_submit():
            try:
                cc_service.update_event(event_id, requested_by=current_user.username, **_fields_from_form(form))
                if fragment:
                    return "", 204
                flash("Event updated.", "success")
                return redirect(url_for("corporate_calendar.mine"))
            except (cc_service.CorporateEventValidationError, cc_service.CorporateEventPermissionError) as exc:
                service_error = str(exc)
                if not fragment:
                    flash(service_error, "danger")
        elif not fragment:
            flash("Please fill in every field marked *.", "danger")
        return render_template(template, form=form, event=event, service_error=service_error)

    form = CorporateEventForm(obj=event)
    _populate_form_from_event(form, event)
    return render_template(template, form=form, event=event, service_error=None)


@corporate_calendar_bp.route("/<int:event_id>/delete", methods=["POST"])
@login_required
@roles_required("EMPLOYEE")
def delete(event_id):
    fragment = _wants_fragment()
    try:
        cc_service.delete_event(event_id, requested_by=current_user.username)
        if fragment:
            return "", 204
        flash("Event deleted.", "info")
    except (cc_service.CorporateEventValidationError, cc_service.CorporateEventPermissionError) as exc:
        if fragment:
            return str(exc), 400
        flash(str(exc), "danger")
    return redirect(url_for("corporate_calendar.mine"))


@corporate_calendar_bp.route("/<int:event_id>/reschedule", methods=["POST"])
@login_required
@roles_required("EMPLOYEE")
def reschedule(event_id):
    """Drag-to-move / resize on the calendar grid — AJAX (see mine.html),
    no page reload, so it can shift the date span without a full form
    round-trip through every other field.
    """
    payload = request.get_json(silent=True) or {}
    try:
        start = date.fromisoformat(payload.get("start") or "")
        end = date.fromisoformat(payload.get("end") or "")
    except ValueError:
        return jsonify({"error": "Invalid date."}), 400

    try:
        cc_service.reschedule_event(event_id, requested_by=current_user.username, start_date=start, end_date=end)
        return jsonify({"ok": True})
    except (cc_service.CorporateEventValidationError, cc_service.CorporateEventPermissionError) as exc:
        return jsonify({"error": str(exc)}), 400
