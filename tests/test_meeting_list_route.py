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


def _meeting(db, *, title="Steering Committee", start=None):
    start = start or datetime.now()
    return meeting_service.create_meeting(
        title=title, start_datetime=start, end_datetime=start + timedelta(hours=1), created_by="ea1"
    )


def test_default_view_is_today(app, db, client):
    ea = _make_user(db, UserRole.EA, username="ea1")
    _login(client, ea)

    resp = client.get("/meetings/")
    assert resp.status_code == 200
    assert b'class="nav-link active"' in resp.data
    # the "today" pill is the one rendered active by default
    assert b'href="/meetings/?view=today"' in resp.data


def test_upcoming_tab_sorts_earliest_first(app, db, client):
    ea = _make_user(db, UserRole.EA, username="ea1")
    _login(client, ea)
    far = _meeting(db, title="Far Meeting", start=datetime.now() + timedelta(days=30))
    near = _meeting(db, title="Near Meeting", start=datetime.now() + timedelta(days=1))

    resp = client.get("/meetings/?view=upcoming")

    assert resp.status_code == 200
    body = resp.data.decode()
    assert body.index("Near Meeting") < body.index("Far Meeting")


def test_upcoming_tab_orders_same_day_meetings_by_time_fifo(app, db, client):
    ea = _make_user(db, UserRole.EA, username="ea1")
    _login(client, ea)
    same_day = datetime.now() + timedelta(days=5)
    late = _meeting(db, title="Late Slot", start=same_day.replace(hour=16, minute=0, second=0, microsecond=0))
    early = _meeting(db, title="Early Slot", start=same_day.replace(hour=9, minute=0, second=0, microsecond=0))

    resp = client.get("/meetings/?view=upcoming")

    body = resp.data.decode()
    assert body.index("Early Slot") < body.index("Late Slot")


def test_completed_tab_sorts_earliest_first(app, db, client):
    ea = _make_user(db, UserRole.EA, username="ea1")
    _login(client, ea)
    older = _meeting(db, title="Older Completed", start=datetime.now() - timedelta(days=10))
    newer = _meeting(db, title="Newer Completed", start=datetime.now() - timedelta(days=1))
    meeting_service.start_meeting(older, performed_by="ea1")
    meeting_service.complete_meeting(older, performed_by="ea1")
    meeting_service.start_meeting(newer, performed_by="ea1")
    meeting_service.complete_meeting(newer, performed_by="ea1")

    resp = client.get("/meetings/?view=completed")

    body = resp.data.decode()
    assert body.index("Older Completed") < body.index("Newer Completed")


def test_all_tab_still_sorts_newest_first(app, db, client):
    ea = _make_user(db, UserRole.EA, username="ea1")
    _login(client, ea)
    older = _meeting(db, title="Older Any", start=datetime.now() + timedelta(days=1))
    newer = _meeting(db, title="Newer Any", start=datetime.now() + timedelta(days=10))

    resp = client.get("/meetings/?view=all")

    body = resp.data.decode()
    assert body.index("Newer Any") < body.index("Older Any")


def test_today_tab_lists_only_todays_meetings(app, db, client):
    ea = _make_user(db, UserRole.EA, username="ea1")
    _login(client, ea)
    today_meeting = _meeting(db, title="Today's Sync", start=datetime.now() + timedelta(hours=1))
    _meeting(db, title="Next Week Review", start=datetime.now() + timedelta(days=7))

    resp = client.get("/meetings/?view=today")
    assert resp.status_code == 200
    assert b"Today&#39;s Sync" in resp.data or b"Today's Sync" in resp.data
    assert b"Next Week Review" not in resp.data


def test_date_range_filter_narrows_results(app, db, client):
    ea = _make_user(db, UserRole.EA, username="ea1")
    _login(client, ea)
    near = datetime.now() + timedelta(days=2)
    far = datetime.now() + timedelta(days=40)
    _meeting(db, title="Near Meeting", start=near)
    _meeting(db, title="Far Meeting", start=far)

    date_from = near.date().isoformat()
    date_to = (near.date() + timedelta(days=1)).isoformat()
    resp = client.get(f"/meetings/?view=all&date_from={date_from}&date_to={date_to}")

    assert resp.status_code == 200
    assert b"Near Meeting" in resp.data
    assert b"Far Meeting" not in resp.data


def test_invalid_date_args_are_ignored_not_errors(app, db, client):
    ea = _make_user(db, UserRole.EA, username="ea1")
    _login(client, ea)
    _meeting(db)

    resp = client.get("/meetings/?view=all&date_from=not-a-date")
    assert resp.status_code == 200
