from datetime import datetime, timedelta

import pytest

from app.models.meeting import MeetingStatus
from app.models.task import TaskStatus
from app.services import meeting_service, project_service, task_service


def _create(db, **overrides):
    start = overrides.pop("start_datetime", datetime.now() + timedelta(hours=1))
    end = overrides.pop("end_datetime", start + timedelta(hours=1))
    return meeting_service.create_meeting(
        title=overrides.pop("title", "Production Review"),
        start_datetime=start,
        end_datetime=end,
        created_by=overrides.pop("created_by", "admin1"),
        **overrides,
    )


def test_create_meeting_defaults(app, db):
    meeting = _create(db)
    assert meeting.id is not None
    assert meeting.status == MeetingStatus.SCHEDULED


def test_start_then_complete_workflow(app, db):
    meeting = _create(db)
    meeting_service.start_meeting(meeting, performed_by="admin1")
    assert meeting.status == MeetingStatus.IN_PROGRESS
    assert meeting.started_at is not None

    meeting_service.complete_meeting(meeting, performed_by="admin1")
    assert meeting.status == MeetingStatus.COMPLETED
    assert meeting.completed_at is not None


def test_cannot_complete_a_meeting_that_has_not_started(app, db):
    meeting = _create(db)
    with pytest.raises(meeting_service.MeetingWorkflowError):
        meeting_service.complete_meeting(meeting, performed_by="admin1")


def test_cannot_start_a_completed_meeting(app, db):
    meeting = _create(db)
    meeting_service.start_meeting(meeting, performed_by="admin1")
    meeting_service.complete_meeting(meeting, performed_by="admin1")
    with pytest.raises(meeting_service.MeetingWorkflowError):
        meeting_service.start_meeting(meeting, performed_by="admin1")


def test_reopen_completed_meeting(app, db):
    meeting = _create(db)
    meeting_service.start_meeting(meeting, performed_by="admin1")
    meeting_service.complete_meeting(meeting, performed_by="admin1")
    meeting_service.reopen_meeting(meeting, performed_by="admin1")
    assert meeting.status == MeetingStatus.IN_PROGRESS
    assert meeting.completed_at is None


def test_add_note_and_decision(app, db):
    meeting = _create(db)
    note = meeting_service.add_note(meeting, note_text="Finance to confirm budget", created_by="admin1")
    decision = meeting_service.add_decision(meeting, decision_text="Proceed with Phase II", created_by="admin1")

    assert note.sequence == 1
    assert len(meeting.notes) == 1
    assert len(meeting.decisions) == 1
    assert decision.decision_text == "Proceed with Phase II"


def test_convert_note_to_decision(app, db):
    meeting = _create(db)
    note = meeting_service.add_note(meeting, note_text="Actually we decided on Vendor B", created_by="admin1")

    decision = meeting_service.convert_note_to_decision(note, performed_by="admin1")

    assert decision.decision_text == "Actually we decided on Vendor B"
    assert decision.created_by == "admin1"
    assert list(meeting.notes) == []
    assert list(meeting.decisions) == [decision]


def test_note_sequence_increments(app, db):
    meeting = _create(db)
    meeting_service.add_note(meeting, note_text="First", created_by="admin1")
    second = meeting_service.add_note(meeting, note_text="Second", created_by="admin1")
    assert second.sequence == 2


def test_completion_checklist_reflects_state(app, db):
    meeting = _create(db)
    checklist = meeting_service.completion_checklist(meeting)
    assert checklist == {
        "notes_captured": False,
        "decisions_captured": False,
        "actions_assigned": False,
        "due_dates_added": False,
    }

    meeting_service.add_note(meeting, note_text="x", created_by="admin1")
    meeting_service.add_decision(meeting, decision_text="x", created_by="admin1")
    checklist = meeting_service.completion_checklist(meeting)
    assert checklist["notes_captured"] is True
    assert checklist["decisions_captured"] is True


def test_followup_status_no_actions(app, db):
    meeting = _create(db)
    assert meeting_service.followup_status(meeting) == "No Actions"


# ---------- prep brief (carry-over context from a related prior meeting) ----------

