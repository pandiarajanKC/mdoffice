"""Unified Task / TaskAssignee model (master spec section 44: "Unified Task
Architecture — Preferred"). Meeting action items, project tasks and general
follow-ups are all a ``Task`` distinguished by ``task_type`` — this avoids
duplicating status/assignment/overdue/reporting logic per module.
"""
from __future__ import annotations

import enum
from datetime import date

from app.extensions import db
from app.models.mixins import TimestampMixin, utcnow


class TaskType(str, enum.Enum):
    MEETING_ACTION = "MEETING_ACTION"
    PROJECT_TASK = "PROJECT_TASK"
    GENERAL_FOLLOWUP = "GENERAL_FOLLOWUP"


class TaskStatus(str, enum.Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    ON_HOLD = "ON_HOLD"
    CANCELLED = "CANCELLED"


class TaskPriority(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


OPEN_STATUSES = (TaskStatus.OPEN, TaskStatus.IN_PROGRESS, TaskStatus.ON_HOLD)


class Task(TimestampMixin, db.Model):
    __tablename__ = "md_task"

    id = db.Column(db.Integer, primary_key=True)
    task_type = db.Column(db.Enum(TaskType, name="md_task_type"), nullable=False, index=True)

    title = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text, nullable=True)

    meeting_id = db.Column(db.Integer, db.ForeignKey("md_meeting.id"), nullable=True, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey("md_project.id"), nullable=True, index=True)

    priority = db.Column(db.Enum(TaskPriority, name="md_task_priority"), nullable=False, default=TaskPriority.MEDIUM)
    status = db.Column(db.Enum(TaskStatus, name="md_task_status"), nullable=False, default=TaskStatus.OPEN, index=True)

    start_date = db.Column(db.Date, nullable=True)
    due_date = db.Column(db.Date, nullable=True, index=True)
    progress_percent = db.Column(db.Integer, nullable=False, default=0)

    created_by = db.Column(db.String(64), nullable=False)
    updated_by = db.Column(db.String(64), nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)

    meeting = db.relationship("Meeting", backref="tasks")
    project = db.relationship("Project", backref="tasks")
    assignees = db.relationship("TaskAssignee", backref="task", cascade="all, delete-orphan")

    @property
    def is_overdue(self) -> bool:
        if self.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
            return False
        return bool(self.due_date and self.due_date < date.today())

    def __repr__(self) -> str:
        return f"<Task {self.id} {self.title!r} {self.status.value}>"


class TaskAssignee(db.Model):
    __tablename__ = "md_task_assignee"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("md_task.id"), nullable=False, index=True)
    employee_code = db.Column(db.String(50), nullable=False, index=True)
    assigned_by = db.Column(db.String(64), nullable=False)
    assigned_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    __table_args__ = (db.UniqueConstraint("task_id", "employee_code", name="uq_task_assignee"),)
