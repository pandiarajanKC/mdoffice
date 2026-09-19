from datetime import datetime, timedelta

from app.dashboard.routes import _action_items_summary, _meeting_summary, _task_summary
from app.extensions import db
from app.models.task import Task, TaskPriority, TaskStatus, TaskType
from app.services import meeting_service


def _task(**overrides):
    task = Task(
        task_type=overrides.pop("task_type"),
        title=overrides.pop("title", "Task"),
        status=overrides.pop("status", TaskStatus.OPEN),
        priority=overrides.pop("priority", TaskPriority.MEDIUM),
        created_by=overrides.pop("created_by", "admin1"),
        **overrides,
    )
    db.session.add(task)
    db.session.commit()
    return task


def test_action_items_excludes_standalone_tasks(app, db):
    _task(task_type=TaskType.MEETING_ACTION, title="From a meeting")
    _task(task_type=TaskType.PROJECT_TASK, title="From a project")
    _task(task_type=TaskType.GENERAL_FOLLOWUP, title="Standalone")

    action_items = _action_items_summary()
    tasks = _task_summary()

    assert action_items["total"] == 2
    assert tasks["total"] == 1


def test_action_items_and_tasks_never_overlap(app, db):
    for i in range(3):
        _task(task_type=TaskType.MEETING_ACTION, title=f"Action {i}")
    for i in range(4):
        _task(task_type=TaskType.GENERAL_FOLLOWUP, title=f"Task {i}")

    action_items = _action_items_summary()
    tasks = _task_summary()

    assert action_items["total"] == 3
    assert tasks["total"] == 4
    # The two cards must add up to the whole Task table with nothing
    # double-counted and nothing dropped.
    assert action_items["total"] + tasks["total"] == Task.query.count()


def test_action_items_breakdown_respects_status(app, db):
    _task(task_type=TaskType.MEETING_ACTION, status=TaskStatus.OPEN)
    _task(task_type=TaskType.PROJECT_TASK, status=TaskStatus.IN_PROGRESS)
    _task(task_type=TaskType.PROJECT_TASK, status=TaskStatus.COMPLETED)
    _task(task_type=TaskType.GENERAL_FOLLOWUP, status=TaskStatus.COMPLETED)  # must not leak in

    summary = _action_items_summary()

    assert summary["total"] == 3
    assert summary["new"] == 1
    assert summary["in_progress"] == 1
    assert summary["completed"] == 1


def _meeting(db, *, start, title="Meeting"):
    return meeting_service.create_meeting(
        title=title, start_datetime=start, end_datetime=start + timedelta(hours=1), created_by="admin1"
    )


def test_meeting_summary_only_counts_today(app, db):
    now = datetime.now()
    _meeting(db, start=now.replace(hour=9, minute=0, second=0, microsecond=0), title="Today AM")
    _meeting(db, start=now + timedelta(days=3), title="Later this week")
    _meeting(db, start=now - timedelta(days=1), title="Yesterday")

    summary = _meeting_summary()

    assert summary["total"] == 1


def test_meeting_summary_breakdown_scoped_to_today(app, db):
    now = datetime.now()
    scheduled = _meeting(db, start=now.replace(hour=9, minute=0, second=0, microsecond=0), title="Scheduled today")
    in_progress = _meeting(db, start=now.replace(hour=10, minute=0, second=0, microsecond=0), title="In progress today")
    completed = _meeting(db, start=now.replace(hour=8, minute=0, second=0, microsecond=0), title="Completed today")
    meeting_service.start_meeting(in_progress, performed_by="admin1")
    meeting_service.start_meeting(completed, performed_by="admin1")
    meeting_service.complete_meeting(completed, performed_by="admin1")
    # A completed meeting from a prior day must not count toward today's total.
    old_meeting = _meeting(db, start=now - timedelta(days=5), title="Completed last week")
    meeting_service.start_meeting(old_meeting, performed_by="admin1")
    meeting_service.complete_meeting(old_meeting, performed_by="admin1")

    summary = _meeting_summary()

    assert summary["total"] == 3
    assert summary["new"] == 1
    assert summary["in_progress"] == 1
    assert summary["completed"] == 1
