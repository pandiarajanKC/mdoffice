"""CSS badge-class lookups for status/priority enums, shared by every template
so a status's color coding is defined in exactly one place.
"""
MEETING_STATUS_BADGE = {
    "SCHEDULED": "open",
    "IN_PROGRESS": "inprogress",
    "COMPLETED": "completed",
    "ON_HOLD": "hold",
    "CANCELLED": "cancelled",
}

TASK_STATUS_BADGE = {
    "OPEN": "open",
    "IN_PROGRESS": "inprogress",
    "COMPLETED": "completed",
    "ON_HOLD": "hold",
    "CANCELLED": "cancelled",
}

PRIORITY_BADGE = {
    "LOW": "open",
    "MEDIUM": "inprogress",
    "HIGH": "hold",
    "CRITICAL": "critical",
}


def badge_class(status_value: str, mapping: dict) -> str:
    return mapping.get(status_value, "open")
