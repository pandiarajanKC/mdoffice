from flask_wtf import FlaskForm
from wtforms import DateField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional

from app.models.meeting import ConfidentialityLevel
from app.models.task import TaskPriority


class MeetingForm(FlaskForm):
    title = StringField("Title", validators=[DataRequired(), Length(max=300)])
    description = TextAreaField("Description", validators=[Optional()])
    meeting_date = DateField("Date", validators=[DataRequired()])
    start_time = StringField("Start Time", validators=[DataRequired()], render_kw={"placeholder": "Select time", "autocomplete": "off"})
    end_time = StringField("End Time", validators=[DataRequired()], render_kw={"placeholder": "Select time", "autocomplete": "off"})
    location = StringField("Location", validators=[Optional(), Length(max=300)])
    meeting_link = StringField("Meeting Link", validators=[Optional(), Length(max=500)])
    organizer = StringField("Organizer", validators=[Optional(), Length(max=200)])
    meeting_type = StringField("Meeting Type", validators=[Optional(), Length(max=100)])
    attendees_emails = StringField(
        "Attendee Emails", validators=[Optional(), Length(max=2000)],
        render_kw={"placeholder": "comma-separated, e.g. alice@company.com, bob@company.com"},
    )
    confidentiality_level = SelectField(
        "Confidentiality",
        choices=[(c.value, c.value.title()) for c in ConfidentialityLevel],
        default=ConfidentialityLevel.NORMAL.value,
    )


class NoteForm(FlaskForm):
    note_text = StringField("Note", validators=[DataRequired(), Length(max=2000)])


class DecisionForm(FlaskForm):
    decision_text = TextAreaField("Decision", validators=[DataRequired(), Length(max=2000)])


class ActionForm(FlaskForm):
    title = StringField("Action", validators=[DataRequired(), Length(max=500)])
    description = TextAreaField("Details", validators=[Optional()])
    due_date = DateField("Due Date", validators=[Optional()])
    priority = SelectField(
        "Priority",
        choices=[(p.value, p.value.title()) for p in TaskPriority],
        default=TaskPriority.MEDIUM.value,
    )
    # Assignee employee codes are submitted as a native multi-select
    # (enhanced client-side by Tom Select) and read directly via
    # request.form.getlist("employee_codes") in the route — WTForms'
    # SelectMultipleField would reject codes outside a fixed choice list,
    # which doesn't fit a searchable-against-2000-employees picker.
