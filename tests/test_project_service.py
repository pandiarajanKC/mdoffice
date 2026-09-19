from datetime import date, timedelta

import pytest

from app.models.project import ProjectStatus
from app.models.task import TaskPriority, TaskStatus
from app.services import project_service, task_service


def _project(db, **overrides):
    return project_service.create_project(
        project_code=overrides.pop("project_code", "MES-001"),
        name=overrides.pop("name", "MES Integration"),
        owner_employee_code=overrides.pop("owner_employee_code", "1001"),
        created_by=overrides.pop("created_by", "admin1"),
        **overrides,
    )


def test_create_project_requires_name(app, db):
    with pytest.raises(project_service.ProjectValidationError):
        project_service.create_project(project_code="X-001", name="  ", owner_employee_code="1001", created_by="admin1")


def test_create_project_requires_owner(app, db):
    with pytest.raises(project_service.ProjectValidationError):
        project_service.create_project(project_code="X-001", name="X", owner_employee_code="", created_by="admin1")


def test_create_project_rejects_duplicate_code(app, db):
    _project(db, project_code="DUP-001")
    with pytest.raises(project_service.ProjectValidationError):
        _project(db, project_code="DUP-001")


def test_generate_project_code_avoids_collision(app, db):
    p1 = project_service.create_project(
        project_code=project_service.generate_project_code("MES Integration"),
        name="MES Integration", owner_employee_code="1001", created_by="admin1",
    )
    p2 = project_service.create_project(
        project_code=project_service.generate_project_code("MES Integration"),
        name="MES Integration Phase 2", owner_employee_code="1001", created_by="admin1",
    )
    assert p1.project_code != p2.project_code


def test_update_status_sets_completed_at(app, db):
    project = _project(db)
    project_service.update_status(project, new_status=ProjectStatus.COMPLETED, performed_by="admin1")
    assert project.completed_at is not None


def test_add_member_and_reject_duplicate(app, db):
    project = _project(db)
    project_service.add_member(project, employee_code="2002", role="Lead", added_by="admin1")
    with pytest.raises(project_service.ProjectValidationError):
        project_service.add_member(project, employee_code="2002", role="Contributor", added_by="admin1")


def test_remove_member(app, db):
    project = _project(db)
    member = project_service.add_member(project, employee_code="2002", role="Lead", added_by="admin1")
    project_service.remove_member(project, member, performed_by="admin1")
    assert len(project.members) == 0


def test_progress_recalculates_from_tasks(app, db):
    project = _project(db)
    t1 = task_service.create_project_task(project, title="Task 1", employee_codes=["2002"], created_by="admin1")
    t2 = task_service.create_project_task(project, title="Task 2", employee_codes=["2002"], created_by="admin1")
    assert project.progress_percent == 0

    task_service.update_status(t1, new_status=TaskStatus.COMPLETED, performed_by="2002")
    assert project.progress_percent == 50

    task_service.update_status(t2, new_status=TaskStatus.COMPLETED, performed_by="2002")
    assert project.progress_percent == 100


def test_project_task_requires_assignee(app, db):
    project = _project(db)
    with pytest.raises(task_service.TaskValidationError):
        task_service.create_project_task(project, title="X", employee_codes=[], created_by="admin1")


def test_health_green_with_no_issues(app, db):
    project = _project(db, target_date=date.today() + timedelta(days=60))
    assert project_service.health(project) == "GREEN"


def test_health_red_on_overdue_high_priority_task(app, db):
    project = _project(db)
    task_service.create_project_task(
        project, title="Late", employee_codes=["2002"], created_by="admin1",
        priority=TaskPriority.HIGH, due_date=date.today() - timedelta(days=2),
    )
    assert project_service.health(project) == "RED"


def test_health_red_on_missed_target_date(app, db):
    project = _project(db, target_date=date.today() - timedelta(days=1))
    assert project_service.health(project) == "RED"


def test_health_amber_on_approaching_target(app, db):
    project = _project(db, target_date=date.today() + timedelta(days=3))
    assert project_service.health(project) == "AMBER"


def test_health_amber_on_overdue_medium_task(app, db):
    project = _project(db, target_date=date.today() + timedelta(days=60))
    task_service.create_project_task(
        project, title="Late-ish", employee_codes=["2002"], created_by="admin1",
        priority=TaskPriority.MEDIUM, due_date=date.today() - timedelta(days=1),
    )
    assert project_service.health(project) == "AMBER"


def test_health_completed_project_is_always_green(app, db):
    project = _project(db, target_date=date.today() - timedelta(days=30))
    project_service.update_status(project, new_status=ProjectStatus.COMPLETED, performed_by="admin1")
    assert project_service.health(project) == "GREEN"


def test_tag_and_untag_meeting(app, db):
    from app.services import meeting_service
    from datetime import datetime

    project = _project(db)
    meeting = meeting_service.create_meeting(
        title="Steering Committee", start_datetime=datetime.now(), end_datetime=datetime.now() + timedelta(hours=1),
        created_by="admin1",
    )
    link = project_service.tag_meeting(project, meeting, performed_by="admin1")
    assert len(project.meeting_links) == 1

    with pytest.raises(project_service.ProjectValidationError):
        project_service.tag_meeting(project, meeting, performed_by="admin1")

    project_service.untag_meeting(link, performed_by="admin1")
    assert len(project.meeting_links) == 0


def test_update_project_details_edits_fields(app, db):
    project = _project(db)
    project_service.update_project_details(
        project,
        name="Renamed Project",
        owner_employee_code="2002",
        description="New details",
        start_date=date.today(),
        target_date=date.today() + timedelta(days=30),
        priority=TaskPriority.HIGH,
        performed_by="admin1",
    )
    assert project.name == "Renamed Project"
    assert project.owner_employee_code == "2002"
    assert project.description == "New details"
    assert project.priority == TaskPriority.HIGH


def test_update_project_details_requires_name(app, db):
    project = _project(db)
    with pytest.raises(project_service.ProjectValidationError):
        project_service.update_project_details(project, name="  ", owner_employee_code="1001", performed_by="admin1")


def test_update_project_details_requires_owner(app, db):
    project = _project(db)
    with pytest.raises(project_service.ProjectValidationError):
        project_service.update_project_details(project, name="X", owner_employee_code="", performed_by="admin1")


def test_delete_project_removes_project_and_dependents(app, db):
    from datetime import datetime

    from app.extensions import db as _db
    from app.models.meeting import Meeting
    from app.models.meeting_project import MeetingProject
    from app.models.project import Project
    from app.models.task import Task
    from app.services import meeting_service

    project = _project(db)
    project_id = project.id
    task_service.create_project_task(project, title="Do the thing", employee_codes=["2002"], created_by="admin1")
    meeting = meeting_service.create_meeting(
        title="Kickoff", start_datetime=datetime.now(), end_datetime=datetime.now() + timedelta(hours=1),
        created_by="admin1",
    )
    meeting_id = meeting.id
    project_service.tag_meeting(project, meeting, performed_by="admin1")
    project_service.add_member(project, employee_code="3003", role="Contributor", added_by="admin1")

    project_service.delete_project(project, performed_by="admin1")

    assert _db.session.get(Project, project_id) is None
    assert Task.query.filter_by(project_id=project_id).count() == 0
    assert MeetingProject.query.filter_by(project_id=project_id).count() == 0
    # the meeting itself is untouched — only the tag link is removed
    assert _db.session.get(Meeting, meeting_id) is not None
