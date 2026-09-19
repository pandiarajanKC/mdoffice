from datetime import date, timedelta

import pytest

from app.models.corporate_event import EventType, MDInvolvement, PriorityLevel, division_color_name
from app.services import corporate_calendar_service as cc_service
from tests.fakes import FakeEmployeeRepository, make_employee


def _span():
    start = date.today()
    return start, start + timedelta(days=2)


def _fields(**overrides):
    start, end = _span()
    fields = dict(
        title="Division Sync", event_type=EventType.TRAINING, event_type_other=None,
        start_date=start, end_date=end, location="HQ", organizer="Alice", description="Quarterly sync",
        priority_level=PriorityLevel.STANDARD, md_presence_required=MDInvolvement.NO,
        involves_external_stakeholder=False, has_critical_impact=False,
        key_participants=None, estimated_attendees=None,
        pre_event_actions=None, documents_to_prepare=None,
        budget_involved=False, budget_amount=None, preparation_deadline=None,
        conflicts_with_events=None, conflict_notes=None,
        attachment_types=None, attachment_notes=None,
    )
    fields.update(overrides)
    return fields


def test_create_event_requires_title(app, db):
    with pytest.raises(cc_service.CorporateEventValidationError):
        cc_service.create_event(created_by="1001", **_fields(title="  "))


def test_create_event_rejects_end_before_start(app, db):
    start, end = _span()
    with pytest.raises(cc_service.CorporateEventValidationError):
        cc_service.create_event(created_by="1001", **_fields(start_date=end, end_date=start))


def test_create_event_requires_other_type_label(app, db):
    with pytest.raises(cc_service.CorporateEventValidationError):
        cc_service.create_event(created_by="1001", **_fields(event_type=EventType.OTHER, event_type_other="  "))


def test_create_event_success(app, db):
    event = cc_service.create_event(created_by="1001", **_fields(title="  Division Sync  ", location=" HQ "))
    assert event.title == "Division Sync"  # trimmed
    assert event.location == "HQ"
    assert event.id is not None


def test_list_mine_only_returns_own_events(app, db):
    cc_service.create_event(created_by="1001", **_fields(title="Mine"))
    cc_service.create_event(created_by="1002", **_fields(title="Theirs"))

    mine = cc_service.list_mine("1001")
    assert [e.title for e in mine] == ["Mine"]


def test_update_event_blocks_non_owner(app, db):
    event = cc_service.create_event(created_by="1001", **_fields(title="Mine"))
    with pytest.raises(cc_service.CorporateEventPermissionError):
        cc_service.update_event(event.id, requested_by="1002", **_fields(title="Hijacked"))


def test_delete_event_blocks_non_owner(app, db):
    event = cc_service.create_event(created_by="1001", **_fields(title="Mine"))
    with pytest.raises(cc_service.CorporateEventPermissionError):
        cc_service.delete_event(event.id, requested_by="1002")


def test_reschedule_event_moves_span_only(app, db):
    event = cc_service.create_event(created_by="1001", **_fields(title="Mine"))
    new_start = event.start_date + timedelta(days=5)
    new_end = event.end_date + timedelta(days=5)
    cc_service.reschedule_event(event.id, requested_by="1001", start_date=new_start, end_date=new_end)

    assert event.start_date == new_start
    assert event.end_date == new_end
    assert event.title == "Mine"  # untouched


def test_division_color_is_stable_for_same_division():
    assert division_color_name("Finance") == division_color_name("Finance")


def test_divisions_legend_groups_by_department(app, db):
    cc_service.create_event(created_by="1001", **_fields(title="A"))
    cc_service.create_event(created_by="1002", **_fields(title="B"))
    # A second event from the same division shouldn't produce a duplicate legend row.
    cc_service.create_event(created_by="1001", **_fields(title="C"))

    repo = FakeEmployeeRepository([
        make_employee(code="1001", name="Alice", department="Finance"),
        make_employee(code="1002", name="Bob", department="IT"),
    ])
    legend = cc_service.divisions_legend(repo)
    assert {row["division"] for row in legend} == {"Finance", "IT"}
    assert len(legend) == 2


def test_event_to_calendar_dict_includes_division_when_requested(app, db):
    event = cc_service.create_event(created_by="1001", **_fields(title="A", location="HQ"))
    repo = FakeEmployeeRepository([make_employee(code="1001", name="Alice", department="Finance")])

    payload = cc_service.event_to_calendar_dict(event, employee_repository=repo, include_division=True)
    assert payload["extendedProps"]["division"] == "Finance"
    assert payload["extendedProps"]["createdByName"] == "Alice"
    division_color_class = "tag-" + payload["extendedProps"]["colorName"]
    assert division_color_class in payload["classNames"]

    plain = cc_service.event_to_calendar_dict(event)
    assert "division" not in plain["extendedProps"]
    # Uncolored by division on an employee's own calendar — colored by
    # priority instead (see event_to_calendar_dict's docstring).
    assert plain["extendedProps"]["colorName"] == "sky"  # STANDARD priority's badge color


def test_is_md_eligible_true_when_any_criterion_is_met(app, db):
    external = cc_service.create_event(created_by="1001", **_fields(title="A", involves_external_stakeholder=True))
    assert external.is_md_eligible is True

    critical_impact = cc_service.create_event(created_by="1001", **_fields(title="B", has_critical_impact=True))
    assert critical_impact.is_md_eligible is True

    awareness = cc_service.create_event(created_by="1001", **_fields(title="C", md_presence_required=MDInvolvement.AWARENESS_ONLY))
    assert awareness.is_md_eligible is True

    routine = cc_service.create_event(created_by="1001", **_fields(title="D"))
    assert routine.is_md_eligible is False


def test_find_conflicts_flags_overlapping_date_spans(app, db):
    start = date.today()
    a = cc_service.create_event(created_by="1001", **_fields(title="A", start_date=start, end_date=start + timedelta(days=2)))
    # Overlaps A (shares day start+2) but not C below.
    b = cc_service.create_event(created_by="1002", **_fields(title="B", start_date=start + timedelta(days=2), end_date=start + timedelta(days=4)))
    # Entirely after both — no overlap with either.
    c = cc_service.create_event(created_by="1002", **_fields(title="C", start_date=start + timedelta(days=10), end_date=start + timedelta(days=11)))

    conflicts = cc_service.find_conflicts([a, b, c])
    assert {e.id for e in conflicts[a.id]} == {b.id}
    assert {e.id for e in conflicts[b.id]} == {a.id}
    assert conflicts[c.id] == []


def test_find_conflicts_no_overlap_for_adjacent_non_touching_spans(app, db):
    start = date.today()
    a = cc_service.create_event(created_by="1001", **_fields(title="A", start_date=start, end_date=start + timedelta(days=1)))
    b = cc_service.create_event(created_by="1002", **_fields(title="B", start_date=start + timedelta(days=2), end_date=start + timedelta(days=3)))

    conflicts = cc_service.find_conflicts([a, b])
    assert conflicts[a.id] == []
    assert conflicts[b.id] == []
