from datetime import datetime, timedelta

import pytest

from app.models.meeting import ConfidentialityLevel
from app.models.task import TaskStatus
from app.services import meeting_service, project_service, task_service
from app.utils.task_display import task_source_label


def _meeting_task(db, confidentiality=ConfidentialityLevel.NORMAL):
    meeting = meeting_service.create_meeting(
        title="Production Review",
        start_datetime=datetime.now(),
        end_datetime=datetime.now() + timedelta(hours=1),
        created_by="admin1",
        confidentiality_level=confidentiality,
    )
    task = task_service.create_meeting_action(meeting, title="Prepare report", employee_codes=["1001"], created_by="admin1")
    return meeting, task


def test_add_progress_update_requires_text(app, db):
    _, task = _meeting_task(db)
    with pytest.raises(task_service.TaskValidationError):
        task_service.add_progress_update(task, update_text="   ", performed_by="1001")


def test_add_progress_update_creates_entry(app, db):
    _, task = _meeting_task(db)
    entry = task_service.add_progress_update(task, update_text="Waiting for Finance.", performed_by="1001", employee_code="1001")
    assert entry.id is not None
    assert len(task.progress_updates) == 1
    assert task.progress_updates[0].update_text == "Waiting for Finance."


def test_add_progress_update_with_percent_updates_task(app, db):
    _, task = _meeting_task(db)
    task_service.add_progress_update(task, update_text="Halfway done.", performed_by="1001", employee_code="1001", progress_percent=50)
    assert task.progress_percent == 50
    assert task.status == TaskStatus.OPEN  # only completing at 100 flips status


def test_add_progress_update_to_100_completes_task(app, db):
    _, task = _meeting_task(db)
    task_service.add_progress_update(task, update_text="Done.", performed_by="1001", employee_code="1001", progress_percent=100)
    assert task.status == TaskStatus.COMPLETED


def test_task_source_label_normal_meeting_shows_title(app, db):
    _, task = _meeting_task(db, confidentiality=ConfidentialityLevel.NORMAL)
    assert task_source_label(task, viewer_is_employee=True) == "Meeting: Production Review"
    assert task_source_label(task, viewer_is_employee=False) == "Meeting: Production Review"


def test_task_source_label_confidential_meeting_hidden_from_employee(app, db):
    _, task = _meeting_task(db, confidentiality=ConfidentialityLevel.CONFIDENTIAL)
    assert task_source_label(task, viewer_is_employee=True) == "MD Office Meeting"
    # EA/MD always see the real title
    assert task_source_label(task, viewer_is_employee=False) == "Meeting: Production Review"


def test_task_source_label_restricted_meeting_hidden_from_employee(app, db):
    _, task = _meeting_task(db, confidentiality=ConfidentialityLevel.RESTRICTED)
    assert task_source_label(task, viewer_is_employee=True) == "MD Office Meeting"


def test_task_source_label_project_task_shows_project_name(app, db):
    project = project_service.create_project(
        project_code="PRJ-001", name="MES Integration", owner_employee_code="1001", created_by="admin1"
    )
    task = task_service.create_project_task(project, title="Design spec", employee_codes=["1001"], created_by="admin1")
    assert task_source_label(task, viewer_is_employee=True) == "Project: MES Integration"


def test_task_source_label_general_followup():
    from app.models.task import Task, TaskType

    task = Task(task_type=TaskType.GENERAL_FOLLOWUP, title="Follow up", created_by="admin1")
    assert task_source_label(task, viewer_is_employee=True) == "General Follow-up"
