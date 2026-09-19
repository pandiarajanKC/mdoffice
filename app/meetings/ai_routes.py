import os
from datetime import date

from flask import abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.meetings import meetings_bp
from app.meetings.routes import _get_meeting_or_404, build_workspace_context
from app.models.attachment import Attachment
from app.models.task import TaskPriority
from app.services import attachment_service, meeting_ai_service, meeting_service, task_service
from app.services.ai import factory as ai_factory
from app.utils.rbac import roles_required


def _require_ai_configured():
    if not ai_factory.is_configured():
        flash(
            "AI features are not configured yet. An administrator needs to set OPENAI_API_KEY in .env — see the README's AI Tools setup section.",
            "warning",
        )
        return False
    return True


def _build_followup_source_text(meeting, tasks, assignees_by_task) -> str:
    lines = [f"Meeting: {meeting.title}", f"Date: {meeting.start_datetime.strftime('%d %b %Y')}"]
    if meeting.location:
        lines.append(f"Location: {meeting.location}")
    lines.append("")

    if meeting.notes:
        lines.append("Notes:")
        lines.extend(f"- {note.note_text}" for note in meeting.notes)
        lines.append("")

    if meeting.decisions:
        lines.append("Decisions:")
        lines.extend(f"- {decision.decision_text}" for decision in meeting.decisions)
        lines.append("")

    if tasks:
        lines.append("Action Items:")
        for task in tasks:
            owners = ", ".join(assignees_by_task.get(task.id, [])) or "Unassigned"
            due = task.due_date.strftime("%d %b %Y") if task.due_date else "no due date set"
            lines.append(f"- {task.title} (Owner: {owners}; Due: {due})")

    return "\n".join(lines).strip()


@meetings_bp.route("/<int:meeting_id>/audio", methods=["POST"])
@login_required
@roles_required("EA")
def upload_audio(meeting_id):
    meeting = _get_meeting_or_404(meeting_id)
    file = request.files.get("audio_file")
    try:
        attachment_service.save_audio_attachment(file, entity_type="MEETING", entity_id=meeting.id, uploaded_by=current_user.username)
        flash("Audio uploaded. You can now generate a transcript.", "success")
    except attachment_service.AttachmentValidationError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-transcript")


@meetings_bp.route("/<int:meeting_id>/audio/<int:attachment_id>")
@login_required
@roles_required("MD", "EA")
def audio_file(meeting_id, attachment_id):
    """Streams a meeting's uploaded audio back for in-page playback (see
    app/models/attachment.py — the physical path is never exposed directly,
    only through this access-checked route), so whoever is reviewing the
    transcript can listen alongside it to confirm the AI got it right.
    """
    meeting = _get_meeting_or_404(meeting_id)
    attachment = Attachment.query.filter_by(id=attachment_id, entity_type="MEETING", entity_id=str(meeting.id)).first()
    if attachment is None:
        abort(404)
    path = attachment_service.attachment_path(attachment)
    if not os.path.exists(path):
        abort(404)
    return send_file(path, mimetype=attachment.mime_type or "audio/mpeg")


@meetings_bp.route("/<int:meeting_id>/audio/<int:attachment_id>/delete", methods=["POST"])
@login_required
@roles_required("EA")
def delete_audio(meeting_id, attachment_id):
    meeting = _get_meeting_or_404(meeting_id)
    attachment = Attachment.query.filter_by(id=attachment_id, entity_type="MEETING", entity_id=str(meeting.id)).first()
    if attachment is None:
        abort(404)
    attachment_service.delete_attachment(attachment)
    flash("Audio removed.", "success")
    return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-transcript")


@meetings_bp.route("/<int:meeting_id>/transcribe", methods=["POST"])
@login_required
@roles_required("EA")
def transcribe(meeting_id):
    meeting = _get_meeting_or_404(meeting_id)
    if not _require_ai_configured():
        return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-transcript")

    attachment = (
        Attachment.query.filter_by(entity_type="MEETING", entity_id=str(meeting.id))
        .order_by(Attachment.uploaded_at.desc())
        .first()
    )
    if attachment is None:
        flash("Upload a meeting audio file first.", "danger")
        return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-transcript")

    try:
        meeting_ai_service.generate_transcript(meeting, attachment=attachment, requested_by=current_user.username)
        flash("Transcript generated. Review and edit it before relying on it.", "success")
    except meeting_ai_service.MeetingAIError as exc:
        flash(f"Transcription failed: {exc}", "danger")
    return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-transcript")


