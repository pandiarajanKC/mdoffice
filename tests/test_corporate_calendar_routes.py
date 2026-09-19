from datetime import date, timedelta

from app.models.app_user import AppUser, UserRole
from app.models.corporate_event import EventType, MDInvolvement, PriorityLevel
from app.services import corporate_calendar_service as cc_service
from tests.fakes import FakeEmployeeRepository, make_employee


def _make_user(db, *, username, role, employee_code=None):
    user = AppUser(username=username, role=role, employee_code=employee_code)
    user.set_password("Password123")
    db.session.add(user)
    db.session.commit()
    return user


def _login(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def _valid_form(**overrides):
    start = date.today()
    end = start + timedelta(days=1)
    data = {
        "title": "Division Sync",
        "event_type": EventType.TRAINING.value,
        "event_type_other": "",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "location": "HQ",
        "organizer": "Alice",
        "description": "Quarterly sync",
        "priority_level": PriorityLevel.STANDARD.value,
        "md_presence_required": MDInvolvement.NO.value,
        "involves_external_stakeholder": "no",
        "has_critical_impact": "no",
        "budget_involved": "no",
        "conflicts_with_events": "NOT_SURE",
    }
    data.update(overrides)
    return data


def test_mine_page_loads_for_employee(app, db, client):
    _login(client, _make_user(db, username="1001", role=UserRole.EMPLOYEE, employee_code="1001"))
    resp = client.get("/corporate-calendar/mine")
    assert resp.status_code == 200


def test_mine_page_blocked_for_ea(app, db, client):
    _login(client, _make_user(db, username="ea1", role=UserRole.EA))
    resp = client.get("/corporate-calendar/mine")
    assert resp.status_code == 403


def test_create_page_loads_for_employee(app, db, client):
    _login(client, _make_user(db, username="1001", role=UserRole.EMPLOYEE, employee_code="1001"))
    resp = client.get("/corporate-calendar/create")
    assert resp.status_code == 200
    assert b"Event Title" in resp.data


def test_create_event_end_to_end(app, db, client):
    _login(client, _make_user(db, username="1001", role=UserRole.EMPLOYEE, employee_code="1001"))
    resp = client.post("/corporate-calendar/create", data=_valid_form(), follow_redirects=True)
    assert resp.status_code == 200
    events = cc_service.list_mine("1001")
    assert len(events) == 1
    assert events[0].title == "Division Sync"


def test_edit_page_prefills_existing_event(app, db, client):
    _login(client, _make_user(db, username="1001", role=UserRole.EMPLOYEE, employee_code="1001"))
    event = cc_service.create_event(created_by="1001", **{
        "title": "Original", "event_type": EventType.TRAINING, "event_type_other": None,
        "start_date": date.today(), "end_date": date.today(), "location": "HQ", "organizer": None,
        "description": "desc", "priority_level": PriorityLevel.STANDARD, "md_presence_required": MDInvolvement.NO,
        "involves_external_stakeholder": False, "has_critical_impact": False, "key_participants": None,
        "estimated_attendees": None, "pre_event_actions": None, "documents_to_prepare": None,
        "budget_involved": False, "budget_amount": None, "preparation_deadline": None,
        "conflicts_with_events": None, "conflict_notes": None, "attachment_types": None, "attachment_notes": None,
    })
    resp = client.get(f"/corporate-calendar/{event.id}/edit")
    assert resp.status_code == 200
    assert b"Original" in resp.data


def test_edit_blocked_for_non_owner(app, db, client):
    owner = _make_user(db, username="1001", role=UserRole.EMPLOYEE, employee_code="1001")
    event = cc_service.create_event(created_by=owner.username, **{
        "title": "Original", "event_type": EventType.TRAINING, "event_type_other": None,
        "start_date": date.today(), "end_date": date.today(), "location": "HQ", "organizer": None,
        "description": "desc", "priority_level": PriorityLevel.STANDARD, "md_presence_required": MDInvolvement.NO,
        "involves_external_stakeholder": False, "has_critical_impact": False, "key_participants": None,
        "estimated_attendees": None, "pre_event_actions": None, "documents_to_prepare": None,
        "budget_involved": False, "budget_amount": None, "preparation_deadline": None,
        "conflicts_with_events": None, "conflict_notes": None, "attachment_types": None, "attachment_notes": None,
    })

    _login(client, _make_user(db, username="1002", role=UserRole.EMPLOYEE, employee_code="1002"))
    resp = client.get(f"/corporate-calendar/{event.id}/edit", follow_redirects=True)
    assert resp.status_code == 200
    assert b"only manage events you created" in resp.data


_AJAX_HEADERS = {"X-Requested-With": "XMLHttpRequest"}


def test_create_page_as_fragment_omits_page_chrome(app, db, client):
    _login(client, _make_user(db, username="1001", role=UserRole.EMPLOYEE, employee_code="1001"))
    resp = client.get("/corporate-calendar/create", headers=_AJAX_HEADERS)
    assert resp.status_code == 200
    assert b"data-corporate-event-form" in resp.data
    assert b"<html" not in resp.data  # no sidebar/base layout, just the form


def test_create_via_fragment_returns_204_on_success(app, db, client):
    _login(client, _make_user(db, username="1001", role=UserRole.EMPLOYEE, employee_code="1001"))
    resp = client.post("/corporate-calendar/create", data=_valid_form(), headers=_AJAX_HEADERS)
    assert resp.status_code == 204
    assert resp.data == b""
    assert len(cc_service.list_mine("1001")) == 1


def test_create_via_fragment_reshows_form_on_validation_error(app, db, client):
    _login(client, _make_user(db, username="1001", role=UserRole.EMPLOYEE, employee_code="1001"))
    resp = client.post("/corporate-calendar/create", data=_valid_form(title=""), headers=_AJAX_HEADERS)
    assert resp.status_code == 200
    assert b"data-corporate-event-form" in resp.data
    assert cc_service.list_mine("1001") == []


def test_delete_via_fragment_returns_204(app, db, client):
    _login(client, _make_user(db, username="1001", role=UserRole.EMPLOYEE, employee_code="1001"))
    event = cc_service.create_event(created_by="1001", **{
        "title": "To delete", "event_type": EventType.TRAINING, "event_type_other": None,
        "start_date": date.today(), "end_date": date.today(), "location": "HQ", "organizer": None,
        "description": "desc", "priority_level": PriorityLevel.STANDARD, "md_presence_required": MDInvolvement.NO,
        "involves_external_stakeholder": False, "has_critical_impact": False, "key_participants": None,
        "estimated_attendees": None, "pre_event_actions": None, "documents_to_prepare": None,
        "budget_involved": False, "budget_amount": None, "preparation_deadline": None,
        "conflicts_with_events": None, "conflict_notes": None, "attachment_types": None, "attachment_notes": None,
    })
    resp = client.post(f"/corporate-calendar/{event.id}/delete", headers=_AJAX_HEADERS)
    assert resp.status_code == 204
    assert cc_service.list_mine("1001") == []


def test_edit_via_fragment_shows_error_for_non_owner(app, db, client):
    owner = _make_user(db, username="1001", role=UserRole.EMPLOYEE, employee_code="1001")
    event = cc_service.create_event(created_by=owner.username, **{
        "title": "Original", "event_type": EventType.TRAINING, "event_type_other": None,
        "start_date": date.today(), "end_date": date.today(), "location": "HQ", "organizer": None,
        "description": "desc", "priority_level": PriorityLevel.STANDARD, "md_presence_required": MDInvolvement.NO,
        "involves_external_stakeholder": False, "has_critical_impact": False, "key_participants": None,
        "estimated_attendees": None, "pre_event_actions": None, "documents_to_prepare": None,
        "budget_involved": False, "budget_amount": None, "preparation_deadline": None,
        "conflicts_with_events": None, "conflict_notes": None, "attachment_types": None, "attachment_notes": None,
    })

    _login(client, _make_user(db, username="1002", role=UserRole.EMPLOYEE, employee_code="1002"))
    resp = client.get(f"/corporate-calendar/{event.id}/edit", headers=_AJAX_HEADERS)
    assert resp.status_code == 200
    assert b"only manage events you created" in resp.data
    assert b"<html" not in resp.data


def test_index_and_events_json_for_ea(app, db, client, monkeypatch):
    cc_service.create_event(created_by="1001", **{
        "title": "Conference", "event_type": EventType.CONFERENCE_TRADE_SHOW, "event_type_other": None,
        "start_date": date.today(), "end_date": date.today(), "location": "HQ", "organizer": None,
        "description": "desc", "priority_level": PriorityLevel.HIGH, "md_presence_required": MDInvolvement.YES,
        "involves_external_stakeholder": True, "has_critical_impact": False, "key_participants": None,
        "estimated_attendees": None, "pre_event_actions": None, "documents_to_prepare": None,
        "budget_involved": False, "budget_amount": None, "preparation_deadline": None,
        "conflicts_with_events": None, "conflict_notes": None, "attachment_types": None, "attachment_notes": None,
    })

    repo = FakeEmployeeRepository([make_employee(code="1001", name="Alice", department="Finance")])
    app.extensions["employee_repository"] = repo

    _login(client, _make_user(db, username="ea1", role=UserRole.EA))

    resp = client.get("/corporate-calendar/")
    assert resp.status_code == 200
    assert b"Finance" in resp.data

    resp = client.get("/corporate-calendar/events.json")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload[0]["extendedProps"]["division"] == "Finance"
    assert payload[0]["extendedProps"]["mdEligible"] is True


def test_index_table_renders_every_optional_field_populated(app, db, client):
    """The detailed table (Section 2-5 for every event, not just what fits
    a calendar cell — see index.html) has a template branch for every
    optional field; exercise all of them at once rather than only the
    "empty" defaults the other index test leaves in place.
    """
    from decimal import Decimal

    from app.models.corporate_event import ConflictStatus

    cc_service.create_event(created_by="1001", **{
        "title": "AIOS 2026 Conference", "event_type": EventType.OTHER, "event_type_other": "Trade Fair",
        "start_date": date.today(), "end_date": date.today() + timedelta(days=2), "location": "Mumbai",
        "organizer": "Alice", "description": "Annual conference", "priority_level": PriorityLevel.CRITICAL,
        "md_presence_required": MDInvolvement.YES, "involves_external_stakeholder": True, "has_critical_impact": True,
        "key_participants": "Alice, Bob", "estimated_attendees": 40,
        "pre_event_actions": "Book travel", "documents_to_prepare": "Agenda",
        "budget_involved": True, "budget_amount": Decimal("12500.50"), "preparation_deadline": date.today(),
        "conflicts_with_events": ConflictStatus.YES, "conflict_notes": "Overlaps with Board Meeting",
        "attachment_types": ["BROCHURE_INVITE", "TRAVEL_PLAN"], "attachment_notes": "See shared drive",
    })

    repo = FakeEmployeeRepository([make_employee(code="1001", name="Alice", department="Finance")])
    app.extensions["employee_repository"] = repo
    _login(client, _make_user(db, username="ea1", role=UserRole.EA))

    resp = client.get("/corporate-calendar/")
    assert resp.status_code == 200
    assert b"AIOS 2026 Conference" in resp.data
    assert b"Trade Fair" in resp.data  # event_type_label falls back to "Other (specify)" text
    assert b"12500.50" in resp.data
    assert b"Book travel" in resp.data
    assert b"See shared drive" in resp.data
    assert b"Overlaps with Board Meeting" in resp.data


def test_index_table_shows_conflict_alert_for_overlapping_events(app, db, client):
    start = date.today()
    a = cc_service.create_event(created_by="1001", **{
        "title": "Board Meeting", "event_type": EventType.BOARD_MANAGEMENT_MEETING, "event_type_other": None,
        "start_date": start, "end_date": start + timedelta(days=1), "location": "HQ", "organizer": None,
        "description": "desc", "priority_level": PriorityLevel.CRITICAL, "md_presence_required": MDInvolvement.YES,
        "involves_external_stakeholder": False, "has_critical_impact": True, "key_participants": None,
        "estimated_attendees": None, "pre_event_actions": None, "documents_to_prepare": None,
        "budget_involved": False, "budget_amount": None, "preparation_deadline": None,
        "conflicts_with_events": None, "conflict_notes": None, "attachment_types": None, "attachment_notes": None,
    })
    cc_service.create_event(created_by="1002", **{
        "title": "Regulatory Audit", "event_type": EventType.REGULATORY_AUDIT, "event_type_other": None,
        "start_date": start + timedelta(days=1), "end_date": start + timedelta(days=2), "location": "HQ", "organizer": None,
        "description": "desc", "priority_level": PriorityLevel.CRITICAL, "md_presence_required": MDInvolvement.YES,
        "involves_external_stakeholder": True, "has_critical_impact": False, "key_participants": None,
        "estimated_attendees": None, "pre_event_actions": None, "documents_to_prepare": None,
        "budget_involved": False, "budget_amount": None, "preparation_deadline": None,
        "conflicts_with_events": None, "conflict_notes": None, "attachment_types": None, "attachment_notes": None,
    })

    repo = FakeEmployeeRepository([
        make_employee(code="1001", name="Alice", department="Finance"),
        make_employee(code="1002", name="Bob", department="Compliance"),
    ])
    app.extensions["employee_repository"] = repo
    _login(client, _make_user(db, username="ea1", role=UserRole.EA))

    resp = client.get("/corporate-calendar/")
    assert resp.status_code == 200
    assert b"Scheduling conflict" in resp.data
    assert f'href="#eventRow{a.id}"'.encode() in resp.data
