import time

from flask import current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.auth import auth_bp
from app.auth.forms import ChangePasswordForm, ForgotPasswordForm, LoginForm, ResetPasswordForm
from app.extensions import db
from app.models.app_user import AppUser
from app.services import auth_service

# How long a verified identity stays good for actually setting the new
# password, so the "verify" and "set new password" pages can be two
# separate requests without a signed token or a new DB table.
PASSWORD_RESET_WINDOW_SECONDS = 600


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    form = LoginForm()
    if form.validate_on_submit():
        employee_repository = current_app.extensions["employee_repository"]
        try:
            user = auth_service.authenticate(
                form.username.data,
                form.password.data,
                employee_repository,
                max_attempts=current_app.config["LOGIN_MAX_ATTEMPTS"],
                lockout_minutes=current_app.config["LOGIN_LOCKOUT_MINUTES"],
                ip_address=request.remote_addr,
            )
        except auth_service.AuthenticationError as exc:
            flash(str(exc), "danger")
            return render_template("auth/login.html", form=form)

        login_user(user)
        if user.must_change_password:
            flash("For security, please set a new password before continuing.", "info")
            return redirect(url_for("auth.change_password"))
        return redirect(request.args.get("next") or url_for("dashboard.index"))

    return render_template("auth/login.html", form=form)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        try:
            auth_service.change_password(
                current_user,
                form.current_password.data,
                form.new_password.data,
                form.confirm_password.data,
            )
        except auth_service.PasswordPolicyError as exc:
            flash(str(exc), "danger")
            return render_template("auth/change_password.html", form=form)

        flash("Your password has been updated.", "success")
        return redirect(url_for("dashboard.index"))

    return render_template("auth/change_password.html", form=form)


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    form = ForgotPasswordForm()
    if form.validate_on_submit():
        employee_repository = current_app.extensions["employee_repository"]
        try:
            user = auth_service.verify_identity_for_reset(
                form.username.data,
                form.date_of_birth.data,
                employee_repository,
                max_attempts=current_app.config["LOGIN_MAX_ATTEMPTS"],
                lockout_minutes=current_app.config["LOGIN_LOCKOUT_MINUTES"],
                ip_address=request.remote_addr,
            )
        except auth_service.AuthenticationError as exc:
            flash(str(exc), "danger")
            return render_template("auth/forgot_password.html", form=form)

        session["pw_reset_user_id"] = user.id
        session["pw_reset_expires"] = time.time() + PASSWORD_RESET_WINDOW_SECONDS
        return redirect(url_for("auth.reset_password"))

    return render_template("auth/forgot_password.html", form=form)


@auth_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    user_id = session.get("pw_reset_user_id")
    expires = session.get("pw_reset_expires")
    if not user_id or not expires or time.time() > expires:
        session.pop("pw_reset_user_id", None)
        session.pop("pw_reset_expires", None)
        flash("That identity check has expired. Please verify your identity again.", "danger")
        return redirect(url_for("auth.forgot_password"))

    user = db.session.get(AppUser, user_id)
    if user is None:
        session.pop("pw_reset_user_id", None)
        session.pop("pw_reset_expires", None)
        return redirect(url_for("auth.forgot_password"))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        try:
            auth_service.reset_password_after_verification(user, form.new_password.data, form.confirm_password.data)
        except auth_service.PasswordPolicyError as exc:
            flash(str(exc), "danger")
            return render_template("auth/reset_password.html", form=form)

        session.pop("pw_reset_user_id", None)
        session.pop("pw_reset_expires", None)
        flash("Your password has been reset. Please sign in with your new password.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", form=form)