@meetings_bp.route("/<int:meeting_id>/transcript/save", methods=["POST"])
@login_required
@roles_required("EA")
def save_transcript(meeting_id):
    meeting = _get_meeting_or_404(meeting_id)
    try:
        meeting_ai_service.save_transcript_edit(meeting, text=request.form.get("transcript_text", ""), edited_by=current_user.username)
        flash("Transcript saved.", "success")
    except meeting_ai_service.MeetingAIError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-transcript")


@meetings_bp.route("/<int:meeting_id>/summarize", methods=["POST"])
@login_required
@roles_required("EA")
def summarize(meeting_id):
    meeting = _get_meeting_or_404(meeting_id)
    if not _require_ai_configured():
        return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-transcript")

    source_text = request.form.get("source_text", "").strip()
    if not source_text and meeting.transcript:
        source_text = meeting.transcript.transcript_text
    if not source_text:
        flash("There is no transcript or notes to summarize yet.", "danger")
        return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-transcript")

    try:
        meeting_ai_service.generate_summary(meeting, source_text=source_text, requested_by=current_user.username)
        flash("Summary generated — AI Generated, review before relying on it.", "success")
    except meeting_ai_service.MeetingAIError as exc:
        flash(f"Summarization failed: {exc}", "danger")
    return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-transcript")


@meetings_bp.route("/<int:meeting_id>/summary/save", methods=["POST"])
@login_required
@roles_required("EA")
def save_summary(meeting_id):
    meeting = _get_meeting_or_404(meeting_id)
    try:
        meeting_ai_service.save_summary_edit(meeting, text=request.form.get("summary_text", ""), edited_by=current_user.username)
        flash("Summary saved.", "success")
    except meeting_ai_service.MeetingAIError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-transcript")


@meetings_bp.route("/<int:meeting_id>/extract-actions", methods=["POST"])
@login_required
@roles_required("EA")
def extract_actions(meeting_id):
    meeting = _get_meeting_or_404(meeting_id)
    if not _require_ai_configured():
        return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-transcript")

    source_text = ""
    if meeting.summary:
        source_text = meeting.summary.summary_text
    elif meeting.transcript:
        source_text = meeting.transcript.transcript_text

    if not source_text:
        flash("Generate a summary or transcript first.", "danger")
        return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-transcript")

    try:
        suggestions = meeting_ai_service.extract_suggested_actions(meeting, source_text=source_text, requested_by=current_user.username)
    except meeting_ai_service.MeetingAIError as exc:
        flash(f"Action extraction failed: {exc}", "danger")
        return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-transcript")

    if not suggestions:
        flash("The AI did not find any clear action items in this text.", "info")
        return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-transcript")

    return render_template("meetings/workspace.html", **build_workspace_context(meeting, ai_suggestions=suggestions))


def _create_actions_from_indexed_form(meeting, *, count: int, created_by: str) -> int:
    """Parse action_{i}_* fields and create Task rows for every selected
    suggestion that has a title and at least one assignee. Shared by the
    AI action-extraction review form and the notes-draft review form —
    both submit suggested actions in this same indexed shape.
    """
    created = 0
    for i in range(count):
        if request.form.get(f"action_{i}_selected") != "1":
            continue
        title = request.form.get(f"action_{i}_title", "").strip()
        employee_codes = [c for c in request.form.getlist(f"action_{i}_employee_codes") if c.strip()]
        if not title or not employee_codes:
            continue  # EA must have filled in a title and picked at least one assignee — never auto-assign

        raw_priority = request.form.get(f"action_{i}_priority", "MEDIUM")
        try:
            priority = TaskPriority(raw_priority)
        except ValueError:
            priority = TaskPriority.MEDIUM

        due_date_raw = request.form.get(f"action_{i}_due_date", "")
        due_date = date.fromisoformat(due_date_raw) if due_date_raw else None

        try:
            task_service.create_meeting_action(
                meeting,
                title=title,
                description=request.form.get(f"action_{i}_description", "").strip() or None,
                employee_codes=employee_codes,
                priority=priority,
                due_date=due_date,
                created_by=created_by,
            )
            created += 1
        except task_service.TaskValidationError:
            continue
    return created