def test_prep_brief_none_for_completed_meeting(app, db):
    meeting = _create(db)
    meeting_service.start_meeting(meeting, performed_by="admin1")
    meeting_service.complete_meeting(meeting, performed_by="admin1")
    assert meeting_service.prep_brief(meeting) is None


def test_prep_brief_none_without_a_related_prior_meeting(app, db):
    meeting = _create(db, title="Brand New Series")
    assert meeting_service.prep_brief(meeting) is None


def test_prep_brief_matches_by_same_title(app, db):
    past = _create(db, title="Weekly Ops Review", start_datetime=datetime.now() - timedelta(days=7))
    meeting_service.start_meeting(past, performed_by="admin1")
    meeting_service.add_decision(past, decision_text="Approved the new vendor", created_by="admin1")
    open_task = task_service.create_meeting_action(past, title="Follow up with vendor", employee_codes=["1001"], created_by="admin1")
    done_task = task_service.create_meeting_action(past, title="Send agenda", employee_codes=["1001"], created_by="admin1")
    task_service.update_status(done_task, new_status=TaskStatus.COMPLETED, performed_by="admin1")
    meeting_service.complete_meeting(past, performed_by="admin1")

    upcoming = _create(db, title="Weekly Ops Review", start_datetime=datetime.now() + timedelta(days=7))

    brief = meeting_service.prep_brief(upcoming)
    assert brief is not None
    assert brief["previous_meeting"].id == past.id
    assert [t.id for t in brief["open_actions"]] == [open_task.id]  # completed task excluded
    assert brief["decisions"][0].decision_text == "Approved the new vendor"


def test_prep_brief_matches_by_shared_tagged_project(app, db):
    past = _create(db, title="Ad-hoc Sync")
    meeting_service.start_meeting(past, performed_by="admin1")
    meeting_service.complete_meeting(past, performed_by="admin1")

    upcoming = _create(db, title="Different Title Entirely")
    project = project_service.create_project(project_code="P-100", name="Shared Project", owner_employee_code="1001", created_by="admin1")
    project_service.tag_meeting(project, past, performed_by="admin1")
    project_service.tag_meeting(project, upcoming, performed_by="admin1")

    brief = meeting_service.prep_brief(upcoming)
    assert brief is not None
    assert brief["previous_meeting"].id == past.id


# ---------- meeting history (full series by title) ----------

def test_meeting_history_includes_only_same_title_meetings(app, db):
    a1 = _create(db, title="Weekly Ops Review", start_datetime=datetime.now() - timedelta(days=14))
    a2 = _create(db, title="Weekly Ops Review", start_datetime=datetime.now() - timedelta(days=7))
    _create(db, title="Something Else Entirely")

    history = meeting_service.meeting_history(a2)
    ids = [m.id for m in history["series"]]
    assert set(ids) == {a1.id, a2.id}


def test_meeting_history_matches_title_case_insensitively(app, db):
    a1 = _create(db, title="Weekly Ops Review", start_datetime=datetime.now() - timedelta(days=7))
    a2 = _create(db, title="weekly ops review", start_datetime=datetime.now())

    history = meeting_service.meeting_history(a2)
    assert {m.id for m in history["series"]} == {a1.id, a2.id}


def test_meeting_history_orders_most_recent_first(app, db):
    older = _create(db, title="Weekly Ops Review", start_datetime=datetime.now() - timedelta(days=14))
    newer = _create(db, title="Weekly Ops Review", start_datetime=datetime.now() - timedelta(days=1))

    history = meeting_service.meeting_history(newer)
    assert [m.id for m in history["series"]] == [newer.id, older.id]


def test_meeting_history_aggregates_participants_across_the_series(app, db):
    past = _create(db, title="Weekly Ops Review", start_datetime=datetime.now() - timedelta(days=7), organizer="Alice")
    past.attendees_text = "Alice, Bob"
    current = _create(db, title="Weekly Ops Review")
    current.attendees_text = "Alice, Priya"

    history = meeting_service.meeting_history(current)
    assert history["participants"] == ["Alice", "Bob", "Priya"]


def test_meeting_history_single_occurrence_still_returns_itself(app, db):
    meeting = _create(db, title="Brand New Series")
    history = meeting_service.meeting_history(meeting)
    assert [m.id for m in history["series"]] == [meeting.id]


# ---------- effectiveness score ----------

