"""Corporate Calendar: one nominated employee per division posts events
their division wants EA/MD to see — separate from the Meeting-based Google
Calendar sync in app/calendar/, which is about the MD's own meetings.

The field set below is a direct match to the company's paper/PDF
"Corporate Calendar Event Submission Form" (Sections 2-5), so the online
form needs no separate training for anyone who's already used the paper
one. Dates are date-only throughout (no time-of-day) because the source
form has none — these are day-granularity events (conferences, audits,
board meetings, deadlines), not scheduled meetings with a start/end clock
time (that's what app/meetings/ is for).

No division/color column is stored on this table. Exactly like AppUser and
Meeting elsewhere in this app, HR Employee Master data (Department, name,
...) is never copied in — see EmployeeRepository's module docstring. An
event's division is always resolved live from its creator's Department via
EmployeeRepository, and its display color is derived deterministically from
that division name (division_color_hex below) so the same division always
renders the same color with no admin setup step required.
"""
from __future__ import annotations

import enum
import zlib

from app.extensions import db
from app.models.mixins import TimestampMixin

# The same eight-hue palette the Tag Master uses (see app/models/tag.py's
# TAG_COLORS) so a division's calendar color always matches an existing
# .tag-<color> soft-background CSS rule in app/static/css/app.css.
DIVISION_COLOR_HEXES = {
    "violet": "#6D5EF8",
    "pink": "#FF6B9D",
    "teal": "#14B8A6",
    "orange": "#FF9F43",
    "sky": "#3AB7FF",
    "success": "#16B981",
    "danger": "#F4416C",
    "warning": "#FFAB1E",
}
_DIVISION_COLOR_NAMES = list(DIVISION_COLOR_HEXES)


def division_color_name(division: str | None) -> str:
    """Deterministic color name (a key of DIVISION_COLOR_HEXES) for a
    division/department label. Uses crc32 rather than Python's built-in
    hash() because hash() of a string is randomized per-process
    (PYTHONHASHSEED) — this needs to land on the same color every time,
    across requests and server restarts, for the same division name.
    """
    label = (division or "Unassigned").strip() or "Unassigned"
    index = zlib.crc32(label.encode("utf-8")) % len(_DIVISION_COLOR_NAMES)
    return _DIVISION_COLOR_NAMES[index]


def division_color_hex(division: str | None) -> str:
    return DIVISION_COLOR_HEXES[division_color_name(division)]


# ---------------------------------------------------------------- Section 2: Event Details ----

class EventType(str, enum.Enum):
    CONFERENCE_TRADE_SHOW = "CONFERENCE_TRADE_SHOW"
    BOARD_MANAGEMENT_MEETING = "BOARD_MANAGEMENT_MEETING"
    REGULATORY_AUDIT = "REGULATORY_AUDIT"
    VIP_GUEST_VISIT = "VIP_GUEST_VISIT"
    TRAINING = "TRAINING"
    PRODUCT_LAUNCH = "PRODUCT_LAUNCH"
    STATUTORY_DEADLINE = "STATUTORY_DEADLINE"
    OTHER = "OTHER"


EVENT_TYPE_LABELS = {
    EventType.CONFERENCE_TRADE_SHOW: "Conference / Trade Show",
    EventType.BOARD_MANAGEMENT_MEETING: "Board / Management Meeting",
    EventType.REGULATORY_AUDIT: "Regulatory / Audit",
    EventType.VIP_GUEST_VISIT: "VIP / Guest Visit",
    EventType.TRAINING: "Training",
    EventType.PRODUCT_LAUNCH: "Product Launch",
    EventType.STATUTORY_DEADLINE: "Statutory Deadline",
    EventType.OTHER: "Other",
}


# ------------------------------------------------------- Section 3: Priority & Impact ----

