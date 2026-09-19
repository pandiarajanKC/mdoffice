"""Presentation helpers for how a Task's origin is shown to different viewers.

Employees must not see the identity of a Restricted/Confidential meeting
just because they have one action item from it (master spec section 41) —
MD and EA always see the real title since they manage those meetings.
"""
from app.models.meeting import ConfidentialityLevel
from app.models.task import Task


def task_source_label(task: Task, *, viewer_is_employee: bool) -> str:
    if task.meeting_id:
        meeting = task.meeting
        if viewer_is_employee and meeting.confidentiality_level != ConfidentialityLevel.NORMAL:
            return "MD Office Meeting"
        return f"Meeting: {meeting.title}"
    if task.project_id:
        return f"Project: {task.project.name}"
    return "General Follow-up"
