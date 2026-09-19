from flask import abort, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models.notification import Notification
from app.notifications import notifications_bp
from app.services import notification_service


def resolve_notification_url(notification: Notification):
    """Only link to something the recipient can actually open — e.g. an
    employee notified about a project assignment has no project detail
    page (that's MD/EA only), so that notification just isn't clickable.
    """
    if notification.entity_type == "TASK" and notification.entity_id:
        return url_for("tasks.detail", task_id=notification.entity_id)
    if notification.entity_type == "PROJECT" and notification.entity_id and current_user.role.value in ("MD", "EA"):
        return url_for("projects.detail", project_id=notification.entity_id)
    if notification.entity_type == "CALENDAR" and current_user.role.value in ("MD", "EA"):
        return url_for("calendar.status")
    return None


@notifications_bp.route("/")
@login_required
def list_view():
    notifications = (
        Notification.query.filter_by(recipient_username=current_user.username)
        .order_by(Notification.created_at.desc())
        .limit(100)
        .all()
    )
    links = {n.id: resolve_notification_url(n) for n in notifications}
    return render_template("notifications/list.html", notifications=notifications, links=links)


@notifications_bp.route("/<int:notification_id>/read", methods=["POST"])
@login_required
def mark_read(notification_id):
    notification = db.session.get(Notification, notification_id)
    if notification is None or notification.recipient_username != current_user.username:
        abort(404)
    notification_service.mark_read(notification)
    target = resolve_notification_url(notification)
    return redirect(target or url_for("notifications.list_view"))


@notifications_bp.route("/mark-all-read", methods=["POST"])
@login_required
def mark_all_read():
    notification_service.mark_all_read(current_user.username)
    return redirect(request.referrer or url_for("notifications.list_view"))
