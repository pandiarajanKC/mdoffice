from datetime import date, datetime, timedelta

from app.models.task import TaskPriority, TaskStatus
from app.services import meeting_service, project_service, reports_service, task_service
from tests.fakes import FakeEmployeeRepository, make_employee


def _meeting(db, title="Steering Committee"):
    return meeting_service.create_meeting(
        title=title, start_datetime=datetime.now(), end_datetime=datetime.now() + timedelta(hours=1),
        created_by="admin1",
    )


def _repo():
    return FakeEmployeeRepository([make_employee(code="1001", name="Alice"), make_employee(code="1002", name="Bob")])


def test_action_status_report_totals_match(app, db):
    meeting = _meeting(db)
    t1 = task_service.create_meeting_action(meeting, title="A", employee_codes=["1001"], created_by="admin1")
    task_service.create_meeting_action(meeting, title="B", employee_codes=["1001"], created_by="admin1")
    task_service.update_status(t1, new_status=TaskStatus.COMPLETED, performed_by="admin1")

    summary, rows = reports_service.action_status_report(_repo())
    assert summary["total"] == 2
    assert sum(r["count"] for r in rows) == 2
    completed_row = next(r for r in rows if r["status"] == "COMPLETED")
    assert completed_row["count"] == 1
    assert completed_row["percent"] == 50.0


def test_employee_pending_report_excludes_completed(app, db):
    meeting = _meeting(db)
    task_service.create_meeting_action(meeting, title="A", employee_codes=["1001"], created_by="admin1")
    t2 = task_service.create_meeting_action(meeting, title="B", employee_codes=["1001"], created_by="admin1")
    task_service.update_status(t2, new_status=TaskStatus.COMPLETED, performed_by="admin1")

    summary, rows = reports_service.employee_pending_report(_repo())
    assert len(rows) == 1
    assert rows[0]["name"] == "Alice"
    assert rows[0]["open"] == 1
    assert rows[0]["total_pending"] == 1


def test_overdue_actions_report(app, db):
    meeting = _meeting(db)
    task_service.create_meeting_action(
        meeting, title="Late task", employee_codes=["1001"], created_by="admin1",
        due_date=date.today() - timedelta(days=3),
    )
    task_service.create_meeting_action(
        meeting, title="Future task", employee_codes=["1001"], created_by="admin1",
        due_date=date.today() + timedelta(days=3),
    )
    summary, rows = reports_service.overdue_actions_report(_repo())
    assert summary["total_overdue"] == 1
    assert rows[0]["title"] == "Late task"
    assert rows[0]["days_overdue"] == 3


def test_meeting_actions_report_skips_meetings_without_actions(app, db):
    meeting_with = _meeting(db, title="With Actions")
    task_service.create_meeting_action(meeting_with, title="A", employee_codes=["1001"], created_by="admin1")
    _meeting(db, title="Without Actions")

    summary, rows = reports_service.meeting_actions_report(_repo())
    titles = [r["meeting"] for r in rows]
    assert "With Actions" in titles
    assert "Without Actions" not in titles


def test_project_actions_report(app, db):
    project = project_service.create_project(project_code="P-1", name="MES", owner_employee_code="1001", created_by="admin1")
    t = task_service.create_project_task(project, title="Design", employee_codes=["1001"], created_by="admin1")
    task_service.update_status(t, new_status=TaskStatus.COMPLETED, performed_by="admin1")

    summary, rows = reports_service.project_actions_report(_repo())
    row = next(r for r in rows if r["code"] == "P-1")
    assert row["total_tasks"] == 1
    assert row["completed"] == 1
    assert row["progress_percent"] == 100


def test_completed_actions_report_computes_days_to_complete(app, db):
    meeting = _meeting(db)
    t = task_service.create_meeting_action(meeting, title="X", employee_codes=["1001"], created_by="admin1")
    t.created_at = datetime.now() - timedelta(days=5)
    task_service.update_status(t, new_status=TaskStatus.COMPLETED, performed_by="admin1")

    summary, rows = reports_service.completed_actions_report(_repo())
    assert summary["total_completed"] == 1
    assert rows[0]["days_to_complete"] == 5