class PriorityLevel(str, enum.Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    STANDARD = "STANDARD"


PRIORITY_LABELS = {
    PriorityLevel.CRITICAL: "Critical — MD must be aware or present",
    PriorityLevel.HIGH: "High — Key milestone or external stakeholder",
    PriorityLevel.STANDARD: "Standard — Scheduled internal activity",
}

# Badge color (a Tag Master color name — see DIVISION_COLOR_HEXES) for each
# priority, used on the EA/MD aggregate view so urgency reads at a glance.
PRIORITY_BADGE_COLOR = {
    PriorityLevel.CRITICAL: "danger",
    PriorityLevel.HIGH: "warning",
    PriorityLevel.STANDARD: "sky",
}


class MDInvolvement(str, enum.Enum):
    YES = "YES"
    NO = "NO"
    AWARENESS_ONLY = "AWARENESS_ONLY"


MD_INVOLVEMENT_LABELS = {
    MDInvolvement.YES: "Yes",
    MDInvolvement.NO: "No",
    MDInvolvement.AWARENESS_ONLY: "Awareness only",
}


# --------------------------------------------------- Section 4: Preparation & Action ----

class ConflictStatus(str, enum.Enum):
    YES = "YES"
    NO = "NO"
    NOT_SURE = "NOT_SURE"


CONFLICT_STATUS_LABELS = {
    ConflictStatus.YES: "Yes",
    ConflictStatus.NO: "No",
    ConflictStatus.NOT_SURE: "Not sure",
}


# ------------------------------------------------------------- Section 5: Attachments ----

# Stored on the row as a comma-separated list of these codes (see
# CorporateEvent.attachment_type_list) — same convention as Meeting's
# comma-separated attendees_text, rather than a separate join table for
# what's just a fixed checklist.
ATTACHMENT_TYPE_CHOICES = [
    ("BROCHURE_INVITE", "Conference / event brochure or invite"),
    ("REGISTRATION_CONFIRMATION", "Registration confirmation"),
    ("GUEST_COMMUNICATION", "Guest communication"),
    ("TRAVEL_PLAN", "Travel plan"),
    ("AGENDA_PROGRAMME", "Agenda / programme"),
    ("OTHER_DOCUMENTS", "Other documents"),
]
ATTACHMENT_TYPE_LABELS = dict(ATTACHMENT_TYPE_CHOICES)


class CorporateEvent(TimestampMixin, db.Model):
    """One event on the Corporate Calendar, owned by the employee who
    created it. `created_by` is that employee's login username, which for
    an EMPLOYEE-role account is always their employee_code (see
    auth_service.provision_and_login_employee) — so it doubles as the key
    for both "whose event is this" (an employee only ever sees their own)
    and "which division does it belong to" (resolved via
    EmployeeRepository.get_by_code(created_by).department), with no
    separate column needed for either.
    """
    __tablename__ = "md_corporate_event"

    id = db.Column(db.Integer, primary_key=True)

    # ---- Section 2: Event Details ----
    title = db.Column(db.String(300), nullable=False)
    event_type = db.Column(db.Enum(EventType, name="md_corp_event_type"), nullable=False, default=EventType.OTHER)
    event_type_other = db.Column(db.String(200), nullable=True)
    start_date = db.Column(db.Date, nullable=False, index=True)
    end_date = db.Column(db.Date, nullable=False)
    location = db.Column(db.String(300), nullable=True)
    organizer = db.Column(db.String(200), nullable=True)
    description = db.Column(db.Text, nullable=True)

    # ---- Section 3: Priority & Impact Assessment ----
    priority_level = db.Column(db.Enum(PriorityLevel, name="md_corp_priority_level"), nullable=False, default=PriorityLevel.STANDARD)
    md_presence_required = db.Column(db.Enum(MDInvolvement, name="md_corp_md_involvement"), nullable=False, default=MDInvolvement.NO)
    involves_external_stakeholder = db.Column(db.Boolean, nullable=False, default=False)
    has_critical_impact = db.Column(db.Boolean, nullable=False, default=False)
    key_participants = db.Column(db.Text, nullable=True)
    estimated_attendees = db.Column(db.Integer, nullable=True)

    # ---- Section 4: Preparation & Action Required ----
    pre_event_actions = db.Column(db.Text, nullable=True)
    documents_to_prepare = db.Column(db.Text, nullable=True)
    budget_involved = db.Column(db.Boolean, nullable=False, default=False)
    budget_amount = db.Column(db.Numeric(12, 2), nullable=True)
    preparation_deadline = db.Column(db.Date, nullable=True)
    conflicts_with_events = db.Column(db.Enum(ConflictStatus, name="md_corp_conflict_status"), nullable=True)
    conflict_notes = db.Column(db.Text, nullable=True)

    # ---- Section 5: Attachments ----
    attachment_types = db.Column(db.Text, nullable=True)
    attachment_notes = db.Column(db.Text, nullable=True)

    created_by = db.Column(db.String(64), nullable=False, index=True)

    @property
    def attachment_type_list(self) -> list[str]:
        return [t for t in (self.attachment_types or "").split(",") if t]

    @property
    def event_type_label(self) -> str:
        if self.event_type == EventType.OTHER and self.event_type_other:
            return self.event_type_other
        return EVENT_TYPE_LABELS[self.event_type]

    @property
    def is_md_eligible(self) -> bool:
        """Mirrors the source form's "Quick Reference — MD Calendar
        Eligibility Filter": qualifies if any one of the three Section 3
        questions is affirmative. Informational only (surfaced as a badge
        on the EA/MD aggregate view, see corporate_calendar_service) —
        never hides an event, since this app doesn't have a separate
        departmental-calendar tier to gate a "no" down to yet.
        """
        return (
            self.md_presence_required in (MDInvolvement.YES, MDInvolvement.AWARENESS_ONLY)
            or self.involves_external_stakeholder
            or self.has_critical_impact
        )

    def __repr__(self) -> str:
        return f"<CorporateEvent {self.id} {self.title!r}>"
