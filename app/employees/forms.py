from flask_wtf import FlaskForm
from wtforms import StringField
from wtforms.validators import DataRequired, Email, Length, Optional


class ExternalContactForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=200)])
    organization = StringField("Organization", validators=[Optional(), Length(max=200)], render_kw={"placeholder": "e.g. Auswegin Info Controls"})
    role_title = StringField(
        "Role / Relationship", validators=[Optional(), Length(max=100)],
        render_kw={"placeholder": "e.g. Consultant, Trustee, External Agent"},
    )
    email = StringField("Email", validators=[Optional(), Email(), Length(max=255)])