def test_ageing_report_excludes_completed_and_sorts_oldest_first(app, db):
    meeting = _meeting(db)
    old_task = task_service.create_meeting_action(meeting, title="Old", employee_codes=["1001"], created_by="admin1")
    old_task.created_at = datetime.now() - timedelta(days=40)
    task_service.create_meeting_action(meeting, title="New", employee_codes=["1001"], created_by="admin1")
    done = task_service.create_meeting_action(meeting, title="Done", employee_codes=["1001"], created_by="admin1")
    task_service.update_status(done, new_status=TaskStatus.COMPLETED, performed_by="admin1")

    buckets, rows = reports_service.ageing_report(_repo())
    assert buckets["31-60"] == 1
    assert len(rows) == 2  # completed excluded
    assert rows[0]["title"] == "Old"  # oldest first


def test_employee_completion_rate_report(app, db):
    meeting = _meeting(db)
    t1 = task_service.create_meeting_action(meeting, title="A", employee_codes=["1001"], created_by="admin1")
    task_service.create_meeting_action(meeting, title="B", employee_codes=["1001"], created_by="admin1")
    task_service.update_status(t1, new_status=TaskStatus.COMPLETED, performed_by="admin1")

    summary, rows = reports_service.employee_completion_rate_report(_repo())
    row = next(r for r in rows if r["employee_code"] == "1001")
    assert row["total_assigned"] == 2
    assert row["completed"] == 1
    assert row["completion_rate"] == 50.0


def test_employee_completion_rate_on_time(app, db):
    meeting = _meeting(db)
    t = task_service.create_meeting_action(
        meeting, title="A", employee_codes=["1001"], created_by="admin1", due_date=date.today() + timedelta(days=5)
    )
    task_service.update_status(t, new_status=TaskStatus.COMPLETED, performed_by="admin1")

    summary, rows = reports_service.employee_completion_rate_report(_repo())
    row = next(r for r in rows if r["employee_code"] == "1001")
    assert row["on_time_rate"] == 100.0


def test_project_progress_report_includes_health(app, db):
    project_service.create_project(
        project_code="P-1", name="MES", owner_employee_code="1001", created_by="admin1", priority=TaskPriority.HIGH
    )
    summary, rows = reports_service.project_progress_report(_repo())
    assert rows[0]["health"] in ("GREEN", "AMBER", "RED")
    assert rows[0]["owner"] == "Alice"


def test_meeting_compliance_report(app, db):
    compliant_meeting = _meeting(db, title="Compliant")
    meeting_service.start_meeting(compliant_meeting, performed_by="admin1")
    t = task_service.create_meeting_action(compliant_meeting, title="X", employee_codes=["1001"], created_by="admin1")
    task_service.update_status(t, new_status=TaskStatus.COMPLETED, performed_by="admin1")
    meeting_service.complete_meeting(compliant_meeting, performed_by="admin1")

    noncompliant_meeting = _meeting(db, title="Non-compliant")
    meeting_service.start_meeting(noncompliant_meeting, performed_by="admin1")
    task_service.create_meeting_action(noncompliant_meeting, title="Y", employee_codes=["1001"], created_by="admin1")
    meeting_service.complete_meeting(noncompliant_meeting, performed_by="admin1")

    summary, rows = reports_service.meeting_compliance_report(_repo())
    assert summary["total_completed_meetings"] == 2
    assert summary["fully_compliant"] == 1
    assert summary["compliance_rate"] == 50.0
    compliant_row = next(r for r in rows if r["meeting"] == "Compliant")
    assert compliant_row["compliant"] == "Yes"
    noncompliant_row = next(r for r in rows if r["meeting"] == "Non-compliant")
    assert noncompliant_row["compliant"] == "No"
