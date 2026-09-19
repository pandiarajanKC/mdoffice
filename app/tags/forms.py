from flask_wtf import FlaskForm
from wtforms import SelectField, StringField
from wtforms.validators import DataRequired, Length, Optional

from app.models.tag import TAG_COLORS


class TagCreateForm(FlaskForm):
    """Tag Master page: EA creates a new label."""
    name = StringField(
        "Tag name", validators=[DataRequired(), Length(max=80)],
        render_kw={"placeholder": "e.g. Board Priority, Q4 Budget, Urgent"},
    )
    color = SelectField("Color", choices=[(c, c.title()) for c in TAG_COLORS], validators=[Optional()])
