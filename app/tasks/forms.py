from flask_wtf import FlaskForm
from wtforms import DateField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional

from app.models.task import TaskPriority


class TaskForm(FlaskForm):
    title = StringField("Task", validators=[DataRequired(), Length(max=500)])
    description = TextAreaField("Details", validators=[Optional()])
    due_date = DateField("Due Date", validators=[Optional()])
    priority = SelectField(
        "Priority",
        choices=[(p.value, p.value.title()) for p in TaskPriority],
        default=TaskPriority.MEDIUM.value,
    )
    # Assignee employee codes come from a Tom Select multi-select and are
    # read directly via request.form.getlist("employee_codes") in the route
    # — see ActionForm's note in app/meetings/forms.py for why.
