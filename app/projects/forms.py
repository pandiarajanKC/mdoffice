from flask_wtf import FlaskForm
from wtforms import DateField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional

from app.models.task import TaskPriority

PRIORITY_CHOICES = [(p.value, p.value.title()) for p in TaskPriority]


class ProjectForm(FlaskForm):
    project_code = StringField("Project Code", validators=[Optional(), Length(max=30)])
    name = StringField("Project Name", validators=[DataRequired(), Length(max=300)])
    description = TextAreaField("Description", validators=[Optional()])
    start_date = DateField("Start Date", validators=[Optional()])
    target_date = DateField("Target Date", validators=[Optional()])
    priority = SelectField("Priority", choices=PRIORITY_CHOICES, default=TaskPriority.MEDIUM.value)
    # owner_employee_code comes from a Tom Select single-select and is read
    # directly via request.form (see ActionForm's note on employee_codes).


class ProjectTaskForm(FlaskForm):
    title = StringField("Task", validators=[DataRequired(), Length(max=500)])
    description = TextAreaField("Details", validators=[Optional()])
    start_date = DateField("Start Date", validators=[Optional()])
    due_date = DateField("Due Date", validators=[Optional()])
    priority = SelectField("Priority", choices=PRIORITY_CHOICES, default=TaskPriority.MEDIUM.value)


class MemberForm(FlaskForm):
    role = StringField("Role", validators=[Optional(), Length(max=100)], default="Member")


class TagMeetingForm(FlaskForm):
    meeting_id = SelectField("Meeting", coerce=int, validators=[DataRequired()])