def test_effectiveness_score_none_for_uncompleted_meeting(app, db):
    meeting = _create(db)
    assert meeting_service.effectiveness_score(meeting) is None


def test_effectiveness_score_high_for_a_well_run_meeting(app, db):
    from app.models.meeting_ai import MeetingFollowupEmail
    from app.models.mixins import utcnow

    meeting = _create(db, start_datetime=datetime.now() - timedelta(hours=1))
    meeting.attendees_text = "Alice, Bob"
    meeting_service.start_meeting(meeting, performed_by="admin1")
    meeting_service.add_decision(meeting, decision_text="Approved the plan", created_by="admin1")
    meeting_service.add_note(meeting, note_text="Discussed Q3 roadmap", created_by="admin1")
    task = task_service.create_meeting_action(meeting, title="Send the report", employee_codes=["1001"], created_by="admin1")
    task_service.update_status(task, new_status=TaskStatus.COMPLETED, performed_by="admin1")
    meeting_service.complete_meeting(meeting, performed_by="admin1")
    db.session.add(MeetingFollowupEmail(meeting_id=meeting.id, subject="Recap", body_text="...", generated_at=utcnow()))
    db.session.commit()

    result = meeting_service.effectiveness_score(meeting)
    assert result["score"] >= 80
    assert result["tier"] == "great"
    labels = [c["label"] for c in result["components"]]
    assert "Recurrence trend" not in labels  # no prior occurrence, weight redistributed
    assert round(sum(c["points"] for c in result["components"])) == result["score"]


def test_effectiveness_score_low_for_an_unproductive_meeting(app, db):
    meeting = _create(db, start_datetime=datetime.now() - timedelta(hours=1))
    meeting_service.start_meeting(meeting, performed_by="admin1")
    meeting_service.complete_meeting(meeting, performed_by="admin1")

    result = meeting_service.effectiveness_score(meeting)
    assert result["score"] < 50
    assert result["tier"] == "poor"


def test_effectiveness_score_recurrence_trend_reflects_unresolved_backlog(app, db):
    past = _create(db, title="Weekly Ops Review", start_datetime=datetime.now() - timedelta(days=7, hours=1))
    meeting_service.start_meeting(past, performed_by="admin1")
    task_service.create_meeting_action(past, title="Still not done", employee_codes=["1001"], created_by="admin1")
    meeting_service.complete_meeting(past, performed_by="admin1")

    current = _create(db, title="Weekly Ops Review", start_datetime=datetime.now() - timedelta(hours=1))
    meeting_service.start_meeting(current, performed_by="admin1")
    meeting_service.complete_meeting(current, performed_by="admin1")

    result = meeting_service.effectiveness_score(current)
    recurrence = next(c for c in result["components"] if c["label"] == "Recurrence trend")
    assert recurrence["score"] == 0  # the prior meeting's only action item is still unresolved


# ---------- recurring meeting templates ----------

def test_save_as_template_captures_meeting_shape(app, db):
    meeting = _create(
        db, title="Weekly Ops Review", location="Board Room", meeting_type="Recurring",
        start_datetime=datetime(2026, 1, 5, 10, 0), end_datetime=datetime(2026, 1, 5, 10, 45),
    )
    template = meeting_service.save_as_template(meeting, name="Weekly Ops", created_by="admin1")
    assert template.title == "Weekly Ops Review"
    assert template.location == "Board Room"
    assert template.duration_minutes == 45
    assert template in meeting_service.list_templates()


def test_save_as_template_rejects_duplicate_name(app, db):
    meeting = _create(db)
    meeting_service.save_as_template(meeting, name="Ops Review", created_by="admin1")
    with pytest.raises(meeting_service.MeetingTemplateError):
        meeting_service.save_as_template(meeting, name="Ops Review", created_by="admin1")


def test_save_as_template_rejects_blank_name(app, db):
    meeting = _create(db)
    with pytest.raises(meeting_service.MeetingTemplateError):
        meeting_service.save_as_template(meeting, name="   ", created_by="admin1")


def test_delete_template(app, db):
    meeting = _create(db)
    template = meeting_service.save_as_template(meeting, name="To Delete", created_by="admin1")
    meeting_service.delete_template(template)
    assert meeting_service.list_templates() == []
