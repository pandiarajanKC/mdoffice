from datetime import date, timedelta

import pytest

from app.models.task import TaskPriority, TaskStatus
from app.services import meeting_service, task_service


def _meeting(db):
    from datetime import datetime

    return meeting_service.create_meeting(
        title="Steering Committee",
        start_datetime=datetime.now(),
        end_datetime=datetime.now() + timedelta(hours=1),
        created_by="admin1",
    )


def test_create_meeting_action_requires_title(app, db):
    meeting = _meeting(db)
    with pytest.raises(task_service.TaskValidationError):
        task_service.create_meeting_action(meeting, title="  ", employee_codes=["1001"], created_by="admin1")


def test_create_meeting_action_requires_assignee(app, db):
    meeting = _meeting(db)
    with pytest.raises(task_service.TaskValidationError):
        task_service.create_meeting_action(meeting, title="Prepare report", employee_codes=[], created_by="admin1")


def test_create_meeting_action_success(app, db):
    meeting = _meeting(db)
    task = task_service.create_meeting_action(
        meeting,
        title="Prepare validation report",
        employee_codes=["1001", "1002", "1001"],  # duplicate should be ignored
        created_by="admin1",
        priority=TaskPriority.HIGH,
        due_date=date.today() + timedelta(days=5),
    )
    assert task.status == TaskStatus.OPEN
    assert len(task.assignees) == 2
    assert {a.employee_code for a in task.assignees} == {"1001", "1002"}


def test_task_overdue_calculation(app, db):
    meeting = _meeting(db)
    task = task_service.create_meeting_action(
        meeting, title="Late task", employee_codes=["1001"], created_by="admin1",
        due_date=date.today() - timedelta(days=2),
    )
    assert task.is_overdue is True

    task_service.update_status(task, new_status=TaskStatus.COMPLETED, performed_by="admin1")
    assert task.is_overdue is False  # completed tasks are never overdue


def test_update_status_sets_completed_at(app, db):
    meeting = _meeting(db)
    task = task_service.create_meeting_action(meeting, title="X", employee_codes=["1001"], created_by="admin1")
    task_service.update_status(task, new_status=TaskStatus.COMPLETED, performed_by="admin1")
    assert task.completed_at is not None
    assert task.progress_percent == 100


def test_update_status_reopening_clears_completed_at(app, db):
    meeting = _meeting(db)
    task = task_service.create_meeting_action(meeting, title="X", employee_codes=["1001"], created_by="admin1")
    task_service.update_status(task, new_status=TaskStatus.COMPLETED, performed_by="admin1")
    task_service.update_status(task, new_status=TaskStatus.IN_PROGRESS, performed_by="admin1")
    assert task.completed_at is None


def test_update_progress_to_100_completes_task(app, db):
    meeting = _meeting(db)
    task = task_service.create_meeting_action(meeting, title="X", employee_codes=["1001"], created_by="admin1")
    task_service.update_progress(task, progress_percent=100, performed_by="1001")
    assert task.status == TaskStatus.COMPLETED


def test_update_progress_clamped(app, db):
    meeting = _meeting(db)
    task = task_service.create_meeting_action(meeting, title="X", employee_codes=["1001"], created_by="admin1")
    task_service.update_progress(task, progress_percent=150, performed_by="1001")
    assert task.progress_percent == 100
    task_service.update_progress(task, progress_percent=-10, performed_by="1001")
    assert task.progress_percent == 0


def test_meeting_followup_status_transitions(app, db):
    meeting = _meeting(db)
    task = task_service.create_meeting_action(
        meeting, title="X", employee_codes=["1001"], created_by="admin1",
        due_date=date.today() + timedelta(days=5),
    )
    assert meeting_service.followup_status(meeting) == "Actions Pending"

    task_service.update_status(task, new_status=TaskStatus.IN_PROGRESS, performed_by="1001")
    assert meeting_service.followup_status(meeting) == "Actions In Progress"

    task_service.update_status(task, new_status=TaskStatus.COMPLETED, performed_by="1001")
    assert meeting_service.followup_status(meeting) == "All Actions Completed"


def test_meeting_followup_overdue(app, db):
    meeting = _meeting(db)
    task_service.create_meeting_action(
        meeting, title="X", employee_codes=["1001"], created_by="admin1",
        due_date=date.today() - timedelta(days=1),
    )
    assert meeting_service.followup_status(meeting) == "Actions Overdue"


def test_update_task_details_edits_fields_and_reassigns(app, db):
    meeting = _meeting(db)
    task = task_service.create_meeting_action(meeting, title="Original", employee_codes=["1001"], created_by="admin1")

    task_service.update_task_details(
        task,
        title="Revised title",
        description="More context",
        priority=TaskPriority.CRITICAL,
        due_date=date.today() + timedelta(days=3),
        employee_codes=["1002", "1003"],
        performed_by="admin1",
    )

    assert task.title == "Revised title"
    assert task.description == "More context"
    assert task.priority == TaskPriority.CRITICAL
    assert task.due_date == date.today() + timedelta(days=3)
    assert sorted(a.employee_code for a in task.assignees) == ["1002", "1003"]


def test_update_task_details_requires_title(app, db):
    meeting = _meeting(db)
    task = task_service.create_meeting_action(meeting, title="X", employee_codes=["1001"], created_by="admin1")
    with pytest.raises(task_service.TaskValidationError):
        task_service.update_task_details(task, title="  ", employee_codes=["1001"], performed_by="admin1")


def test_update_task_details_requires_assignee(app, db):
    meeting = _meeting(db)
    task = task_service.create_meeting_action(meeting, title="X", employee_codes=["1001"], created_by="admin1")
    with pytest.raises(task_service.TaskValidationError):
        task_service.update_task_details(task, title="X", employee_codes=[], performed_by="admin1")


def test_update_task_details_keeps_unchanged_assignees_intact(app, db):
    # An assignee kept across the edit shouldn't be deleted-then-recreated
    # (would lose its assigned_at/assigned_by history for no reason).
    meeting = _meeting(db)
    task = task_service.create_meeting_action(meeting, title="X", employee_codes=["1001", "1002"], created_by="admin1")
    original_ids = {a.id for a in task.assignees}

    task_service.update_task_details(task, title="X", employee_codes=["1001", "1003"], performed_by="admin1")

    kept = next(a for a in task.assignees if a.employee_code == "1001")
    assert kept.id in original_ids
    assert sorted(a.employee_code for a in task.assignees) == ["1001", "1003"]


def test_delete_task_removes_task_and_dependents(app, db):
    from app.extensions import db as _db
    from app.models.task import Task, TaskAssignee
    from app.models.task_progress_update import TaskProgressUpdate

    meeting = _meeting(db)
    task = task_service.create_meeting_action(meeting, title="X", employee_codes=["1001"], created_by="admin1")
    task_id = task.id
    task_service.add_progress_update(task, update_text="update", performed_by="1001", employee_code="1001")

    task_service.delete_task(task, performed_by="admin1")

    assert _db.session.get(Task, task_id) is None
    assert TaskAssignee.query.filter_by(task_id=task_id).count() == 0
    assert TaskProgressUpdate.query.filter_by(task_id=task_id).count() == 0
