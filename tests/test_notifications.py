from datetime import date, datetime, timedelta

import pytest

from app.models.mixins import utcnow
from app.models.notification import Notification
from app.models.task import TaskStatus
from app.services import (
    escalation_service,
    meeting_service,
    notification_service,
    project_service,
    reminder_service,
    task_service,
)


def _meeting(db):
    return meeting_service.create_meeting(
        title="Steering Committee", start_datetime=datetime.now(), end_datetime=datetime.now() + timedelta(hours=1),
        created_by="admin1",
    )


def test_task_assignment_creates_notification(app, db):
    meeting = _meeting(db)
    task_service.create_meeting_action(meeting, title="Prepare report", employee_codes=["1001", "1002"], created_by="admin1")

    n1 = Notification.query.filter_by(recipient_username="1001", notification_type="TASK_ASSIGNED").first()
    n2 = Notification.query.filter_by(recipient_username="1002", notification_type="TASK_ASSIGNED").first()
    assert n1 is not None and n2 is not None
    assert "Prepare report" in n1.title


def test_status_change_notifies_other_assignees_not_the_actor(app, db):
    meeting = _meeting(db)
    task = task_service.create_meeting_action(meeting, title="X", employee_codes=["1001", "1002"], created_by="admin1")
    Notification.query.delete()  # clear assignment notifications for a clean check
    db.session.commit()

    task_service.update_status(task, new_status=TaskStatus.IN_PROGRESS, performed_by="1001")

    assert Notification.query.filter_by(recipient_username="1002", notification_type="STATUS_CHANGED").count() == 1
    assert Notification.query.filter_by(recipient_username="1001", notification_type="STATUS_CHANGED").count() == 0


def test_task_completed_notifies_creator(app, db):
    meeting = _meeting(db)
    task = task_service.create_meeting_action(meeting, title="X", employee_codes=["1001"], created_by="admin1")
    Notification.query.delete()
    db.session.commit()

    task_service.update_status(task, new_status=TaskStatus.COMPLETED, performed_by="1001")

    n = Notification.query.filter_by(recipient_username="admin1", notification_type="TASK_COMPLETED").first()
    assert n is not None


def test_completed_by_creator_does_not_self_notify(app, db):
    meeting = _meeting(db)
    task = task_service.create_meeting_action(meeting, title="X", employee_codes=["1001"], created_by="admin1")
    Notification.query.delete()
    db.session.commit()

    task_service.update_status(task, new_status=TaskStatus.COMPLETED, performed_by="admin1")

    assert Notification.query.filter_by(notification_type="TASK_COMPLETED").count() == 0


def test_project_member_added_notifies_them(app, db):
    project = project_service.create_project(project_code="P-1", name="X", owner_employee_code="1001", created_by="admin1")
    project_service.add_member(project, employee_code="2002", role="Contributor", added_by="admin1")

    n = Notification.query.filter_by(recipient_username="2002", notification_type="PROJECT_ASSIGNED").first()
    assert n is not None


def test_mark_read_and_unread_count(app, db):
    meeting = _meeting(db)
    task_service.create_meeting_action(meeting, title="X", employee_codes=["1001"], created_by="admin1")
    assert notification_service.unread_count("1001") == 1

    n = Notification.query.filter_by(recipient_username="1001").first()
    notification_service.mark_read(n)
    assert notification_service.unread_count("1001") == 0
    assert n.read_at is not None


def test_mark_all_read(app, db):
    meeting = _meeting(db)
    task_service.create_meeting_action(meeting, title="A", employee_codes=["1001"], created_by="admin1")
    task_service.create_meeting_action(meeting, title="B", employee_codes=["1001"], created_by="admin1")
    assert notification_service.unread_count("1001") == 2

    marked = notification_service.mark_all_read("1001")
    assert marked == 2
    assert notification_service.unread_count("1001") == 0


def test_already_notified_prevents_duplicate(app, db):
    meeting = _meeting(db)
    task = task_service.create_meeting_action(meeting, title="X", employee_codes=["1001"], created_by="admin1")
    assert notification_service.already_notified(
        recipient_username="1001", notification_type="TASK_ASSIGNED", entity_type="TASK", entity_id=str(task.id)
    )
    assert not notification_service.already_notified(
        recipient_username="9999", notification_type="TASK_ASSIGNED", entity_type="TASK", entity_id=str(task.id)
    )


def test_reminder_must_be_in_future(app, db):
    meeting = _meeting(db)
    task = task_service.create_meeting_action(meeting, title="X", employee_codes=["1001"], created_by="admin1")
    with pytest.raises(reminder_service.ReminderValidationError):
        reminder_service.create_reminder(task, remind_at=utcnow() - timedelta(hours=1), note="past", created_by="admin1")


def test_reminder_fires_notification_to_assignees(app, db):
    meeting = _meeting(db)
    task = task_service.create_meeting_action(meeting, title="Follow up task", employee_codes=["1001", "1002"], created_by="admin1")
    reminder = reminder_service.create_reminder(
        task, remind_at=utcnow() + timedelta(seconds=1), note="Check with Finance", created_by="admin1"
    )
    reminder.remind_at = utcnow() - timedelta(seconds=1)  # simulate time passing
    db.session.commit()

    fired = reminder_service.fire_due_reminders()
    assert fired == 1
    assert reminder.notified_at is not None
    assert Notification.query.filter_by(recipient_username="1001", notification_type="REMINDER").count() == 1
    assert Notification.query.filter_by(recipient_username="1002", notification_type="REMINDER").count() == 1


