"""In-app notification creation and read-state (master spec section 37).

Kept generic and provider-agnostic on purpose: `notify()` only ever writes
a row to `md_notification`. Email delivery can be added later by having a
listener drain unsent rows — nothing here assumes in-app is the only
channel, but nothing here sends email either (spec section 37: "Design
notification service so email notifications can later be enabled").
"""
from __future__ import annotations

from app.extensions import db
from app.models.notification import Notification


def notify(
    *,
    recipient_username: str,
    notification_type: str,
    title: str,
    message: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
) -> Notification:
    entry = Notification(
        recipient_username=recipient_username,
        notification_type=notification_type,
        title=title,
        message=message,
        entity_type=entity_type,
        entity_id=entity_id,
    )
    db.session.add(entry)
    return entry


def notify_many(recipient_usernames: list[str], **kwargs) -> list[Notification]:
    return [notify(recipient_username=code, **kwargs) for code in dict.fromkeys(recipient_usernames)]


def already_notified(*, recipient_username: str, notification_type: str, entity_type: str, entity_id: str) -> bool:
    """Idempotency check for scheduled/escalation notifications — a given
    recipient should only ever get one notification per (type, entity).
    """
    return (
        Notification.query.filter_by(
            recipient_username=recipient_username,
            notification_type=notification_type,
            entity_type=entity_type,
            entity_id=entity_id,
        ).first()
        is not None
    )


def mark_read(notification: Notification) -> Notification:
    from app.models.mixins import utcnow

    if not notification.is_read:
        notification.is_read = True
        notification.read_at = utcnow()
        db.session.commit()
    return notification


def mark_all_read(recipient_username: str) -> int:
    from app.models.mixins import utcnow

    unread = Notification.query.filter_by(recipient_username=recipient_username, is_read=False).all()
    for n in unread:
        n.is_read = True
        n.read_at = utcnow()
    db.session.commit()
    return len(unread)


def unread_count(recipient_username: str) -> int:
    return Notification.query.filter_by(recipient_username=recipient_username, is_read=False).count()


def recent_for(recipient_username: str, limit: int = 10) -> list[Notification]:
    return (
        Notification.query.filter_by(recipient_username=recipient_username)
        .order_by(Notification.created_at.desc())
        .limit(limit)
        .all()
    )
