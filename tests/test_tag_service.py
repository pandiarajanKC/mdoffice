from datetime import datetime, timedelta

import pytest

from app.extensions import db as _db
from app.models.project import Project, ProjectStatus
from app.models.task import Task, TaskPriority, TaskStatus, TaskType
from app.services import meeting_service, tag_service


def _meeting(db, **overrides):
    start = overrides.pop("start_datetime", datetime.now() + timedelta(hours=1))
    end = overrides.pop("end_datetime", start + timedelta(hours=1))
    return meeting_service.create_meeting(
        title=overrides.pop("title", "Production Review"),
        start_datetime=start,
        end_datetime=end,
        created_by=overrides.pop("created_by", "admin1"),
        **overrides,
    )


def _project(db, **overrides):
    project = Project(
        project_code=overrides.pop("project_code", "MES-001"),
        name=overrides.pop("name", "MES Integration"),
        owner_employee_code=overrides.pop("owner_employee_code", "1001"),
        status=ProjectStatus.OPEN,
        created_by=overrides.pop("created_by", "admin1"),
    )
    db.session.add(project)
    db.session.commit()
    return project


def _task(db, **overrides):
    task = Task(
        task_type=overrides.pop("task_type", TaskType.GENERAL_FOLLOWUP),
        title=overrides.pop("title", "Send revised numbers"),
        status=overrides.pop("status", TaskStatus.OPEN),
        priority=overrides.pop("priority", TaskPriority.MEDIUM),
        created_by=overrides.pop("created_by", "admin1"),
        **overrides,
    )
    db.session.add(task)
    db.session.commit()
    return task


# ---------------------------------------------------------------- tags ----

def test_create_tag_requires_name(app, db):
    with pytest.raises(tag_service.TagValidationError):
        tag_service.create_tag("  ", created_by="admin1")


def test_create_tag_rejects_case_insensitive_duplicate(app, db):
    tag_service.create_tag("Board Priority", created_by="admin1")
    with pytest.raises(tag_service.TagValidationError):
        tag_service.create_tag("board priority", created_by="admin1")


def test_create_tag_cycles_default_colors(app, db):
    from app.models.tag import TAG_COLORS

    t1 = tag_service.create_tag("Alpha", created_by="admin1")
    t2 = tag_service.create_tag("Beta", created_by="admin1")
    assert t1.color == TAG_COLORS[0]
    assert t2.color == TAG_COLORS[1]


def test_get_or_create_tag_reuses_existing(app, db):
    created = tag_service.create_tag("Urgent", created_by="admin1")
    reused = tag_service.get_or_create_tag("URGENT", created_by="admin1")
    assert reused.id == created.id
    assert tag_service.list_all_tags() == [created]


def test_delete_tag_cascades_assignments(app, db):
    meeting = _meeting(db)
    tag, _ = tag_service.tag_entity(
        tag_name="Board Priority", entity_type="MEETING", entity_id=meeting.id, tagged_by="admin1"
    )
    assert tag_service.get_assignments_for_entity("MEETING", meeting.id)

    tag_service.delete_tag(tag)
    assert tag_service.get_assignments_for_entity("MEETING", meeting.id) == []


# ---------------------------------------------------------- assignments ----

def test_tag_entity_creates_tag_on_first_use(app, db):
    meeting = _meeting(db)
    tag, newly_linked = tag_service.tag_entity(
        tag_name="Q4 Budget", entity_type="MEETING", entity_id=meeting.id, tagged_by="admin1"
    )
    assert newly_linked is True
    assert tag.name == "Q4 Budget"
    assert [a.tag.name for a in tag_service.get_assignments_for_entity("MEETING", meeting.id)] == ["Q4 Budget"]


