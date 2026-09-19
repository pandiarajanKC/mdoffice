from datetime import datetime, timedelta

from app.models.app_user import AppUser, UserRole
from app.models.meeting import MeetingNote
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
        title="Steering Committee", start_datetime=start, end_datetime=start + timedelta(hours=1), created_by="ea1"
    )


def test_convert_note_to_decision_route(app, db, client):
    ea = _make_user(db, UserRole.EA, username="ea1")
    _login(client, ea)
    meeting = _meeting(db)
    note = meeting_service.add_note(meeting, note_text="Actually decided on Vendor B", created_by="ea1")

    resp = client.post(f"/meetings/{meeting.id}/notes/{note.id}/convert-to-decision")

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/meetings/{meeting.id}#tab-decisions")
    assert db.session.get(MeetingNote, note.id) is None
    assert [d.decision_text for d in meeting.decisions] == ["Actually decided on Vendor B"]


def test_convert_note_to_decision_rejects_note_from_another_meeting(app, db, client):
    ea = _make_user(db, UserRole.EA, username="ea1")
    _login(client, ea)
    meeting = _meeting(db)
    other_meeting = _meeting(db)
    note = meeting_service.add_note(other_meeting, note_text="Belongs elsewhere", created_by="ea1")

    resp = client.post(f"/meetings/{meeting.id}/notes/{note.id}/convert-to-decision")

    assert resp.status_code == 404
    assert db.session.get(MeetingNote, note.id) is not None


def test_convert_note_to_decision_is_ea_only(app, db, client):
    md = _make_user(db, UserRole.MD, username="md1")
    _login(client, md)
    meeting = _meeting(db)
    note = meeting_service.add_note(meeting, note_text="x", created_by="ea1")

    resp = client.post(f"/meetings/{meeting.id}/notes/{note.id}/convert-to-decision")

    assert resp.status_code == 403
    assert db.session.get(MeetingNote, note.id) is not None


def test_convert_note_to_action_deletes_source_note_on_success(app, db, client):
    ea = _make_user(db, UserRole.EA, username="ea1")
    _login(client, ea)
    meeting = _meeting(db)
    note = meeting_service.add_note(meeting, note_text="John to send the revised numbers", created_by="ea1")

    resp = client.post(
        f"/meetings/{meeting.id}/actions",
        data={
            "title": "John to send the revised numbers",
            "priority": "MEDIUM",
            "employee_codes": ["1001"],
            "source_note_id": str(note.id),
        },
    )

    assert resp.status_code == 302
    assert db.session.get(MeetingNote, note.id) is None
    tasks = [t for t in meeting.tasks]
    assert len(tasks) == 1
    assert tasks[0].title == "John to send the revised numbers"


def test_convert_note_to_action_keeps_note_when_no_assignee_given(app, db, client):
    # Action items require an assignee — a failed conversion (e.g. the EA
    # closed the picker without choosing anyone) must leave the note intact
    # rather than silently losing it.
    ea = _make_user(db, UserRole.EA, username="ea1")
    _login(client, ea)
    meeting = _meeting(db)
    note = meeting_service.add_note(meeting, note_text="Needs an owner", created_by="ea1")

    resp = client.post(
        f"/meetings/{meeting.id}/actions",
        data={"title": "Needs an owner", "priority": "MEDIUM", "source_note_id": str(note.id)},
    )

    assert resp.status_code == 302
    assert db.session.get(MeetingNote, note.id) is not None
