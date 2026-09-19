"""WTForms mirror of the company's paper "Corporate Calendar Event
Submission Form" (Sections 2-5) — see app/models/corporate_event.py for the
field-by-field mapping and why dates are date-only.
"""
from flask_wtf import FlaskForm
from wtforms import (
    DateField, DecimalField, IntegerField, RadioField, SelectField, SelectMultipleField, StringField, TextAreaField, widgets,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from app.models.corporate_event import (
    ATTACHMENT_TYPE_CHOICES,
    CONFLICT_STATUS_LABELS,
    EVENT_TYPE_LABELS,
    MD_INVOLVEMENT_LABELS,
    PRIORITY_LABELS,
    ConflictStatus,
    EventType,
    MDInvolvement,
    PriorityLevel,
)

_YES_NO = [("yes", "Yes"), ("no", "No")]


class CorporateEventForm(FlaskForm):
    # ---- Section 2: Event Details ----
    title = StringField(
        "Event Title", validators=[DataRequired(), Length(max=300)],
        render_kw={"placeholder": 'Be specific — e.g. "AIOS 2026 Conference Participation, Mumbai"'},
    )
    event_type = SelectField(
        "Event Type", choices=[(t.value, EVENT_TYPE_LABELS[t]) for t in EventType], validators=[DataRequired()],
    )
    event_type_other = StringField("Other (specify)", validators=[Optional(), Length(max=200)])
    start_date = DateField("Event Start Date", validators=[DataRequired()])
    end_date = DateField("Event End Date", validators=[DataRequired()])
    location = StringField("Venue / Location", validators=[DataRequired(), Length(max=300)])
    organizer = StringField("Event Organizer", validators=[Optional(), Length(max=200)])
    description = TextAreaField(
        "Brief Description", validators=[DataRequired()],
        render_kw={"placeholder": "What is this event and why is it happening? (2–3 lines)"},
    )

    # ---- Section 3: Priority & Impact Assessment ----
    priority_level = RadioField(
        "Priority Level", choices=[(p.value, PRIORITY_LABELS[p]) for p in PriorityLevel], validators=[DataRequired()],
    )
    md_presence_required = RadioField(
        "MD's presence or approval required?",
        choices=[(m.value, MD_INVOLVEMENT_LABELS[m]) for m in MDInvolvement], validators=[DataRequired()],
    )
    involves_external_stakeholder = RadioField(
        "Involves external stakeholder / regulatory body?", choices=_YES_NO, validators=[DataRequired()],
    )
    has_critical_impact = RadioField(
        "Financial / reputational / compliance impact if missed?", choices=_YES_NO, validators=[DataRequired()],
    )
    key_participants = TextAreaField("Key participants from our organization", validators=[Optional()])
    estimated_attendees = IntegerField("Estimated no. of attendees", validators=[Optional(), NumberRange(min=0)])

    # ---- Section 4: Preparation & Action Required ----
    pre_event_actions = TextAreaField(
        "Pre-event actions required", validators=[Optional()],
        render_kw={"placeholder": "e.g. Travel booking, visa, agenda preparation, guest arrangement"},
    )
    documents_to_prepare = TextAreaField("Documents / Reports to be prepared", validators=[Optional()])
    budget_involved = RadioField("Budget involved?", choices=_YES_NO, default="no", validators=[Optional()])
    budget_amount = DecimalField("Approx. Budget (₹)", validators=[Optional(), NumberRange(min=0)], places=2)
    preparation_deadline = DateField("Deadline for preparation", validators=[Optional()])
    conflicts_with_events = RadioField(
        "Conflicts with known company events?",
        choices=[(c.value, CONFLICT_STATUS_LABELS[c]) for c in ConflictStatus], default=ConflictStatus.NOT_SURE.value,
        validators=[Optional()],
    )
    conflict_notes = TextAreaField("If yes, mention the conflicting event(s)", validators=[Optional()])

    # ---- Section 5: Attachments ----
    attachment_types = SelectMultipleField(
        "Please tick all that apply", choices=ATTACHMENT_TYPE_CHOICES, validators=[Optional()],
        widget=widgets.ListWidget(prefix_label=False), option_widget=widgets.CheckboxInput(),
    )
    attachment_notes = TextAreaField("Notes on attachments", validators=[Optional()])
