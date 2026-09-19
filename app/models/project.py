"""Project and ProjectMember (master spec sections 15-16).

Project tasks reuse the unified Task model from Phase 3
(task_type=PROJECT_TASK, Task.project_id) rather than a separate table —
see app/models/task.py.
"""
from __future__ import annotations

import enum

from app.extensions import db
from app.models.mixins import TimestampMixin, utcnow
from app.models.task import TaskPriority


class ProjectStatus(str, enum.Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    ON_HOLD = "ON_HOLD"
    CANCELLED = "CANCELLED"


class Project(TimestampMixin, db.Model):
    __tablename__ = "md_project"

    id = db.Column(db.Integer, primary_key=True)
    project_code = db.Column(db.String(30), unique=True, nullable=False, index=True)
    name = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text, nullable=True)

    owner_employee_code = db.Column(db.String(50), nullable=False, index=True)

    start_date = db.Column(db.Date, nullable=True)
    target_date = db.Column(db.Date, nullable=True)

    status = db.Column(db.Enum(ProjectStatus, name="md_project_status"), nullable=False, default=ProjectStatus.OPEN, index=True)
    priority = db.Column(db.Enum(TaskPriority, name="md_task_priority"), nullable=False, default=TaskPriority.MEDIUM)
    progress_percent = db.Column(db.Integer, nullable=False, default=0)

    created_by = db.Column(db.String(64), nullable=False)
    updated_by = db.Column(db.String(64), nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)

    members = db.relationship("ProjectMember", backref="project", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Project {self.project_code} {self.name!r}>"


class ProjectMember(db.Model):
    __tablename__ = "md_project_member"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("md_project.id"), nullable=False, index=True)
    employee_code = db.Column(db.String(50), nullable=False, index=True)
    role = db.Column(db.String(100), nullable=False, default="Member")
    added_by = db.Column(db.String(64), nullable=False)
    added_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    __table_args__ = (db.UniqueConstraint("project_id", "employee_code", name="uq_project_member"),)
