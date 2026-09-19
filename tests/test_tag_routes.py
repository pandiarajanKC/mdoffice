from datetime import datetime, timedelta

from app.models.app_user import AppUser, UserRole
from app.services import meeting_service


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


def _meeting(db):
    start = datetime.now() + timedelta(hours=1)
    return meeting_service.create_meeting(
        title="Production Review", start_datetime=start, end_datetime=start + timedelta(hours=1), created_by="ea1"
    )


def test_master_requires_login(client):
    resp = client.get("/tags/")
    assert resp.status_code in (302, 401)


def test_employee_cannot_view_tag_master(app, db, client):
    employee = _make_user(db, UserRole.EMPLOYEE)
    _login(client, employee)
    resp = client.get("/tags/")
    assert resp.status_code == 403


def test_md_can_view_but_not_create(app, db, client):
    md = _make_user(db, UserRole.MD)
    _login(client, md)

    resp = client.get("/tags/")
    assert resp.status_code == 200
    assert b"Create a Tag" not in resp.data

    resp = client.post("/tags/create", data={"name": "Board Priority", "color": "violet"}, follow_redirects=True)
    assert resp.status_code == 403


def test_ea_can_create_and_view_tag(app, db, client):
    ea = _make_user(db, UserRole.EA, username="ea1")
    _login(client, ea)

    resp = client.post("/tags/create", data={"name": "Board Priority", "color": "violet"}, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Board Priority" in resp.data

    from app.services import tag_service

    tag = tag_service.find_by_name_ci("Board Priority")
    assert tag is not None

    resp = client.get(f"/tags/{tag.id}")
    assert resp.status_code == 200
    assert b"Board Priority" in resp.data


def test_ea_can_assign_and_unassign_tag_to_meeting(app, db, client):
    ea = _make_user(db, UserRole.EA, username="ea1")
    _login(client, ea)
    meeting = _meeting(db)

    resp = client.post(
        "/tags/assign",
        data={"entity_type": "MEETING", "entity_id": str(meeting.id), "tag_name": "Q4 Budget"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    from app.services import tag_service

    assignments = tag_service.get_assignments_for_entity("MEETING", meeting.id)
    assert len(assignments) == 1
    assert assignments[0].tag.name == "Q4 Budget"

    resp = client.post(f"/tags/unassign/{assignments[0].id}", follow_redirects=True)
    assert resp.status_code == 200
    assert tag_service.get_assignments_for_entity("MEETING", meeting.id) == []


def test_assign_rejects_unknown_meeting(app, db, client):
    ea = _make_user(db, UserRole.EA, username="ea1")
    _login(client, ea)

    resp = client.post(
        "/tags/assign",
        data={"entity_type": "MEETING", "entity_id": "999999", "tag_name": "Ghost"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"no longer exists" in resp.data


def test_delete_tag_forbidden_for_md(app, db, client):
    # Deliberately a single login+request per test here: Flask caches the
    # loaded user on `g` for the lifetime of an app context, and the `app`
    # fixture keeps one app context open for the whole test, so switching
    # users and issuing a second request in the same test would silently
    # keep resolving to the first request's identity.
    from app.services import tag_service

    tag = tag_service.create_tag("Temp", created_by="ea1")
    md = _make_user(db, UserRole.MD, username="md1")
    _login(client, md)

    resp = client.post(f"/tags/{tag.id}/delete", follow_redirects=True)
    assert resp.status_code == 403
    assert tag_service.list_all_tags() == [tag]


def test_delete_tag_allowed_for_ea(app, db, client):
    from app.services import tag_service

    tag = tag_service.create_tag("Temp", created_by="ea1")
    ea = _make_user(db, UserRole.EA, username="ea1")
    _login(client, ea)

    resp = client.post(f"/tags/{tag.id}/delete", follow_redirects=True)
    assert resp.status_code == 200
    assert tag_service.list_all_tags() == []


def test_meeting_workspace_context_includes_tags(app, db):
    # A full GET of the workspace page also exercises pre-existing
    # form-rendering code far outside this feature's scope, so this checks
    # the context-builder wiring directly rather than the rendered HTML.
    from flask_login import login_user

    from app.meetings.routes import build_workspace_context
    from app.services import tag_service

    ea = _make_user(db, UserRole.EA, username="ea1")
    meeting = _meeting(db)
    tag_service.tag_entity(tag_name="Q4 Budget", entity_type="MEETING", entity_id=meeting.id, tagged_by="ea1")

    with app.test_request_context():
        login_user(ea)
        context = build_workspace_context(meeting)

    assert [a.tag.name for a in context["tag_assignments"]] == ["Q4 Budget"]
    assert "Q4 Budget" in context["all_tag_names"]
