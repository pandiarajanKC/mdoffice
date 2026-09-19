from datetime import datetime, timedelta

from app.models.app_user import AppUser, UserRole
from app.services import attachment_service, meeting_service


def _make_user(db, *, username, role):
    user = AppUser(username=username, role=role)
    user.set_password("Password123")
    db.session.add(user)
    db.session.commit()
    return user


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def _make_meeting(db, *, created_by="ea1"):
    start = datetime.now()
    return meeting_service.create_meeting(
        title="Budget Review", start_datetime=start, end_datetime=start + timedelta(hours=1), created_by=created_by,
    )


def test_audio_file_streams_uploaded_audio_for_md_and_ea(app, db, client, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    meeting = _make_meeting(db)

    import io
    from werkzeug.datastructures import FileStorage

    with app.test_request_context():
        f = FileStorage(stream=io.BytesIO(b"fake mp3 bytes"), filename="memo.mp3", content_type="audio/mpeg")
        attachment = attachment_service.save_audio_attachment(
            f, entity_type="MEETING", entity_id=meeting.id, uploaded_by="ea1"
        )

    _login(client, _make_user(db, username="md1", role=UserRole.MD))
    resp = client.get(f"/meetings/{meeting.id}/audio/{attachment.id}")
    assert resp.status_code == 200
    assert resp.data == b"fake mp3 bytes"
    assert resp.mimetype == "audio/mpeg"


def test_audio_file_404_for_wrong_meeting(app, db, client, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    meeting = _make_meeting(db)
    other_meeting = _make_meeting(db)

    import io
    from werkzeug.datastructures import FileStorage

    with app.test_request_context():
        f = FileStorage(stream=io.BytesIO(b"fake mp3 bytes"), filename="memo.mp3", content_type="audio/mpeg")
        attachment = attachment_service.save_audio_attachment(
            f, entity_type="MEETING", entity_id=meeting.id, uploaded_by="ea1"
        )

    _login(client, _make_user(db, username="ea1", role=UserRole.EA))
    resp = client.get(f"/meetings/{other_meeting.id}/audio/{attachment.id}")
    assert resp.status_code == 404


def test_audio_file_blocked_for_employee(app, db, client, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    meeting = _make_meeting(db)

    import io
    from werkzeug.datastructures import FileStorage

    with app.test_request_context():
        f = FileStorage(stream=io.BytesIO(b"fake mp3 bytes"), filename="memo.mp3", content_type="audio/mpeg")
        attachment = attachment_service.save_audio_attachment(
            f, entity_type="MEETING", entity_id=meeting.id, uploaded_by="ea1"
        )

    _login(client, _make_user(db, username="1001", role=UserRole.EMPLOYEE))
    resp = client.get(f"/meetings/{meeting.id}/audio/{attachment.id}")
    assert resp.status_code == 403


def test_delete_audio_removes_attachment_and_file(app, db, client, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    meeting = _make_meeting(db)

    import io
    import os

    from werkzeug.datastructures import FileStorage

    with app.test_request_context():
        f = FileStorage(stream=io.BytesIO(b"fake mp3 bytes"), filename="memo.mp3", content_type="audio/mpeg")
        attachment = attachment_service.save_audio_attachment(
            f, entity_type="MEETING", entity_id=meeting.id, uploaded_by="ea1"
        )
        stored_path = attachment_service.attachment_path(attachment)
        attachment_id = attachment.id
    assert os.path.exists(stored_path)

    _login(client, _make_user(db, username="ea1", role=UserRole.EA))
    resp = client.post(f"/meetings/{meeting.id}/audio/{attachment_id}/delete")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/meetings/{meeting.id}#tab-transcript")
    assert not os.path.exists(stored_path)

    with app.app_context():
        from app.extensions import db as _db
        from app.models.attachment import Attachment
        assert _db.session.get(Attachment, attachment_id) is None

    # gone, so re-fetching it as audio is a 404 rather than an error
    resp = client.get(f"/meetings/{meeting.id}/audio/{attachment_id}")
    assert resp.status_code == 404


def test_delete_audio_blocked_for_md(app, db, client, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    meeting = _make_meeting(db)

    import io
    from werkzeug.datastructures import FileStorage

    with app.test_request_context():
        f = FileStorage(stream=io.BytesIO(b"fake mp3 bytes"), filename="memo.mp3", content_type="audio/mpeg")
        attachment = attachment_service.save_audio_attachment(
            f, entity_type="MEETING", entity_id=meeting.id, uploaded_by="ea1"
        )

    _login(client, _make_user(db, username="md1", role=UserRole.MD))
    resp = client.post(f"/meetings/{meeting.id}/audio/{attachment.id}/delete")
    assert resp.status_code == 403
