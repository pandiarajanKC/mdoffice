from app.models.app_user import AppUser, UserRole
from app.utils.rbac import roles_required


def _make_user(db, role):
    user = AppUser(username=f"user_{role.value}", role=role)
    user.set_password("Password123")
    db.session.add(user)
    db.session.commit()
    return user


def test_roles_required_blocks_wrong_role(app, db, client):
    @app.route("/_test/md-only")
    @roles_required(UserRole.MD)
    def md_only():
        return "ok"

    ea_user = _make_user(db, UserRole.EA)

    with client.session_transaction() as sess:
        sess["_user_id"] = str(ea_user.id)
        sess["_fresh"] = True

    resp = client.get("/_test/md-only")
    assert resp.status_code == 403


def test_roles_required_allows_matching_role(app, db, client):
    @app.route("/_test/ea-only")
    @roles_required(UserRole.EA)
    def ea_only():
        return "ok"

    ea_user = _make_user(db, UserRole.EA)

    with client.session_transaction() as sess:
        sess["_user_id"] = str(ea_user.id)
        sess["_fresh"] = True

    resp = client.get("/_test/ea-only")
    assert resp.status_code == 200
    assert resp.data == b"ok"


def test_roles_required_blocks_anonymous(client, app):
    @app.route("/_test/anon-blocked")
    @roles_required(UserRole.MD)
    def anon_blocked():
        return "ok"

    resp = client.get("/_test/anon-blocked")
    assert resp.status_code in (302, 401)
