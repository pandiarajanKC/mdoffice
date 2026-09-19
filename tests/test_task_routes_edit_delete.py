from datetime import datetime, timedelta

from app.extensions import db as _db
from app.models.app_user import AppUser, UserRole
from app.models.task import Task
from app.services import meeting_service, task_service


def _make_user(db, role, username=None):
    user = AppUser(username=username or f"user_{role.value}", role=role)
    user.set_password("Password123")
    db.session.add(user)
    db.session.commit()
    return user


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def _meeting_action(db, **overrides):
    start = datetime.now() + timedelta(hours=1)
    meeting = meeting_service.create_meeting(
        title="Steering Committee", start_datetime=start, end_datetime=start + timedelta(hours=1), created_by="ea1"
    )
    return task_service.create_meeting_action(
        meeting,
        title=overrides.pop("title", "Original title"),
        employee_codes=overrides.pop("employee_codes", ["1001"]),
        created_by="ea1",
        **overrides,
    )


def test_edit_is_ea_only(app, db, client):
    md = _make_user(db, UserRole.MD, username="md1")
    _login(client, md)
    task = _meeting_action(db)

    resp = client.post(f"/tasks/{task.id}/edit", data={"title": "Hacked", "priority": "HIGH", "employee_codes": ["1001"]})

    assert resp.status_code == 403
    assert _db.session.get(Task, task.id).title == "Original title"


def test_edit_updates_task_fields(app, db, client):
    ea = _make_user(db, UserRole.EA, username="ea1")
    _login(client, ea)
    task = _meeting_action(db)

    resp = client.post(
        f"/tasks/{task.id}/edit",
        data={
            "title": "Updated title",
            "description": "New details",
            "priority": "HIGH",
            "due_date": "2026-12-01",
            "employee_codes": ["1002"],
        },
    )

    assert resp.status_code == 302
    task = _db.session.get(Task, task.id)
    assert task.title == "Updated title"
    assert task.description == "New details"
    assert task.priority.value == "HIGH"
    assert task.due_date.isoformat() == "2026-12-01"
    assert [a.employee_code for a in task.assignees] == ["1002"]


def test_edit_rejects_blank_title(app, db, client):
    ea = _make_user(db, UserRole.EA, username="ea1")
    _login(client, ea)
    task = _meeting_action(db)

    resp = client.post(f"/tasks/{task.id}/edit", data={"title": "  ", "priority": "MEDIUM", "employee_codes": ["1001"]})

    assert resp.status_code == 302
    assert _db.session.get(Task, task.id).title == "Original title"


def test_delete_is_ea_only(app, db, client):
    md = _make_user(db, UserRole.MD, username="md1")
    _login(client, md)
    task = _meeting_action(db)

    resp = client.post(f"/tasks/{task.id}/delete")

    assert resp.status_code == 403
    assert _db.session.get(Task, task.id) is not None


def test_tracker_page_renders_with_edit_delete_controls(app, db, client):
    ea = _make_user(db, UserRole.EA, username="ea1")
    _login(client, ea)
    _meeting_action(db)

    resp = client.get("/tasks/")

    assert resp.status_code == 200
    assert b"edit-task-btn" in resp.data
    assert b"editTaskModal" in resp.data


def test_tracker_excludes_standalone_tasks(app, db, client):
    # The Action Tracker is action items only (meeting actions + project
    # tasks) — standalone tasks belong on the separate Tasks page, same
    # split as the dashboard's Action Items vs Tasks summary cards.
    ea = _make_user(db, UserRole.EA, username="ea1")
    _login(client, ea)
    _meeting_action(db, title="Real action item")
    task_service.create_general_followup(title="Standalone task", employee_codes=["1001"], created_by="ea1")

    resp = client.get("/tasks/")

    assert resp.status_code == 200
    assert b"Real action item" in resp.data
    assert b"Standalone task" not in resp.data


def test_general_tasks_page_renders_with_edit_delete_controls(app, db, client):
    ea = _make_user(db, UserRole.EA, username="ea1")
    _login(client, ea)
    task_service.create_general_followup(title="Standalone task", employee_codes=["1001"], created_by="ea1")

    resp = client.get("/tasks/general")

    assert resp.status_code == 200
    assert b"edit-task-btn" in resp.data
    assert b"editTaskModal" in resp.data


def test_delete_removes_task(app, db, client):
    ea = _make_user(db, UserRole.EA, username="ea1")
    _login(client, ea)
    task = _meeting_action(db)
    task_id = task.id

    resp = client.post(f"/tasks/{task_id}/delete")

    assert resp.status_code == 302
    assert _db.session.get(Task, task_id) is None