def test_tag_entity_is_idempotent(app, db):
    meeting = _meeting(db)
    tag_service.tag_entity(tag_name="Urgent", entity_type="MEETING", entity_id=meeting.id, tagged_by="admin1")
    tag, newly_linked = tag_service.tag_entity(
        tag_name="urgent", entity_type="MEETING", entity_id=meeting.id, tagged_by="admin1"
    )
    assert newly_linked is False
    assert len(tag_service.get_assignments_for_entity("MEETING", meeting.id)) == 1


def test_tag_entity_rejects_unknown_entity_type(app, db):
    with pytest.raises(tag_service.TagValidationError):
        tag_service.tag_entity(tag_name="X", entity_type="BOGUS", entity_id="1", tagged_by="admin1")


def test_tag_entity_rejects_missing_entity(app, db):
    with pytest.raises(tag_service.TagValidationError):
        tag_service.tag_entity(tag_name="X", entity_type="MEETING", entity_id="999999", tagged_by="admin1")


def test_tag_entity_rejects_blank_name(app, db):
    meeting = _meeting(db)
    with pytest.raises(tag_service.TagValidationError):
        tag_service.tag_entity(tag_name="   ", entity_type="MEETING", entity_id=meeting.id, tagged_by="admin1")


def test_untag_entity_removes_just_that_link(app, db):
    meeting = _meeting(db)
    project = _project(db)
    tag = tag_service.create_tag("Cross-cutting", created_by="admin1")
    tag_service.tag_entity(tag_name=tag.name, entity_type="MEETING", entity_id=meeting.id, tagged_by="admin1")
    tag_service.tag_entity(tag_name=tag.name, entity_type="PROJECT", entity_id=project.id, tagged_by="admin1")

    [assignment] = tag_service.get_assignments_for_entity("MEETING", meeting.id)
    tag_service.untag_entity(assignment.id)

    assert tag_service.get_assignments_for_entity("MEETING", meeting.id) == []
    assert len(tag_service.get_assignments_for_entity("PROJECT", project.id)) == 1


def test_untag_entity_missing_id_is_a_noop(app, db):
    tag_service.untag_entity(999999)  # should not raise


# ------------------------------------------------------------ tag view ----

def test_get_entities_for_tag_groups_by_type(app, db):
    meeting = _meeting(db)
    project = _project(db)
    task = _task(db)

    tag = tag_service.create_tag("All Hands", created_by="admin1")
    tag_service.tag_entity(tag_name=tag.name, entity_type="MEETING", entity_id=meeting.id, tagged_by="admin1")
    tag_service.tag_entity(tag_name=tag.name, entity_type="PROJECT", entity_id=project.id, tagged_by="admin1")
    tag_service.tag_entity(tag_name=tag.name, entity_type="TASK", entity_id=task.id, tagged_by="admin1")

    grouped = tag_service.get_entities_for_tag(tag)
    assert grouped["meetings"] == [meeting]
    assert grouped["projects"] == [project]
    assert grouped["tasks"] == [task]


def test_get_entities_for_tag_skips_deleted_rows(app, db):
    meeting = _meeting(db)
    tag = tag_service.create_tag("Stale", created_by="admin1")
    tag_service.tag_entity(tag_name=tag.name, entity_type="MEETING", entity_id=meeting.id, tagged_by="admin1")

    _db.session.delete(meeting)
    _db.session.commit()

    grouped = tag_service.get_entities_for_tag(tag)
    assert grouped["meetings"] == []


def test_usage_counts(app, db):
    meeting = _meeting(db)
    project = _project(db)
    tag_a = tag_service.create_tag("A", created_by="admin1")
    tag_b = tag_service.create_tag("B", created_by="admin1")
    tag_service.tag_entity(tag_name="A", entity_type="MEETING", entity_id=meeting.id, tagged_by="admin1")
    tag_service.tag_entity(tag_name="A", entity_type="PROJECT", entity_id=project.id, tagged_by="admin1")

    counts = tag_service.usage_counts()
    assert counts[tag_a.id] == 2
    assert tag_b.id not in counts