def test_reminder_only_fires_once(app, db):
    meeting = _meeting(db)
    task = task_service.create_meeting_action(meeting, title="X", employee_codes=["1001"], created_by="admin1")
    reminder = reminder_service.create_reminder(task, remind_at=utcnow() + timedelta(seconds=1), note="x", created_by="admin1")
    reminder.remind_at = utcnow() - timedelta(seconds=1)
    db.session.commit()

    assert reminder_service.fire_due_reminders() == 1
    assert reminder_service.fire_due_reminders() == 0  # already notified


def test_dismissed_reminder_never_fires(app, db):
    meeting = _meeting(db)
    task = task_service.create_meeting_action(meeting, title="X", employee_codes=["1001"], created_by="admin1")
    reminder = reminder_service.create_reminder(task, remind_at=utcnow() + timedelta(seconds=1), note="x", created_by="admin1")
    reminder.remind_at = utcnow() - timedelta(seconds=1)
    db.session.commit()
    reminder_service.dismiss_reminder(reminder, performed_by="admin1")

    assert reminder_service.fire_due_reminders() == 0


def test_escalation_due_tomorrow_notifies_assignee(app, db):
    meeting = _meeting(db)
    task_service.create_meeting_action(
        meeting, title="X", employee_codes=["1001"], created_by="admin1", due_date=date.today() + timedelta(days=1)
    )
    counts = escalation_service.run_escalations()
    assert counts[escalation_service.DUE_TOMORROW] == 1
    assert Notification.query.filter_by(recipient_username="1001", notification_type="DUE_TOMORROW").count() == 1


def test_escalation_overdue_3_days_notifies_ea_not_md(app, db):
    from app.models.app_user import AppUser, UserRole

    db.session.add(AppUser(username="admin1", role=UserRole.EA, password_hash="x"))
    db.session.add(AppUser(username="admin", role=UserRole.MD, password_hash="x"))
    db.session.commit()

    meeting = _meeting(db)
    task_service.create_meeting_action(
        meeting, title="X", employee_codes=["1001"], created_by="admin1", due_date=date.today() - timedelta(days=3)
    )
    counts = escalation_service.run_escalations()
    assert counts[escalation_service.OVERDUE_3D_EA] == 1
    assert Notification.query.filter_by(recipient_username="admin1", notification_type="OVERDUE_3D_EA").count() == 1
    assert Notification.query.filter_by(recipient_username="admin", notification_type="OVERDUE_3D_EA").count() == 0


def test_escalation_overdue_7_days_notifies_md(app, db):
    from app.models.app_user import AppUser, UserRole

    db.session.add(AppUser(username="admin", role=UserRole.MD, password_hash="x"))
    db.session.commit()

    meeting = _meeting(db)
    task_service.create_meeting_action(
        meeting, title="X", employee_codes=["1001"], created_by="admin1", due_date=date.today() - timedelta(days=9)
    )
    counts = escalation_service.run_escalations()
    assert counts[escalation_service.OVERDUE_7D_MD] == 1
    assert Notification.query.filter_by(recipient_username="admin", notification_type="OVERDUE_7D_MD").count() == 1


def test_escalation_does_not_duplicate_on_repeated_run(app, db):
    meeting = _meeting(db)
    task_service.create_meeting_action(
        meeting, title="X", employee_codes=["1001"], created_by="admin1", due_date=date.today() + timedelta(days=1)
    )
    escalation_service.run_escalations()
    escalation_service.run_escalations()
    assert Notification.query.filter_by(notification_type="DUE_TOMORROW").count() == 1


def test_nudge_notifies_all_assignees(app, db):
    meeting = _meeting(db)
    task = task_service.create_meeting_action(meeting, title="Follow up task", employee_codes=["1001", "1002"], created_by="admin1")
    Notification.query.delete()
    db.session.commit()

    count = task_service.nudge_assignees(task, sent_by="admin1")
    assert count == 2
    assert Notification.query.filter_by(recipient_username="1001", notification_type="NUDGE").count() == 1
    assert Notification.query.filter_by(recipient_username="1002", notification_type="NUDGE").count() == 1


def test_nudge_without_assignees_raises(app, db):
    meeting = _meeting(db)
    task = task_service.create_meeting_action(meeting, title="X", employee_codes=["1001"], created_by="admin1")
    task.assignees.clear()
    db.session.commit()

    with pytest.raises(task_service.TaskValidationError):
        task_service.nudge_assignees(task, sent_by="admin1")


def test_escalation_skips_completed_tasks(app, db):
    meeting = _meeting(db)
    task = task_service.create_meeting_action(
        meeting, title="X", employee_codes=["1001"], created_by="admin1", due_date=date.today() - timedelta(days=5)
    )
    task_service.update_status(task, new_status=TaskStatus.COMPLETED, performed_by="1001")
    Notification.query.delete()
    db.session.commit()

    escalation_service.run_escalations()
    assert Notification.query.count() == 0