@meetings_bp.route("/<int:meeting_id>/actions/create-from-ai", methods=["POST"])
@login_required
@roles_required("EA")
def create_actions_from_ai(meeting_id):
    meeting = _get_meeting_or_404(meeting_id)
    count = request.form.get("suggestion_count", type=int) or 0
    created = _create_actions_from_indexed_form(meeting, count=count, created_by=current_user.username)

    if created:
        flash(f"Created {created} action item(s) from the reviewed AI suggestions.", "success")
    else:
        flash("No action items were created — select at least one suggestion and assign an employee to it.", "warning")
    return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-actions")


@meetings_bp.route("/<int:meeting_id>/followup-email", methods=["POST"])
@login_required
@roles_required("EA")
def draft_followup_email(meeting_id):
    meeting = _get_meeting_or_404(meeting_id)
    if not _require_ai_configured():
        return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-followup")

    context = build_workspace_context(meeting)
    if not meeting.notes and not meeting.decisions and not context["tasks"]:
        flash("Add some notes, decisions, or action items before drafting a follow-up email.", "danger")
        return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-followup")

    source_text = _build_followup_source_text(meeting, context["tasks"], context["assignees_by_task"])
    try:
        meeting_ai_service.draft_followup_email(meeting, source_text=source_text, requested_by=current_user.username)
        flash("Follow-up email drafted — review before sending.", "success")
    except meeting_ai_service.MeetingAIError as exc:
        flash(f"Drafting the follow-up email failed: {exc}", "danger")
    return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-followup")


@meetings_bp.route("/<int:meeting_id>/followup-email/save", methods=["POST"])
@login_required
@roles_required("EA")
def save_followup_email(meeting_id):
    meeting = _get_meeting_or_404(meeting_id)
    try:
        meeting_ai_service.save_followup_email_edit(
            meeting, subject=request.form.get("email_subject", ""), body=request.form.get("email_body", ""),
            to_emails=request.form.get("email_to", ""), edited_by=current_user.username,
        )
        flash("Follow-up email saved.", "success")
    except meeting_ai_service.MeetingAIError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-followup")


@meetings_bp.route("/<int:meeting_id>/split-notes", methods=["POST"])
@login_required
@roles_required("EA")
def split_notes(meeting_id):
    meeting = _get_meeting_or_404(meeting_id)
    if not _require_ai_configured():
        return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-overview")

    source_text = request.form.get("source_text", "").strip()
    if not source_text:
        flash("Type what happened in the meeting first.", "danger")
        return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-overview")

    try:
        draft = meeting_ai_service.split_notes(meeting, source_text=source_text, requested_by=current_user.username)
    except meeting_ai_service.MeetingAIError as exc:
        flash(f"Splitting the notes failed: {exc}", "danger")
        return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-overview")

    if not draft.notes and not draft.decisions and not draft.actions:
        flash("The AI didn't find any notes, decisions or actions in that text.", "info")
        return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-overview")

    return render_template("meetings/workspace.html", **build_workspace_context(meeting, notes_draft=draft))


@meetings_bp.route("/<int:meeting_id>/notes-draft/save", methods=["POST"])
@login_required
@roles_required("EA")
def save_notes_draft(meeting_id):
    meeting = _get_meeting_or_404(meeting_id)

    note_count = request.form.get("note_count", type=int) or 0
    notes_created = 0
    for i in range(note_count):
        text = request.form.get(f"note_{i}", "").strip()
        if text:
            meeting_service.add_note(meeting, note_text=text, created_by=current_user.username)
            notes_created += 1

    decision_count = request.form.get("decision_count", type=int) or 0
    decisions_created = 0
    for i in range(decision_count):
        text = request.form.get(f"decision_{i}", "").strip()
        if text:
            meeting_service.add_decision(meeting, decision_text=text, created_by=current_user.username)
            decisions_created += 1

    action_count = request.form.get("action_count", type=int) or 0
    actions_created = _create_actions_from_indexed_form(meeting, count=action_count, created_by=current_user.username)

    flash(f"Saved {notes_created} note(s), {decisions_created} decision(s), {actions_created} action item(s).", "success")
    return redirect(url_for("meetings.workspace", meeting_id=meeting.id) + "#tab-overview")
