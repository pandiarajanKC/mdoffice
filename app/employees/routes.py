from flask import current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.employees import employees_bp
from app.employees.forms import ExternalContactForm
from app.extensions import db
from app.models.app_user import AppUser
from app.models.external_contact import ExternalContact
from app.services import auth_service
from app.utils.rbac import roles_required

EMPLOYEES_MAX_ROWS = 5000  # safety cap, comfortably above the ~2000-employee roster; DataTable handles sort/search/paging client-side


@employees_bp.route("/")
@login_required
@roles_required("EA")
def list_view():
    """Directory over the HR Employee Master, plus this app's own roster of
    external contacts (consultants, trustees, external agents — see
    app/models/external_contact.py) who aren't in HR at all.
    """
    q = (request.args.get("q") or "").strip()
    department = request.args.get("department", "")

    repo = current_app.extensions["employee_repository"]
    if q:
        employees = repo.search(q, limit=EMPLOYEES_MAX_ROWS)
    elif department:
        employees = repo.list_by_department(department)
    else:
        employees = repo.list_all(limit=EMPLOYEES_MAX_ROWS)

    manager_ids = [e.manager_id for e in employees if e.manager_id]
    managers = repo.get_by_ids(manager_ids) if manager_ids else {}

    # Which of the rows shown already have a login account — that's the
    # only case "Reset Password" makes sense for; anyone who's never
    # logged in just signs in directly with their code and the default
    # temporary password instead.
    codes = [e.employee_code for e in employees]
    accounts = {u.employee_code: u for u in AppUser.query.filter(AppUser.employee_code.in_(codes)).all()} if codes else {}

    return render_template(
        "employees/list.html",
        employees=employees,
        managers=managers,
        accounts=accounts,
        departments=repo.list_departments(),
        q=q,
        department=department,
        total=len(employees),
        external_form=ExternalContactForm(),
    )


@employees_bp.route("/external", methods=["POST"])
@login_required
@roles_required("EA")
def create_external():
    form = ExternalContactForm()
    if not form.validate_on_submit():
        for field, errors in form.errors.items():
            for err in errors:
                flash(f"{field}: {err}", "danger")
        return redirect(url_for("employees.list_view"))

    contact = ExternalContact(
        name=form.name.data.strip(),
        organization=(form.organization.data or "").strip() or None,
        role_title=(form.role_title.data or "").strip() or None,
        email=(form.email.data or "").strip() or None,
        created_by=current_user.username,
    )
    db.session.add(contact)
    db.session.commit()  # commit first so contact.id (and so .code) exists

    flash(
        f'Added "{contact.name}" — they can sign in with code {contact.code} and the temporary '
        f'password "Pass" to set their own password, same as any employee.',
        "success",
    )
    return redirect(url_for("employees.list_view"))


@employees_bp.route("/<code>/reset-password", methods=["POST"])
@login_required
@roles_required("EA")
def reset_password(code):
    user = AppUser.query.filter_by(employee_code=code).first()
    if user is None:
        flash("This person hasn't logged in yet — they can sign in directly with their code and the default password.", "info")
        return redirect(url_for("employees.list_view"))

    temp_password = auth_service.force_password_reset(user, performed_by=current_user.username)
    flash(
        f'Password reset for {user.display_name or user.username}. Share the temporary password '
        f'"{temp_password}" with them — they\'ll be asked to set their own on next sign-in.',
        "success",
    )
    return redirect(url_for("employees.list_view"))


@employees_bp.route("/search")
@login_required
@roles_required("MD", "EA")
def search():
    query = (request.args.get("q") or "").strip()
    if len(query) < 2:
        return jsonify([])

    repo = current_app.extensions["employee_repository"]
    results = repo.search(query, limit=20)
    return jsonify(
        [
            {
                "code": r.employee_code,
                "name": r.name,
                "department": r.department,
                "designation": r.designation,
                "label": f"{r.name} ({r.employee_code}) — {r.department or 'N/A'}",
            }
            for r in results
        ]
    )
