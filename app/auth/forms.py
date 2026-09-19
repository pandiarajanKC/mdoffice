from flask_wtf import FlaskForm
from wtforms import DateField, PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Length


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(max=64)])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Sign in")


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField("Current password", validators=[DataRequired()])
    new_password = PasswordField("New password", validators=[DataRequired(), Length(min=8)])
    confirm_password = PasswordField("Confirm new password", validators=[DataRequired()])
    submit = SubmitField("Update password")


class ForgotPasswordForm(FlaskForm):
    username = StringField("Employee Code / Username", validators=[DataRequired(), Length(max=64)])
    date_of_birth = DateField("Date of Birth (as on record with HR)", validators=[DataRequired()])
    submit = SubmitField("Verify my identity")


class ResetPasswordForm(FlaskForm):
    new_password = PasswordField("New password", validators=[DataRequired(), Length(min=8)])
    confirm_password = PasswordField("Confirm new password", validators=[DataRequired()])
    submit = SubmitField("Set new password")
