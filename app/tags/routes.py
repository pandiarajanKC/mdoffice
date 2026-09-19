from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models.app_user import UserRole
from app.models.tag import Tag
from app.services import tag_service
from app.tags import tags_bp
from app.tags.forms import TagCreateForm
from app.utils.rbac import roles_required
from app.utils.task_display import task_source_label


def _get_tag_or_404(tag_id: int) -> Tag:
    tag = db.session.get(Tag, tag_id)
    if tag is None:
        abort(404)
    return tag


def _redirect_back():
    if request.referrer:
        return redirect(request.referrer)
    return redirect(url_for("tags.master"))


@tags_bp.route("/")
@login_required
@roles_required("MD", "EA")
def master():
    """Tag Master: the full list of tags EA has defined, with how many
    items each currently labels. Creating/deleting a tag is EA-only;
    MD can browse and drill into a tag's contents read-only.
    """
    tags = tag_service.list_all_tags()
    counts = tag_service.usage_counts()
    return render_template(
        "tags/master.html",
        tags=tags,
        counts=counts,
        is_ea=current_user.role == UserRole.EA,
        form=TagCreateForm(),
    )


@tags_bp.route("/create", methods=["POST"])
@login_required
@roles_required("EA")
def create():
    form = TagCreateForm()
    if not form.validate_on_submit():
        flash("Please provide a tag name.", "danger")
        return redirect(url_for("tags.master"))

    try:
        tag = tag_service.create_tag(form.name.data, created_by=current_user.username, color=form.color.data)
        flash(f"Tag '{tag.name}' created.", "success")
    except tag_service.TagValidationError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("tags.master"))


@tags_bp.route("/<int:tag_id>/delete", methods=["POST"])
@login_required
@roles_required("EA")
def delete(tag_id):
    tag = _get_tag_or_404(tag_id)
    name = tag.name
    tag_service.delete_tag(tag)
    flash(f"Tag '{name}' deleted.", "info")
    return redirect(url_for("tags.master"))


@tags_bp.route("/<int:tag_id>")
@login_required
@roles_required("MD", "EA")
def view(tag_id):
    """Tag-wise contents page: every meeting/project/task/action item
    currently carrying this tag.
    """
    tag = _get_tag_or_404(tag_id)
    grouped = tag_service.get_entities_for_tag(tag)
    task_labels = {t.id: task_source_label(t, viewer_is_employee=False) for t in grouped["tasks"]}

    return render_template(
        "tags/view.html",
        tag=tag,
        meetings=grouped["meetings"],
        projects=grouped["projects"],
        tasks=grouped["tasks"],
        task_labels=task_labels,
    )


@tags_bp.route("/assign", methods=["POST"])
@login_required
@roles_required("EA")
def assign():
    """Posted from the small inline "Tags" widget on a meeting/project/task
    page — creates the tag on first use, then links it to that item.
    """
    entity_type = (request.form.get("entity_type") or "").strip().upper()
    entity_id = (request.form.get("entity_id") or "").strip()
    tag_name = (request.form.get("tag_name") or "").strip()

    try:
        tag, newly_linked = tag_service.tag_entity(
            tag_name=tag_name, entity_type=entity_type, entity_id=entity_id, tagged_by=current_user.username
        )
        flash(f"Tagged with '{tag.name}'." if newly_linked else f"Already tagged with '{tag.name}'.",
              "success" if newly_linked else "info")
    except tag_service.TagValidationError as exc:
        flash(str(exc), "danger")
    return _redirect_back()


@tags_bp.route("/unassign/<int:assignment_id>", methods=["POST"])
@login_required
@roles_required("EA")
def unassign(assignment_id):
    tag_service.untag_entity(assignment_id)
    flash("Tag removed.", "info")
    return _redirect_back()
