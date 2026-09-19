"""Tag Master service: create/manage free-form Tags and attach them to
meetings, projects and tasks (tasks covers both meeting action items and
standalone follow-ups — see app/models/task.py).

Kept deliberately separate from app/services/meeting_service.py's and
app/services/project_service.py's meeting<->project "tag" linking, which
is an unrelated, pre-existing feature — see app/models/tag.py's module
docstring for why the two share the English word but not the model.
"""
from __future__ import annotations

from app.extensions import db
from app.models.meeting import Meeting
from app.models.project import Project
from app.models.tag import ENTITY_TYPES, TAG_COLORS, Tag, TagAssignment
from app.models.task import Task

ENTITY_MODELS = {"MEETING": Meeting, "PROJECT": Project, "TASK": Task}


class TagValidationError(Exception):
    pass


# ---------------------------------------------------------------- tags ----

def list_all_tags() -> list[Tag]:
    return Tag.query.order_by(Tag.name.asc()).all()


def all_tag_names() -> list[str]:
    return [name for (name,) in db.session.query(Tag.name).order_by(Tag.name.asc()).all()]


def usage_counts() -> dict[int, int]:
    """tag_id -> number of items it's attached to, for the Tag Master list."""
    rows = (
        db.session.query(TagAssignment.tag_id, db.func.count(TagAssignment.id))
        .group_by(TagAssignment.tag_id)
        .all()
    )
    return {tag_id: count for tag_id, count in rows}


def find_by_name_ci(name: str) -> Tag | None:
    name = (name or "").strip()
    if not name:
        return None
    return Tag.query.filter(db.func.lower(Tag.name) == name.lower()).first()


def _next_color() -> str:
    return TAG_COLORS[Tag.query.count() % len(TAG_COLORS)]


def create_tag(name: str, *, created_by: str, color: str | None = None) -> Tag:
    name = (name or "").strip()
    if not name:
        raise TagValidationError("Tag name is required.")
    if len(name) > 80:
        raise TagValidationError("Tag name is too long (max 80 characters).")
    if find_by_name_ci(name):
        raise TagValidationError(f"A tag named '{name}' already exists.")

    tag = Tag(name=name, color=color if color in TAG_COLORS else _next_color(), created_by=created_by)
    db.session.add(tag)
    db.session.commit()
    return tag


def get_or_create_tag(name: str, *, created_by: str) -> Tag:
    existing = find_by_name_ci(name)
    if existing:
        return existing
    return create_tag(name, created_by=created_by)


def delete_tag(tag: Tag) -> None:
    db.session.delete(tag)  # cascades its TagAssignment rows (see Tag.assignments)
    db.session.commit()


# ------------------------------------------------------------ assignment ----

def entity_exists(entity_type: str, entity_id: str) -> bool:
    model = ENTITY_MODELS.get(entity_type)
    if model is None:
        return False
    try:
        pk = int(entity_id)
    except (TypeError, ValueError):
        return False
    return db.session.get(model, pk) is not None


def get_assignments_for_entity(entity_type: str, entity_id: str) -> list[TagAssignment]:
    return (
        TagAssignment.query.filter_by(entity_type=entity_type, entity_id=str(entity_id))
        .join(Tag)
        .order_by(Tag.name.asc())
        .all()
    )


def tag_entity(*, tag_name: str, entity_type: str, entity_id: str, tagged_by: str) -> tuple[Tag, bool]:
    """Create the tag if it doesn't exist yet and attach it to the entity.

    Returns (tag, newly_linked) — newly_linked is False if that entity was
    already carrying this tag (a harmless no-op, not an error).
    """
    entity_type = (entity_type or "").strip().upper()
    if entity_type not in ENTITY_TYPES:
        raise TagValidationError("Unknown item type.")
    if not entity_exists(entity_type, entity_id):
        raise TagValidationError("That item no longer exists.")

    tag_name = (tag_name or "").strip()
    if not tag_name:
        raise TagValidationError("Please type a tag name.")

    tag = get_or_create_tag(tag_name, created_by=tagged_by)

    existing = TagAssignment.query.filter_by(
        tag_id=tag.id, entity_type=entity_type, entity_id=str(entity_id)
    ).first()
    if existing:
        return tag, False

    assignment = TagAssignment(
        tag_id=tag.id, entity_type=entity_type, entity_id=str(entity_id), tagged_by=tagged_by
    )
    db.session.add(assignment)
    db.session.commit()
    return tag, True


def untag_entity(assignment_id: int) -> None:
    assignment = db.session.get(TagAssignment, assignment_id)
    if assignment is None:
        return
    db.session.delete(assignment)
    db.session.commit()


# ---------------------------------------------------------- tag-wise view ----

def get_entities_for_tag(tag: Tag) -> dict[str, list]:
    """Every meeting/project/task currently carrying this tag, for the
    "tag-wise contents" page — skips any assignment whose target row has
    since been deleted rather than erroring.
    """
    meetings: list[Meeting] = []
    projects: list[Project] = []
    tasks: list[Task] = []

    for assignment in tag.assignments:
        model = ENTITY_MODELS.get(assignment.entity_type)
        if model is None:
            continue
        try:
            pk = int(assignment.entity_id)
        except (TypeError, ValueError):
            continue
        record = db.session.get(model, pk)
        if record is None:
            continue
        if assignment.entity_type == "MEETING":
            meetings.append(record)
        elif assignment.entity_type == "PROJECT":
            projects.append(record)
        elif assignment.entity_type == "TASK":
            tasks.append(record)

    meetings.sort(key=lambda m: m.start_datetime, reverse=True)
    projects.sort(key=lambda p: p.name.lower())
    tasks.sort(key=lambda t: t.due_date or t.created_at.date())

    return {"meetings": meetings, "projects": projects, "tasks": tasks}
