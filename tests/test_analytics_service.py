from datetime import date, datetime, timedelta

from app.models.project import ProjectStatus
from app.models.task import TaskPriority, TaskStatus
from app.services import analytics_service, meeting_service, project_service, task_service
from tests.fakes import FakeEmployeeRepository, make_employee


def _meeting(db, title="Steering Committee"):
    return meeting_service.create_meeting(
        title=title, start_datetime=datetime.now(), end_datetime=datetime.now() + timedelta(hours=1),
        created_by="admin1",
    )


def test_task_status_distribution_counts_all_statuses(app, db):
    meeting = _meeting(db)
    t1 = task_service.create_meeting_action(meeting, title="A", employee_codes=["1001"], created_by="admin1")
    task_service.create_meeting_action(meeting, title="B", employee_codes=["1001"], created_by="admin1")
    task_service.update_status(t1, new_status=TaskStatus.COMPLETED, performed_by="admin1")

    dist = analytics_service.task_status_distribution()
    assert dist["OPEN"] == 1
    assert dist["COMPLETED"] == 1
    assert dist["CANCELLED"] == 0  # present with zero, not missing


def test_project_status_distribution(app, db):
    project_service.create_project(project_code="P-001", name="A", owner_employee_code="1001", created_by="admin1")
    project_service.create_project(project_code="P-002", name="B", owner_employee_code="1001", created_by="admin1")

    dist = analytics_service.project_status_distribution()
    assert dist["OPEN"] == 2
    assert dist["COMPLETED"] == 0


def test_employee_workload_ranks_by_open_load(app, db):
    meeting = _meeting(db)
    task_service.create_meeting_action(meeting, title="A", employee_codes=["1001"], created_by="admin1", priority=TaskPriority.HIGH)
    task_service.create_meeting_action(meeting, title="B", employee_codes=["1001"], created_by="admin1")
    task_service.create_meeting_action(meeting, title="C", employee_codes=["1002"], created_by="admin1")

    repo = FakeEmployeeRepository([make_employee(code="1001", name="Alice"), make_employee(code="1002", name="Bob")])
    workload = analytics_service.employee_workload(repo, limit=10)

    assert workload[0]["employee_code"] == "1001"
    assert workload[0]["name"] == "Alice"
    assert workload[0]["open"] == 2
    assert workload[1]["employee_code"] == "1002"


def test_employee_workload_counts_overdue(app, db):
    meeting = _meeting(db)
    task_service.create_meeting_action(
        meeting, title="Late", employee_codes=["1001"], created_by="admin1", due_date=date.today() - timedelta(days=3)
    )
    repo = FakeEmployeeRepository([make_employee(code="1001")])
    workload = analytics_service.employee_workload(repo)
    assert workload[0]["overdue"] == 1


def test_employee_workload_excludes_completed_tasks(app, db):
    meeting = _meeting(db)
    t = task_service.create_meeting_action(meeting, title="Done", employee_codes=["1001"], created_by="admin1")
    task_service.update_status(t, new_status=TaskStatus.COMPLETED, performed_by="admin1")
    repo = FakeEmployeeRepository([make_employee(code="1001")])
    workload = analytics_service.employee_workload(repo)
    assert workload == []


def test_meetings_with_pending_followup(app, db):
    meeting = _meeting(db, title="Has Pending Action")
    meeting_service.start_meeting(meeting, performed_by="admin1")
    task_service.create_meeting_action(meeting, title="X", employee_codes=["1001"], created_by="admin1")
    meeting_service.complete_meeting(meeting, performed_by="admin1")

    all_done_meeting = _meeting(db, title="All Wrapped Up")
    meeting_service.start_meeting(all_done_meeting, performed_by="admin1")
    t = task_service.create_meeting_action(all_done_meeting, title="Y", employee_codes=["1001"], created_by="admin1")
    task_service.update_status(t, new_status=TaskStatus.COMPLETED, performed_by="admin1")
    meeting_service.complete_meeting(all_done_meeting, performed_by="admin1")

    pending = analytics_service.meetings_with_pending_followup()
    titles = [p["meeting"].title for p in pending]
    assert "Has Pending Action" in titles
    assert "All Wrapped Up" not in titles


def test_recently_completed_tasks_filters_by_priority(app, db):
    meeting = _meeting(db)
    t1 = task_service.create_meeting_action(meeting, title="Critical one", employee_codes=["1001"], created_by="admin1", priority=TaskPriority.CRITICAL)
    t2 = task_service.create_meeting_action(meeting, title="Medium one", employee_codes=["1001"], created_by="admin1", priority=TaskPriority.MEDIUM)
    task_service.update_status(t1, new_status=TaskStatus.COMPLETED, performed_by="admin1")
    task_service.update_status(t2, new_status=TaskStatus.COMPLETED, performed_by="admin1")

    critical_only = analytics_service.recently_completed_tasks(priority=TaskPriority.CRITICAL)
    assert len(critical_only) == 1
    assert critical_only[0].title == "Critical one"

    all_completed = analytics_service.recently_completed_tasks()
    assert len(all_completed) == 2


