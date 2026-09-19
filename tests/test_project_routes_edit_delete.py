from app.extensions import db as _db
from app.models.app_user import AppUser, UserRole
from app.models.project import Project
from app.services import project_service


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


def _project(db, **overrides):
    return project_service.create_project(
        project_code=overrides.pop("project_code", "MES-001"),
        name=overrides.pop("name", "Original Project"),
        owner_employee_code=overrides.pop("owner_employee_code", "1001"),
        created_by=overrides.pop("created_by", "ea1"),
        **overrides,
    )


def test_projects_list_renders_with_filters_and_icons(app, db, client):
    ea = _make_user(db, UserRole.EA, username="ea1")
    _login(client, ea)
    _project(db)

    resp = client.get("/projects/")

    assert resp.status_code == 200
    assert b"edit-project-btn" in resp.data
    assert b"editProjectModal" in resp.data
    assert b"table-filter-row" in resp.data


def test_edit_is_ea_only(app, db, client):
    md = _make_user(db, UserRole.MD, username="md1")
    _login(client, md)
    project = _project(db)

    resp = client.post(f"/projects/{project.id}/edit", data={"name": "Hacked", "owner_employee_code": "1001"})

    assert resp.status_code == 403
    assert _db.session.get(Project, project.id).name == "Original Project"


def test_edit_updates_project_fields(app, db, client):
    ea = _make_user(db, UserRole.EA, username="ea1")
    _login(client, ea)
    project = _project(db)

    resp = client.post(
        f"/projects/{project.id}/edit",
        data={
            "name": "Renamed Project",
            "description": "New details",
            "owner_employee_code": "2002",
            "start_date": "2026-01-01",
            "target_date": "2026-12-01",
            "priority": "HIGH",
        },
    )

    assert resp.status_code == 302
    project = _db.session.get(Project, project.id)
    assert project.name == "Renamed Project"
    assert project.description == "New details"
    assert project.owner_employee_code == "2002"
    assert project.priority.value == "HIGH"


def test_edit_rejects_blank_name(app, db, client):
    ea = _make_user(db, UserRole.EA, username="ea1")
    _login(client, ea)
    project = _project(db)

    resp = client.post(f"/projects/{project.id}/edit", data={"name": "  ", "owner_employee_code": "1001"})

    assert resp.status_code == 302
    assert _db.session.get(Project, project.id).name == "Original Project"


def test_delete_is_ea_only(app, db, client):
    md = _make_user(db, UserRole.MD, username="md1")
    _login(client, md)
    project = _project(db)

    resp = client.post(f"/projects/{project.id}/delete")

    assert resp.status_code == 403
    assert _db.session.get(Project, project.id) is not None


def test_delete_removes_project(app, db, client):
    ea = _make_user(db, UserRole.EA, username="ea1")
    _login(client, ea)
    project = _project(db)
    project_id = project.id

    resp = client.post(f"/projects/{project_id}/delete")

    assert resp.status_code == 302
    assert _db.session.get(Project, project_id) is None
