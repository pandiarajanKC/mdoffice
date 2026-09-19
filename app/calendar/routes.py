from datetime import date, datetime

from flask import abort, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app.calendar import calendar_bp
from app.models.app_user import AppUser, UserRole
from app.models.calendar_integration import CalendarIntegration
from app.models.email_integration import EmailIntegration
from app.models.meeting import Meeting
from app.services import calendar_sync_service, email_sync_service
from app.services.calendar import factory
from app.services.calendar.base_calendar_service import CalendarAuthError, CalendarSyncError
from app.services.email import factory as email_factory
from app.services.email.base_email_service import EmailAuthError, EmailFetchError
from app.utils.rbac import roles_required


@calendar_bp.route("/")
@login_required
@roles_required("MD", "EA")
def status():
    if not factory.is_configured():
        return render_template("calendar/not_configured.html")

    md_accounts = AppUser.query.filter_by(role=UserRole.MD).order_by(AppUser.username).all()
    integrations = {i.owner_username: i for i in CalendarIntegration.query.all()}

    return render_template("calendar/status.html", md_accounts=md_accounts, integrations=integrations)


@calendar_bp.route("/connect")
@login_required
@roles_required("MD", "EA")
def connect():
    if not factory.is_configured():
        abort(404)

    owner_username = request.args.get("md_username", "")
    md_user = AppUser.query.filter_by(username=owner_username, role=UserRole.MD).first()
    if md_user is None:
        flash("Select a valid MD account to connect.", "danger")
        return redirect(url_for("calendar.status"))

    state = calendar_sync_service.new_oauth_state()
    auth_url, code_verifier = calendar_sync_service.start_authorization(state)
    session["oauth_flow"] = "calendar"
    session["calendar_oauth_state"] = state
    session["calendar_oauth_owner"] = owner_username
    session["calendar_oauth_code_verifier"] = code_verifier
    return redirect(auth_url)


@calendar_bp.route("/oauth/callback")
@login_required
@roles_required("MD", "EA")
def oauth_callback():
    """Shared Google OAuth callback for both flows this app has (MD
    calendar connect, EA email connect) — one registered redirect URI
    means one Google Cloud OAuth client to configure instead of two, and
    `oauth_flow` (set right before redirecting to Google) says which
    completion logic to run.
    """
    flow = session.pop("oauth_flow", "calendar")
    error = request.args.get("error")
    if error:
        flash(f"Google authorization was not completed: {error}", "danger")
        return redirect(url_for("calendar.email_search" if flow == "email" else "calendar.status"))

    returned_state = request.args.get("state")
    code = request.args.get("code")
    expected_state = session.pop("calendar_oauth_state", None)
    code_verifier = session.pop("calendar_oauth_code_verifier", None)
    valid = code and returned_state and returned_state == expected_state

    if flow == "email":
        if not valid:
            flash("This authorization link is invalid or has expired. Please try connecting again.", "danger")
            return redirect(url_for("calendar.email_search"))
        try:
            integration = email_sync_service.complete_authorization(
                owner_username=current_user.username, code=code, connected_by=current_user.username, code_verifier=code_verifier
            )
            flash(f"Gmail connected ({integration.google_account_email or current_user.username}).", "success")
        except EmailAuthError as exc:
            flash(str(exc), "danger")
        return redirect(url_for("calendar.email_search"))

    owner_username = session.pop("calendar_oauth_owner", None)
    if not valid or not owner_username:
        flash("This authorization link is invalid or has expired. Please try connecting again.", "danger")
        return redirect(url_for("calendar.status"))

    try:
        integration = calendar_sync_service.complete_authorization(
            owner_username=owner_username, code=code, connected_by=current_user.username, code_verifier=code_verifier
        )
        flash(f"Google Calendar connected ({integration.google_account_email or owner_username}).", "success")
        try:
            log = calendar_sync_service.sync_now(integration, triggered_by=current_user.username)
            flash(f"Initial sync complete: {log.events_created} new, {log.events_updated} updated.", "success")
        except (CalendarAuthError, CalendarSyncError) as exc:
            flash(f"Connected, but the first sync failed: {exc}", "warning")
    except CalendarAuthError as exc:
        flash(str(exc), "danger")

    return redirect(url_for("calendar.status"))


@calendar_bp.route("/sync/<username>", methods=["POST"])
@login_required
@roles_required("MD", "EA")
def sync(username):
    integration = CalendarIntegration.query.filter_by(owner_username=username).first()
    if integration is None:
        abort(404)
    try:
        log = calendar_sync_service.sync_now(integration, triggered_by=current_user.username)
        flash(f"Synced: {log.events_created} new, {log.events_updated} updated.", "success")
    except (CalendarAuthError, CalendarSyncError) as exc:
        flash(f"Sync failed: {exc}", "danger")
    return redirect(url_for("calendar.status"))


@calendar_bp.route("/disconnect/<username>", methods=["POST"])
@login_required
@roles_required("MD", "EA")
def disconnect(username):
    integration = CalendarIntegration.query.filter_by(owner_username=username).first()
    if integration is None:
        abort(404)
    calendar_sync_service.disconnect(integration, performed_by=current_user.username)
    flash("Calendar disconnected. Previously synced meetings are kept.", "info")
    return redirect(url_for("calendar.status"))