def test_todays_meetings_only_returns_meetings_starting_today(app, db):
    today_meeting = _meeting(db, title="Today's Sync")
    tomorrow_meeting = meeting_service.create_meeting(
        title="Tomorrow's Sync", start_datetime=datetime.now() + timedelta(days=1),
        end_datetime=datetime.now() + timedelta(days=1, hours=1), created_by="admin1",
    )

    titles = [m.title for m in analytics_service.todays_meetings()]
    assert "Today's Sync" in titles
    assert "Tomorrow's Sync" not in titles


def test_overdue_tasks_reports_true_total_and_capped_rows(app, db):
    meeting = _meeting(db)
    for i in range(3):
        task_service.create_meeting_action(
            meeting, title=f"Late {i}", employee_codes=["1001"], created_by="admin1",
            due_date=date.today() - timedelta(days=i + 1),
        )
    task_service.create_meeting_action(
        meeting, title="Not due yet", employee_codes=["1001"], created_by="admin1",
        due_date=date.today() + timedelta(days=5),
    )

    repo = FakeEmployeeRepository([make_employee(code="1001", name="Alice")])
    result = analytics_service.overdue_tasks(repo, limit=2)

    assert result["total"] == 3
    assert len(result["rows"]) == 2
    assert result["rows"][0]["days_overdue"] == 3  # most overdue first
    assert result["rows"][0]["assignees"] == "Alice"
    assert "Meeting:" in result["rows"][0]["source_label"]


def test_overdue_tasks_excludes_completed_and_future_due_dates(app, db):
    meeting = _meeting(db)
    done = task_service.create_meeting_action(
        meeting, title="Done late", employee_codes=["1001"], created_by="admin1",
        due_date=date.today() - timedelta(days=1),
    )
    task_service.update_status(done, new_status=TaskStatus.COMPLETED, performed_by="admin1")

    repo = FakeEmployeeRepository([make_employee(code="1001")])
    result = analytics_service.overdue_tasks(repo)
    assert result["total"] == 0
    assert result["rows"] == []


def test_todays_and_overdue_tasks_includes_both_groups(app, db):
    meeting = _meeting(db)
    task_service.create_meeting_action(
        meeting, title="Overdue task", employee_codes=["1001"], created_by="admin1",
        due_date=date.today() - timedelta(days=2),
    )
    task_service.create_meeting_action(
        meeting, title="Due today task", employee_codes=["1001"], created_by="admin1",
        due_date=date.today(),
    )
    task_service.create_meeting_action(
        meeting, title="Due next week", employee_codes=["1001"], created_by="admin1",
        due_date=date.today() + timedelta(days=7),
    )

    repo = FakeEmployeeRepository([make_employee(code="1001", name="Alice")])
    result = analytics_service.todays_and_overdue_tasks(repo)

    titles = [r["task"].title for r in result["rows"]]
    assert result["total"] == 2
    assert "Overdue task" in titles
    assert "Due today task" in titles
    assert "Due next week" not in titles


def test_todays_and_overdue_tasks_orders_overdue_before_today(app, db):
    meeting = _meeting(db)
    task_service.create_meeting_action(
        meeting, title="Due today", employee_codes=["1001"], created_by="admin1", due_date=date.today(),
    )
    task_service.create_meeting_action(
        meeting, title="Overdue", employee_codes=["1001"], created_by="admin1",
        due_date=date.today() - timedelta(days=1),
    )

    repo = FakeEmployeeRepository([make_employee(code="1001")])
    result = analytics_service.todays_and_overdue_tasks(repo)

    assert [r["task"].title for r in result["rows"]] == ["Overdue", "Due today"]
    assert result["rows"][0]["is_overdue"] is True
    assert result["rows"][0]["days_overdue"] == 1
    assert result["rows"][1]["is_overdue"] is False
    assert result["rows"][1]["days_overdue"] == 0


def test_todays_and_overdue_tasks_excludes_completed(app, db):
    meeting = _meeting(db)
    done = task_service.create_meeting_action(
        meeting, title="Done today", employee_codes=["1001"], created_by="admin1", due_date=date.today(),
    )
    task_service.update_status(done, new_status=TaskStatus.COMPLETED, performed_by="admin1")

    repo = FakeEmployeeRepository([make_employee(code="1001")])
    result = analytics_service.todays_and_overdue_tasks(repo)
    assert result["total"] == 0
    assert result["rows"] == []


def test_task_ageing_buckets_excludes_completed(app, db):
    meeting = _meeting(db)
    t = task_service.create_meeting_action(meeting, title="Old", employee_codes=["1001"], created_by="admin1")
    t.created_at = datetime.now() - timedelta(days=45)
    from app.extensions import db as _db
    _db.session.commit()

    task_service.create_meeting_action(meeting, title="New", employee_codes=["1001"], created_by="admin1")
    done = task_service.create_meeting_action(meeting, title="Done", employee_codes=["1001"], created_by="admin1")
    task_service.update_status(done, new_status=TaskStatus.COMPLETED, performed_by="admin1")

    buckets = analytics_service.task_ageing_buckets()
    assert buckets["31-60"] == 1
    assert buckets["0-7"] == 1
    assert sum(buckets.values()) == 2  # completed task excluded