@calendar_bp.route("/view")
@login_required
@roles_required("MD", "EA")
def view():
    return render_template("calendar/view.html")


@calendar_bp.route("/events.json")
@login_required
@roles_required("MD", "EA")
def events_json():
    query = Meeting.query
    start = request.args.get("start")
    end = request.args.get("end")
    if start:
        query = query.filter(Meeting.end_datetime >= datetime.fromisoformat(start.replace("Z", "+00:00")).replace(tzinfo=None))
    if end:
        query = query.filter(Meeting.start_datetime <= datetime.fromisoformat(end.replace("Z", "+00:00")).replace(tzinfo=None))

    # Names from the shared 8(+muted)-color palette in app/static/css/app.css
    # (the same one Tag Master and the Corporate Calendar use) rather than
    # one-off hex values — see corporate_calendar_service.event_to_calendar_dict
    # for the sibling implementation this mirrors.
    status_tag_names = {
        "SCHEDULED": "violet",
        "IN_PROGRESS": "warning",
        "COMPLETED": "success",
        "CANCELLED": "muted",
        "ON_HOLD": "danger",
    }

    return jsonify(
        [
            {
                "id": m.id,
                "title": m.title,
                "start": m.start_datetime.isoformat(),
                "end": m.end_datetime.isoformat(),
                "url": url_for("meetings.workspace", meeting_id=m.id),
                "classNames": ["fc-event-pill", "tag-" + status_tag_names.get(m.status.value, "violet")],
                "extendedProps": {
                    "status": m.status.value.replace("_", " ").title(),
                    "location": m.location or "",
                },
            }
            for m in query.all()
        ]
    )


# ---------- Email (EA's own Gmail, connected the same way as the MD's
# calendar) — search it and turn a message into a Project, Task, Action
# Tracker item, or Meeting. ----------

_CONVERT_ENDPOINTS = {
    "project": "projects.list_view",
    "task": "tasks.general_list",
    "tracker": "tasks.general_list",  # same creation form as "task"; the EA just lands on the Action Tracker afterward
    "meeting": "meetings.list_view",
}


def _parse_date_arg(raw: str) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


@calendar_bp.route("/email")
@login_required
@roles_required("EA")
def email_search():
    email_configured = email_factory.is_configured()
    integration = (
        EmailIntegration.query.filter_by(owner_username=current_user.username).first() if email_configured else None
    )

    from_query = (request.args.get("from") or "").strip()
    subject_query = (request.args.get("subject") or "").strip()
    raw_date_from = request.args.get("date_from", "")
    raw_date_to = request.args.get("date_to", "")
    date_from = _parse_date_arg(raw_date_from)
    date_to = _parse_date_arg(raw_date_to)
    has_filters = bool(from_query or subject_query or date_from or date_to)

    messages = None
    search_error = None
    if integration and has_filters:
        try:
            messages = email_sync_service.search(
                integration, from_query=from_query, subject_query=subject_query, date_from=date_from, date_to=date_to,
            )
        except EmailFetchError as exc:
            search_error = str(exc)

    return render_template(
        "calendar/email.html",
        integration=integration,
        email_configured=email_configured,
        from_query=from_query,
        subject_query=subject_query,
        date_from=raw_date_from,
        date_to=raw_date_to,
        has_filters=has_filters,
        messages=messages,
        search_error=search_error,
    )


@calendar_bp.route("/email/connect")
@login_required
@roles_required("EA")
def email_connect():
    if not email_factory.is_configured():
        abort(404)

    state = calendar_sync_service.new_oauth_state()
    auth_url, code_verifier = email_sync_service.start_authorization(state)
    session["oauth_flow"] = "email"
    session["calendar_oauth_state"] = state
    session["calendar_oauth_code_verifier"] = code_verifier
    return redirect(auth_url)


@calendar_bp.route("/email/disconnect", methods=["POST"])
@login_required
@roles_required("EA")
def email_disconnect():
    integration = EmailIntegration.query.filter_by(owner_username=current_user.username).first()
    if integration is None:
        abort(404)
    email_sync_service.disconnect(integration, performed_by=current_user.username)
    flash("Gmail disconnected.", "info")
    return redirect(url_for("calendar.email_search"))


@calendar_bp.route("/email/<message_id>/convert/<target>")
@login_required
@roles_required("EA")
def email_convert(message_id, target):
    if target not in _CONVERT_ENDPOINTS:
        abort(404)

    integration = EmailIntegration.query.filter_by(owner_username=current_user.username).first()
    if integration is None:
        abort(404)

    try:
        message = email_sync_service.get_message(integration, message_id)
    except EmailFetchError as exc:
        flash(f"Could not open that email: {exc}", "danger")
        return redirect(url_for("calendar.email_search"))

    sender = message.sender_name or message.sender_email or "an unknown sender"
    received = message.received_at.strftime("%d %b %Y, %I:%M %p") if message.received_at else "an unknown date"
    body = (message.body_text or message.snippet or "").strip()
    if len(body) > 4000:
        body = body[:4000] + "…"

    session["email_convert_draft"] = {
        "target": target,
        "title": message.subject or "(No subject)",
        "description": f"From an email — {sender}, {received}\n\n{body}".strip(),
    }
    return redirect(url_for(_CONVERT_ENDPOINTS[target]))
